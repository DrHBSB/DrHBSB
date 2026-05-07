#!/usr/bin/env python3
"""
Extract New Jersey Administrative Code (N.J.A.C.) documents from LexisNexis.

The scraper follows the guidance in Advice.md:
- use visible text and breadcrumbs for hierarchy
- capture the changing source URL on every page
- click the Next control until it disappears or becomes disabled
- write records incrementally so a long run does not lose finished pages
- pause for manual CAPTCHA/login handling when needed

Before running:
    python -m pip install playwright
    python -m playwright install chromium

Example:
    python extract_njac.py --max-pages 25
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as escape_xml_text

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


DEFAULT_LANDING_PAGE = (
    "https://advance.lexis.com/container?"
    "config=00JAA5OTY5MTdjZi1lMzYxLTQxNTEtOWFkNi0xMmU5ZTViODQ2M2MKAFBvZENhdGFsb2coFSYEAfv22IKqMT9DIHrf"
    "&crid=3f2f0aa3-f402-4b70-bcc5-939ff6217c31"
    "&prid=ae61b66e-a692-42a3-8599-4236c0739dca"
)

OUTPUT_DIR = Path("output")
LOG_DIR = Path("logs")
DEFAULT_OUTPUT_FILE = OUTPUT_DIR / "njac_extracted.xml"
DEFAULT_LOG_FILE = LOG_DIR / "extraction.log"

LEVEL_FIELDS = [
    "level10",
    "level20",
    "level30",
    "level40",
    "level50",
    "level60",
    "level70",
    "level80",
    "level90",
    "level100",
]

SECTION_SIGN = "\u00a7"
SECTION_PATTERN = re.compile(
    re.escape(SECTION_SIGN)
    + r"\s*\d+[A-Za-z]?:\d+(?:[-.]\d+[A-Za-z]?)?(?:\([^)]+\))*[^\n]*"
)
TITLE_PATTERN = re.compile(r"\bTITLE\s+\d+[A-Za-z]?\.\s*[^\n]+", re.IGNORECASE)
CHAPTER_PATTERN = re.compile(r"\bCHAPTER\s+\d+[A-Za-z]?\.\s*[^\n]+", re.IGNORECASE)
SUBCHAPTER_PATTERN = re.compile(r"\bSUBCHAPTER\s+\d+[A-Za-z]?\.\s*[^\n]+", re.IGNORECASE)
CHAPTER_NOTES_PATTERN = re.compile(r"\bChapter\s+Notes\b", re.IGNORECASE)

logger = logging.getLogger("njac_extract")


@dataclass(frozen=True)
class ScraperConfig:
    start_url: str
    output_file: Path
    log_file: Path
    max_pages: int
    headless: bool
    slow_mo_ms: int
    page_timeout_ms: int
    selector_timeout_ms: int
    navigation_delay_ms: int
    captcha_wait_seconds: int
    min_content_chars: int
    strict_validation: bool
    user_data_dir: Optional[Path]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(text: Optional[str]) -> str:
    """Normalize text while preserving useful paragraph breaks."""
    if not text:
        return ""

    cleaned_lines: List[str] = []
    previous_blank = False

    for raw_line in str(text).splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", raw_line).strip()
        if line:
            cleaned_lines.append(line)
            previous_blank = False
        elif cleaned_lines and not previous_blank:
            cleaned_lines.append("")
            previous_blank = True

    return "\n".join(cleaned_lines).strip()


def first_match(pattern: re.Pattern[str], texts: Iterable[str]) -> str:
    for text in texts:
        match = pattern.search(text)
        if match:
            return clean_text(match.group(0))
    return ""


def configure_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def xml_escape(text: Optional[str]) -> str:
    return escape_xml_text(str(text or ""), {'"': "&quot;", "'": "&apos;"})


class IncrementalXmlWriter:
    """Append XML records as pages finish, then patch the final document count."""

    def __init__(self, output_file: Path, source_url: str) -> None:
        self.output_file = output_file
        self.source_url = source_url
        self.document_count = 0
        self._handle = None

    def open(self) -> None:
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.output_file.open("w", encoding="utf-8", newline="\n")
        self._handle.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        self._handle.write("<njacDocuments>\n")
        self._handle.write("    <metadata>\n")
        self._handle.write("        <source>New Jersey Administrative Code (N.J.A.C.)</source>\n")
        self._handle.write(f"        <sourceURL>{xml_escape(self.source_url)}</sourceURL>\n")
        self._handle.write(f"        <extractionDate>{utc_now_iso()}</extractionDate>\n")
        self._handle.write("        <extractionTool>Playwright-Python</extractionTool>\n")
        self._handle.write("        <totalDocuments>COUNTING</totalDocuments>\n")
        self._handle.write("    </metadata>\n")
        self._handle.write("    <documents>\n")
        self._handle.flush()

    def write_record(self, record: Dict[str, str]) -> None:
        if self._handle is None:
            raise RuntimeError("XML writer is not open")

        self._handle.write("        <document>\n")
        self._handle.write(f"            <sourceURL>{xml_escape(record.get('source_url'))}</sourceURL>\n")

        for field in LEVEL_FIELDS:
            self._handle.write(f"            <{field}>{xml_escape(record.get(field))}</{field}>\n")

        self._handle.write(f"            <contents>{xml_escape(record.get('contents'))}</contents>\n")
        self._handle.write("        </document>\n")
        self._handle.flush()
        self.document_count += 1

    def close(self, document_count: Optional[int] = None) -> None:
        final_count = self.document_count if document_count is None else document_count

        if self._handle is not None:
            self._handle.write("    </documents>\n")
            self._handle.write("</njacDocuments>\n")
            self._handle.flush()
            self._handle.close()
            self._handle = None

        content = self.output_file.read_text(encoding="utf-8")
        content = content.replace(
            "<totalDocuments>COUNTING</totalDocuments>",
            f"<totalDocuments>{final_count}</totalDocuments>",
            1,
        )
        self.output_file.write_text(content, encoding="utf-8", newline="\n")


async def safe_inner_text(locator: Locator, timeout_ms: int = 1000) -> str:
    try:
        return clean_text(await locator.inner_text(timeout=timeout_ms))
    except Exception:
        return ""


async def visible_texts(locator: Locator, limit: int = 40, timeout_ms: int = 1000) -> List[str]:
    texts: List[str] = []

    try:
        count = min(await locator.count(), limit)
    except Exception:
        return texts

    for index in range(count):
        element = locator.nth(index)
        try:
            if await element.is_visible(timeout=timeout_ms):
                text = await safe_inner_text(element, timeout_ms=timeout_ms)
                if text:
                    texts.append(text)
        except Exception:
            continue

    return texts


async def first_visible_locator(candidates: Sequence[Locator], timeout_ms: int = 1000) -> Optional[Locator]:
    for locator in candidates:
        try:
            count = min(await locator.count(), 8)
        except Exception:
            continue

        for index in range(count):
            element = locator.nth(index)
            try:
                if not await element.is_visible(timeout=timeout_ms):
                    continue
                if not await element.is_enabled(timeout=timeout_ms):
                    continue

                aria_disabled = await element.get_attribute("aria-disabled", timeout=timeout_ms)
                disabled_attr = await element.get_attribute("disabled", timeout=timeout_ms)
                css_class = await element.get_attribute("class", timeout=timeout_ms)
                class_text = css_class or ""

                if aria_disabled == "true" or disabled_attr is not None:
                    continue
                if re.search(r"\b(disabled|inactive)\b", class_text, re.IGNORECASE):
                    continue

                return element
            except Exception:
                continue

    return None


async def page_body_text(page: Page, timeout_ms: int = 2000) -> str:
    try:
        return clean_text(await page.locator("body").inner_text(timeout=timeout_ms))
    except Exception:
        return ""


async def wait_for_page_ready(page: Page, config: ScraperConfig) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=config.page_timeout_ms)
    except PlaywrightTimeoutError:
        logger.debug("Timed out waiting for domcontentloaded; continuing")

    try:
        await page.wait_for_load_state("networkidle", timeout=config.page_timeout_ms)
    except PlaywrightTimeoutError:
        logger.debug("Timed out waiting for networkidle; continuing")

    await page.wait_for_timeout(config.navigation_delay_ms)


async def check_for_captcha(page: Page) -> bool:
    text_locators = [
        page.get_by_text(re.compile(r"captcha|verify you are human|security check", re.IGNORECASE)),
        page.locator("iframe[src*='captcha' i], iframe[title*='captcha' i]"),
        page.locator("input[name*='captcha' i], input[id*='captcha' i]"),
    ]

    for locator in text_locators:
        try:
            if await locator.first.is_visible(timeout=1000):
                return True
        except Exception:
            continue

    return False


async def pause_for_captcha_if_needed(page: Page, config: ScraperConfig) -> None:
    if not await check_for_captcha(page):
        return

    logger.warning("CAPTCHA or security check detected. Solve it in the browser window.")
    remaining = max(config.captcha_wait_seconds, 5)

    while remaining > 0:
        if not await check_for_captcha(page):
            logger.info("CAPTCHA appears to be cleared; resuming.")
            return

        sleep_for = min(5, remaining)
        await asyncio.sleep(sleep_for)
        remaining -= sleep_for

    logger.info("CAPTCHA wait finished; attempting to continue.")


async def extract_breadcrumb_data(page: Page, config: ScraperConfig) -> Tuple[str, str, str]:
    breadcrumb_selectors = [
        "nav a",
        "[aria-label*='breadcrumb' i] a",
        "[class*='breadcrumb' i] a",
        "[class*='bread-crumb' i] a",
        "a[href*='#']",
        "a[href*='container']",
        "a[href*='documentpage']",
    ]

    texts: List[str] = []
    for selector in breadcrumb_selectors:
        try:
            texts.extend(await visible_texts(page.locator(selector), timeout_ms=config.selector_timeout_ms))
        except Exception:
            continue

    body_text = await page_body_text(page, timeout_ms=config.selector_timeout_ms)
    search_space = texts + [body_text]

    level10 = first_match(TITLE_PATTERN, search_space)
    level20 = first_match(CHAPTER_PATTERN, search_space)
    level30 = first_match(SUBCHAPTER_PATTERN, search_space)

    return level10, level20, level30


async def extract_section_header(page: Page, config: ScraperConfig) -> str:
    heading_texts: List[str] = []

    heading_selectors = [
        f"h1:has-text('{SECTION_SIGN}')",
        f"h2:has-text('{SECTION_SIGN}')",
        "h1",
        "h2",
        "[role='heading']",
    ]

    for selector in heading_selectors:
        try:
            heading_texts.extend(await visible_texts(page.locator(selector), limit=10, timeout_ms=config.selector_timeout_ms))
        except Exception:
            continue

    section = first_match(SECTION_PATTERN, heading_texts)
    if section:
        return section

    for text in heading_texts:
        if CHAPTER_NOTES_PATTERN.search(text):
            return clean_text(text)

    body_text = await page_body_text(page, timeout_ms=config.selector_timeout_ms)
    section = first_match(SECTION_PATTERN, [body_text])
    if section:
        return section

    chapter_notes = first_match(CHAPTER_NOTES_PATTERN, [body_text])
    if chapter_notes:
        return "Chapter Notes"

    return heading_texts[0] if heading_texts else ""


async def extract_content_body(page: Page, config: ScraperConfig) -> str:
    content_selectors = [
        "main",
        "[role='main']",
        "article",
        "[class*='document' i]",
        "[class*='content' i]",
    ]

    for selector in content_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=config.selector_timeout_ms):
                text = await safe_inner_text(locator, timeout_ms=config.selector_timeout_ms)
                if len(text) >= config.min_content_chars:
                    return text
        except Exception:
            continue

    return await page_body_text(page, timeout_ms=config.selector_timeout_ms)


async def extract_page_data(page: Page, config: ScraperConfig) -> Dict[str, str]:
    level10, level20, level30 = await extract_breadcrumb_data(page, config)
    level40 = await extract_section_header(page, config)
    contents = await extract_content_body(page, config)

    return {
        "source_url": page.url,
        "level10": level10,
        "level20": level20,
        "level30": level30,
        "level40": level40,
        "level50": "",
        "level60": "",
        "level70": "",
        "level80": "",
        "level90": "",
        "level100": "",
        "contents": contents,
    }


def validate_record(record: Dict[str, str], config: ScraperConfig) -> bool:
    required_fields = ["source_url", "contents"]

    if config.strict_validation:
        required_fields.extend(["level10", "level20"])

    for field in required_fields:
        if not record.get(field, "").strip():
            logger.warning("Skipping page because required field is empty: %s", field)
            return False

    if len(record.get("contents", "").strip()) < config.min_content_chars:
        logger.warning("Skipping page because content is shorter than %d characters", config.min_content_chars)
        return False

    if not record.get("level10") or not record.get("level20"):
        logger.warning("Saved page with incomplete breadcrumb hierarchy: %s", record.get("source_url"))

    return True


def next_button_candidates(page: Page) -> List[Locator]:
    exact_next = re.compile(r"^\s*Next\s*$", re.IGNORECASE)
    contains_next = re.compile(r"\bNext\b", re.IGNORECASE)

    return [
        page.get_by_role("link", name=exact_next),
        page.get_by_role("button", name=exact_next),
        page.get_by_label(contains_next),
        page.locator("a[aria-label*='Next' i], button[aria-label*='Next' i]"),
        page.locator("a:has-text('Next'), button:has-text('Next')"),
        page.locator("[title*='Next' i]"),
    ]


async def navigate_to_next_page(page: Page, config: ScraperConfig) -> bool:
    for attempt in range(1, 4):
        next_button = await first_visible_locator(next_button_candidates(page), timeout_ms=config.selector_timeout_ms)
        if next_button is None:
            logger.info("Next control is not available. Extraction is complete.")
            return False

        old_url = page.url

        try:
            await next_button.click(timeout=config.page_timeout_ms)
            await wait_for_page_ready(page, config)
            await pause_for_captcha_if_needed(page, config)

            logger.info("Moved to next page: %s", page.url)
            if page.url == old_url:
                logger.debug("URL did not change after Next click; continuing after content wait")
            return True
        except Exception as exc:
            logger.warning("Next navigation attempt %d failed: %s", attempt, exc)
            await asyncio.sleep(2)

    logger.error("Failed to navigate after 3 attempts.")
    return False


async def navigate_to_first_content(page: Page, config: ScraperConfig) -> None:
    body_text = await page_body_text(page, timeout_ms=config.selector_timeout_ms)
    if SECTION_PATTERN.search(body_text) or CHAPTER_NOTES_PATTERN.search(body_text):
        logger.info("Landing page already appears to contain document content.")
        return

    candidates = [
        page.get_by_role("link", name=re.compile(r"Chapter\s+Notes", re.IGNORECASE)),
        page.get_by_text(re.compile(r"Title\s+\d+,\s*Chapter\s+\d+\s+--\s+Chapter\s+Notes", re.IGNORECASE)),
        page.locator("a:has-text('Chapter Notes')"),
    ]

    first_content_link = await first_visible_locator(candidates, timeout_ms=config.selector_timeout_ms)
    if first_content_link is None:
        logger.warning("Could not find a Chapter Notes link; starting extraction from the current page.")
        return

    logger.info("Opening the first Chapter Notes page.")
    await first_content_link.click(timeout=config.page_timeout_ms)
    await wait_for_page_ready(page, config)
    await pause_for_captcha_if_needed(page, config)


async def run_extraction(page: Page, config: ScraperConfig, writer: IncrementalXmlWriter) -> int:
    document_count = 0
    seen_urls = set()

    for page_number in range(1, config.max_pages + 1):
        logger.info("Processing page %d of %d: %s", page_number, config.max_pages, page.url)

        if page.url in seen_urls:
            logger.error("Stopping because the scraper returned to an already-seen URL: %s", page.url)
            break
        seen_urls.add(page.url)

        await pause_for_captcha_if_needed(page, config)
        record = await extract_page_data(page, config)

        if validate_record(record, config):
            writer.write_record(record)
            document_count = writer.document_count
            logger.info("Saved document %d", writer.document_count)
        else:
            logger.warning("Page %d did not pass validation.", page_number)

        if not await navigate_to_next_page(page, config):
            break

    return document_count


async def create_context(config: ScraperConfig) -> Tuple[BrowserContext, Optional[Browser]]:
    playwright = await async_playwright().start()

    if config.user_data_dir is not None:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.user_data_dir),
            headless=config.headless,
            slow_mo=config.slow_mo_ms,
        )
        setattr(context, "_njac_playwright", playwright)
        return context, None

    browser = await playwright.chromium.launch(headless=config.headless, slow_mo=config.slow_mo_ms)
    context = await browser.new_context()
    setattr(context, "_njac_playwright", playwright)
    return context, browser


async def close_context(context: BrowserContext, browser: Optional[Browser]) -> None:
    playwright = getattr(context, "_njac_playwright", None)

    await context.close()
    if browser is not None:
        await browser.close()
    if playwright is not None:
        await playwright.stop()


async def main_async(config: ScraperConfig) -> int:
    writer = IncrementalXmlWriter(config.output_file, config.start_url)
    writer.open()

    context: Optional[BrowserContext] = None
    browser: Optional[Browser] = None

    try:
        context, browser = await create_context(config)
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(config.selector_timeout_ms)
        page.set_default_navigation_timeout(config.page_timeout_ms)

        logger.info("Opening landing page: %s", config.start_url)
        await page.goto(config.start_url, wait_until="domcontentloaded", timeout=config.page_timeout_ms)
        await wait_for_page_ready(page, config)
        await pause_for_captcha_if_needed(page, config)
        await navigate_to_first_content(page, config)

        await run_extraction(page, config, writer)
        return writer.document_count
    finally:
        writer.close(writer.document_count)
        if context is not None:
            await close_context(context, browser)


def parse_args(argv: Optional[Sequence[str]] = None) -> ScraperConfig:
    parser = argparse.ArgumentParser(description="Extract N.J.A.C. documents from LexisNexis with Playwright.")
    parser.add_argument("--start-url", default=DEFAULT_LANDING_PAGE, help="LexisNexis landing or document URL.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE, help="XML output path.")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="Log file path.")
    parser.add_argument("--max-pages", type=int, default=10000, help="Safety limit for pages to process.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium headlessly.")
    parser.add_argument("--slow-mo-ms", type=int, default=100, help="Slow Playwright actions by this many milliseconds.")
    parser.add_argument("--page-timeout-ms", type=int, default=30000, help="Navigation/load timeout in milliseconds.")
    parser.add_argument("--selector-timeout-ms", type=int, default=5000, help="Selector/text timeout in milliseconds.")
    parser.add_argument("--navigation-delay-ms", type=int, default=1000, help="Delay after page transitions.")
    parser.add_argument("--captcha-wait-seconds", type=int, default=120, help="Manual CAPTCHA wait window.")
    parser.add_argument("--min-content-chars", type=int, default=50, help="Minimum body text required for a saved record.")
    parser.add_argument("--strict-validation", action="store_true", help="Require title and chapter before saving a record.")
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=None,
        help="Optional Chromium profile directory for persistent LexisNexis login/cookies.",
    )
    parser.add_argument("--verbose", action="store_true", help="Write debug logs.")

    args = parser.parse_args(argv)
    configure_logging(args.log_file, args.verbose)

    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")

    return ScraperConfig(
        start_url=args.start_url,
        output_file=args.output,
        log_file=args.log_file,
        max_pages=args.max_pages,
        headless=args.headless,
        slow_mo_ms=args.slow_mo_ms,
        page_timeout_ms=args.page_timeout_ms,
        selector_timeout_ms=args.selector_timeout_ms,
        navigation_delay_ms=args.navigation_delay_ms,
        captcha_wait_seconds=args.captcha_wait_seconds,
        min_content_chars=args.min_content_chars,
        strict_validation=args.strict_validation,
        user_data_dir=args.user_data_dir,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    config = parse_args(argv)

    logger.info("Starting N.J.A.C. extraction.")
    logger.info("Output file: %s", config.output_file)
    logger.info("Log file: %s", config.log_file)

    try:
        document_count = asyncio.run(main_async(config))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130
    except Exception:
        logger.exception("Fatal extraction error.")
        return 1

    logger.info("Extraction complete. Saved %d documents.", document_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
