#!/usr/bin/env python3
"""
N.J.A.C. EXTRACTION TOOL
─────────────────────────────────────────────────────────────────────────────
Extracts all Titles, Chapters, and Sections from LexisNexis Free Public
Access and writes structured XML.  One <document> per page, navigating via
the "Next" control until the last section.

SETUP:
  pip install playwright
  playwright install chromium
  python claude_njac.py

OUTPUT:  ./output/njac_extracted.xml
LOGS:    ./logs/extraction.log

DESIGN NOTES:
  • Breadcrumbs live in #TOCTrail — NOT page-wide <a> tags (TOC sidebar
    poisoned results with dozens of wrong SUBCHAPTER links).
  • Content uses inner_text(), NOT text_content() — text_content() strips
    all line breaks; inner_text() respects CSS rendering.
  • All file I/O is explicit encoding='utf-8' — Windows default (cp1252)
    turns § into ◆.
  • TOC leaf links use href="#" — clicking them fires JS that changes the
    URL; you cannot navigate to them directly.
─────────────────────────────────────────────────────────────────────────────
"""

# ── Imports ───────────────────────────────────────────────────────────────────
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from playwright.async_api import async_playwright, Page, Browser


# ── Configuration ─────────────────────────────────────────────────────────────
# Stable short URL — 302-redirects to the TOC. Avoids crid/prid params that
# expire every session.
LANDING_PAGE = 'http://www.lexisnexis.com/hottopics/njcode/'

OUTPUT_DIR  = Path('output')
OUTPUT_FILE = OUTPUT_DIR / 'njac_extracted.xml'
LOG_DIR     = Path('logs')
LOG_FILE    = LOG_DIR / 'extraction.log'

MAX_PAGES         = 10000   # safety ceiling
CAPTCHA_WAIT_TIME = 60      # seconds to let human solve CAPTCHA

PAGE_LOAD_TIMEOUT  = 30000  # ms — LexisNexis is slow
NAVIGATION_DELAY   = 1000   # ms between pages — polite crawling

HEADLESS_MODE = False  # True = no visible browser (faster; harder to debug)
SLOW_MOTION   = 100    # ms — slows Playwright actions; set 0 for speed

LOG_LEVEL = logging.INFO

OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(
            stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
        )
    ]
)
logger = logging.getLogger(__name__)


# ── XML Utilities ─────────────────────────────────────────────────────────────

def escape_xml(text: str) -> str:
    """Escape special XML chars so they don't break the output file.
    & must go first — otherwise we'd double-escape the & in &amp;.
    """
    if not text:
        return ''
    text = str(text)
    for char, seq in [('&','&amp;'), ('<','&lt;'), ('>','&gt;'),
                      ('"','&quot;'), ("'",'&apos;')]:
        text = text.replace(char, seq)
    return text


def initialize_output_file():
    """Write the XML header + open tags. Uses a COUNTING... placeholder
    for totalDocuments that finalize_output_file() swaps in at the end.
    encoding='utf-8' is mandatory — omitting it on Windows causes § → ◆.
    """
    header = f"""<?xml version="1.0" encoding="UTF-8"?>
<njacDocuments>
    <metadata>
        <source>New Jersey Administrative Code (N.J.A.C.)</source>
        <sourceURL>{LANDING_PAGE}</sourceURL>
        <extractionDate>{datetime.now(timezone.utc).isoformat()}</extractionDate>
        <extractionTool>Playwright-Python</extractionTool>
        <totalDocuments>COUNTING...</totalDocuments>
    </metadata>
    <documents>
"""
    OUTPUT_FILE.write_text(header, encoding='utf-8')
    logger.info(f"✓ Initialized output file: {OUTPUT_FILE}")


def finalize_output_file(document_count: int):
    """Swap in final doc count and close XML tags."""
    footer = "    </documents>\n</njacDocuments>\n"
    content = OUTPUT_FILE.read_text(encoding='utf-8')
    content = content.replace(
        '<totalDocuments>COUNTING...</totalDocuments>',
        f'<totalDocuments>{document_count}</totalDocuments>'
    )
    OUTPUT_FILE.write_text(content.rstrip() + '\n' + footer, encoding='utf-8')
    logger.info(f"✓ Finalized: {document_count} documents")


def build_xml_record(data: Dict[str, str]) -> str:
    """Serialize one page's extracted dict into a <document> XML block."""
    return f"""        <document>
            <sourceURL>{escape_xml(data.get('source_url', ''))}</sourceURL>
            <level10>{escape_xml(data.get('level10', ''))}</level10>
            <level20>{escape_xml(data.get('level20', ''))}</level20>
            <level30>{escape_xml(data.get('level30', ''))}</level30>
            <level40>{escape_xml(data.get('level40', ''))}</level40>
            <level50>{escape_xml(data.get('level50', ''))}</level50>
            <level60>{escape_xml(data.get('level60', ''))}</level60>
            <level70>{escape_xml(data.get('level70', ''))}</level70>
            <level80>{escape_xml(data.get('level80', ''))}</level80>
            <level90>{escape_xml(data.get('level90', ''))}</level90>
            <level100>{escape_xml(data.get('level100', ''))}</level100>
            <contents>{escape_xml(data.get('contents', ''))}</contents>
        </document>
"""


def validate_record(record: Dict[str, str]) -> bool:
    """Reject records missing source_url, empty contents, or < 50 chars of content.
    Level fields are optional — section pages sometimes lack breadcrumb levels.
    """
    for field in ['source_url', 'contents']:
        if not record.get(field, '').strip():
            logger.debug(f"❌ Validation failed: empty {field}")
            return False
    if len(record['contents'].strip()) < 50:
        logger.debug("❌ Validation failed: contents too short")
        return False
    levels_found = [k for k in ('level10','level20','level30','level40') if record.get(k)]
    logger.debug(f"✓ Valid — levels: {levels_found or ['(none)']}")
    return True


# ── Extraction Functions ───────────────────────────────────────────────────────

async def extract_hierarchy_levels(page: Page) -> Dict[str, str]:
    """
    Map the page breadcrumb + heading into level10…level40.

    WHY THIS APPROACH:
    Early versions queried all <a> tags for TITLE/CHAPTER/SUBCHAPTER text.
    This matched TOC sidebar links too, injecting 10+ wrong SUBCHAPTER entries
    on chapter-notes pages. Fix: read only #TOCTrail (the actual breadcrumb
    bar), which contains exactly the right links in order.

    PSEUDOCODE:
      trail = [link text for each <a> in #TOCTrail matching TITLE/CHAPTER/SUBCHAPTER]
      assign trail[0]→level10, trail[1]→level20, trail[2]→level30
      heading = h2.SS_Banner text
      heading → level[len(trail)]  ← always the slot right after breadcrumbs

    EXAMPLES:
      Chapter Notes page  (2 trail links, heading has no §):
        trail  = ["TITLE 1...", "CHAPTER 1..."]
        heading = "Title 1, Chapter 1 -- Chapter Notes"
        → level10=TITLE, level20=CHAPTER, level30=heading, level40=""

      Section page  (3 trail links, heading has §):
        trail  = ["TITLE 1...", "CHAPTER 1...", "SUBCHAPTER 1..."]
        heading = "§ 1:1-1.1 Applicability..."
        → level10=TITLE, level20=CHAPTER, level30=SUBCHAPTER, level40=§ heading
    """
    level_keys = ['level10', 'level20', 'level30', 'level40']
    result = {k: '' for k in level_keys}

    # ── Breadcrumb trail ──────────────────────────────────────────────────────
    trail_links = []
    try:
        crumb_pattern = re.compile(r'^(TITLE|CHAPTER|SUBCHAPTER)\s+\d+\.', re.IGNORECASE)
        locs = page.locator('#TOCTrail a')
        for i in range(await locs.count()):
            text = (await locs.nth(i).text_content(timeout=2000) or '').strip()
            if crumb_pattern.match(text):
                trail_links.append(text)
    except Exception as e:
        logger.debug(f"#TOCTrail read failed: {e}")

    for i, text in enumerate(trail_links[:3]):
        result[level_keys[i]] = text

    # ── Page heading → next available slot ───────────────────────────────────
    next_slot = min(len(trail_links), 3)
    try:
        heading = (await page.locator('h2.SS_Banner').first.text_content(timeout=4000) or '').strip()
        if heading:
            result[level_keys[next_slot]] = heading
            logger.debug(f"h2.SS_Banner → {level_keys[next_slot]}: {heading[:60]}")
    except Exception as e:
        logger.debug(f"h2.SS_Banner read failed: {e}")

    logger.debug(f"Levels: {[k for k in level_keys if result[k]]}")
    return result


async def extract_content_body(page: Page) -> str:
    """
    Return the regulation body text with line breaks preserved.

    WHY inner_text() NOT text_content():
    text_content() strips all whitespace → (a),(b),(c) paragraphs merge into
    one unreadable blob. inner_text() respects CSS rendering → keeps breaks.

    WHY 'span.SS_LeftAlign > div' FIRST:
    Verified by DOM inspection: this div contains only regulation text,
    2394 chars, containsNav=false. Broader containers (div.document-text,
    section.doc-content) include "Previous / Next / Copy Citation" noise.

    PSEUDOCODE:
      for selector in [specific → generic]:
          text = element.inner_text()
          if len(text) >= 50: return normalize(text)
      return ''
    """
    content_selectors = [
        'span.SS_LeftAlign > div',  # ✓ DOM-verified: pure reg text, no nav
        'span.SS_LeftAlign',        # parent fallback
        'main',
        'article',
        '[role="main"]',
        '.la-card',
        '.content-container',
        '.document-content',
        '#content',
        '.content',
        'body',                     # last resort — includes nav noise
    ]

    for selector in content_selectors:
        try:
            await page.wait_for_selector(selector, timeout=4000)
            content = await page.locator(selector).first.inner_text()
            if content:
                # Collapse 3+ blank lines → 1; keep paragraph/subsection breaks
                content = re.sub(r'\n{3,}', '\n\n', content.strip())
                if len(content) >= 50:
                    logger.debug(f"Content via '{selector}': {len(content)} chars")
                    return content
        except Exception:
            continue

    logger.debug("No content selector matched")
    return ''


async def extract_page_data(page: Page) -> Optional[Dict[str, str]]:
    """
    Orchestrate extraction for one page → returns a flat record dict.

    PSEUDOCODE:
      levels  = extract_hierarchy_levels(page)   # breadcrumb + heading
      content = extract_content_body(page)        # regulation text
      return {source_url, level10..40, contents}
    """
    try:
        logger.info(f"📄 {page.url}")
        levels   = await extract_hierarchy_levels(page)
        contents = await extract_content_body(page)
        return {
            'source_url': page.url,
            'level10': levels.get('level10', ''),
            'level20': levels.get('level20', ''),
            'level30': levels.get('level30', ''),
            'level40': levels.get('level40', ''),
            'level50': '', 'level60': '', 'level70': '',
            'level80': '', 'level90': '', 'level100': '',
            'contents': contents
        }
    except Exception as e:
        logger.error(f"❌ extract_page_data: {e}")
        return None


# ── Navigation Functions ───────────────────────────────────────────────────────

async def check_for_captcha(page: Page) -> bool:
    """Return True if a CAPTCHA element is visible on the page."""
    try:
        present = await page.locator('text=/CAPTCHA/i').is_visible()
        if present:
            logger.warning("🚨 CAPTCHA detected — manual solve required")
        return present
    except:
        return False


async def auto_click_agree(page: Page, max_wait_seconds: int = 25) -> bool:
    """
    Poll for up to 25s and click any consent / I-Agree button.
    Returns True if clicked, False if no button appeared (session already accepted).
    Selectors ordered most-specific → generic to avoid false positives.
    """
    logger.info("🤖 Looking for consent button...")
    agree_selectors = [
        'button:has-text("I Agree")', 'a:has-text("I Agree")',
        'button:has-text("Agree")',   'a:has-text("Agree")',
        'button:has-text("Accept All")', 'button:has-text("Accept")',
        'a:has-text("Accept")',
        'input[type="button"][value*="Agree" i]',
        'input[type="submit"][value*="Agree" i]',
        '[id*="agree" i]', '[class*="agree" i]',
    ]
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max_wait_seconds:
        for selector in agree_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=400):
                    text = (await loc.text_content() or '').strip()
                    logger.info(f"✓ Clicking consent: {text!r}")
                    await loc.click()
                    try:
                        await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue
        await asyncio.sleep(1)
    logger.info("ℹ️  No consent button found — proceeding")
    return False


async def wait_for_toc_ready(page: Page, max_wait_seconds: int = 180) -> bool:
    """
    Poll until real TOC content appears (handles consent screens / CAPTCHAs).
    Checks for any of several TOC indicators every 2s, logs heartbeat every 20s.
    Returns True when found, False on timeout.
    """
    logger.info(f"⏳ Waiting for TOC (up to {max_wait_seconds}s) — solve any consent screen now")
    indicators = [
        'text=/^TITLE \\d+\\./',
        'text=/Chapter Notes/',
        'a[href*="documentpage"]',
        'a:has-text("§")',
    ]
    start = asyncio.get_event_loop().time()
    while (elapsed := asyncio.get_event_loop().time() - start) < max_wait_seconds:
        for ind in indicators:
            try:
                if await page.locator(ind).first.is_visible(timeout=500):
                    logger.info(f"✓ TOC ready (matched '{ind}')")
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue
        await asyncio.sleep(2)
        if int(elapsed) % 20 == 0 and int(elapsed) > 0:
            logger.info(f"   ...waiting ({int(elapsed)}s)")
    logger.warning("⚠️  TOC wait timed out")
    return False


async def dump_debug_snapshot(page: Page, label: str) -> None:
    """Save a full-page screenshot to logs/ for post-mortem debugging."""
    try:
        path = LOG_DIR / f'debug_{label}.png'
        await page.screenshot(path=str(path), full_page=True)
        logger.info(f"📸 Snapshot: {path}  |  title={await page.title()!r}  |  url={page.url}")
    except Exception as e:
        logger.debug(f"Snapshot '{label}' failed: {e}")


async def expand_toc_node(page: Page, *, level: int, title_pattern: str) -> bool:
    """
    Click the expand toggle on a lazy-loaded TOC tree node.

    WHY NOT .locator('button').first:
    Each <li> has a hidden "Save to favorites" button that appears FIRST in
    DOM order. is_visible() returns False for it but the locator still resolves
    to it, silently skipping the real toggle. Must target
    button.toc-tree__toggle-expansion explicitly.

    PSEUDOCODE:
      li = find <li data-level=N> whose text matches pattern
      if li.aria-expanded == 'true': return True  (already open)
      click li > button.toc-tree__toggle-expansion  (force=True bypasses tooltip blocks)
      wait until aria-expanded flips to 'true' in the DOM
    """
    li = page.locator(
        f'li.toc-tree__item[data-level="{level}"]'
    ).filter(has_text=re.compile(title_pattern, re.IGNORECASE)).first

    try:
        await li.wait_for(state='attached', timeout=15000)
    except Exception:
        logger.warning(f"   ⚠️  No level-{level} node matched /{title_pattern}/")
        return False

    if await li.get_attribute('aria-expanded') == 'true':
        logger.info(f"   ℹ️  Level-{level} already expanded")
        return True

    await li.locator('button.toc-tree__toggle-expansion').first.click(force=True)

    try:
        await page.wait_for_function(
            """([level, pattern]) => {
                const re = new RegExp(pattern, 'i');
                for (const li of document.querySelectorAll(
                    `li.toc-tree__item[data-level="${level}"]`)) {
                    if (re.test(li.getAttribute('data-title') || ''))
                        return li.getAttribute('aria-expanded') === 'true';
                }
                return false;
            }""",
            arg=[level, title_pattern],
            timeout=15000,
        )
        logger.info(f"   ✓ Level-{level} expanded")
        return True
    except Exception:
        logger.warning(f"   ⚠️  Level-{level} did not expand in time")
        return False


async def navigate_to_first_content(page: Page) -> bool:
    """
    Land on the first document page from the TOC.

    WHY NOT href-based navigation:
    TOC leaf links all have href="#" — the real URL only appears after the
    JS click handler fires. Selectors like a[href*="documentpage"] always
    miss inside the TOC.

    PSEUDOCODE:
      wait_for_toc_ready()
      expand_toc_node(level=1, TITLE 1)
      wait for level-2 rows to render
      expand_toc_node(level=2, CHAPTER 1)
      click first a[data-action="toclink"] under expanded chapter
      wait for URL → /documentpage/ + networkidle
    """
    try:
        logger.info("🔗 Navigating to first content page...")

        if not await wait_for_toc_ready(page, max_wait_seconds=180):
            await dump_debug_snapshot(page, 'toc_timeout')
            return False

        logger.info("📂 Expanding TITLE 1...")
        if not await expand_toc_node(page, level=1, title_pattern=r'TITLE 1\.'):
            await dump_debug_snapshot(page, 'title_expand_failed')
            return False

        await page.wait_for_selector('li.toc-tree__item[data-level="2"]', timeout=15000)
        logger.info("   ✓ CHAPTER rows rendered")

        logger.info("📂 Expanding CHAPTER 1...")
        if not await expand_toc_node(page, level=2, title_pattern=r'CHAPTER 1\.'):
            await dump_debug_snapshot(page, 'chapter_expand_failed')
            return False

        # Target leaf under the expanded chapter; fallback to any leaf on page
        leaf = page.locator(
            'li.toc-tree__item[data-level="2"][aria-expanded="true"] a[data-action="toclink"]'
        ).first
        leaf_fallback = page.locator('a[data-action="toclink"]').first

        target = None
        try:
            await leaf.wait_for(state='visible', timeout=10000)
            target = leaf
        except Exception:
            try:
                await leaf_fallback.wait_for(state='visible', timeout=5000)
                target = leaf_fallback
            except Exception:
                pass

        if target is None:
            logger.warning("⚠️  No toclink leaf found")
            await dump_debug_snapshot(page, 'no_toclink_after_expand')
            return False

        logger.info(f"✓ Clicking leaf: {(await target.text_content() or '').strip()!r}")
        await target.click()

        try:
            await page.wait_for_url('**/documentpage/**', timeout=PAGE_LOAD_TIMEOUT)
        except Exception:
            pass  # URL pattern varies; networkidle is the real gate
        await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)
        logger.info(f"✓ On first content page: {page.url}")
        return True

    except Exception as e:
        logger.error(f"❌ navigate_to_first_content: {e}")
        await dump_debug_snapshot(page, 'navigate_exception')
        return False


async def has_next_page(page: Page) -> Tuple[bool, str]:
    """
    Return (True, matched_selector) if a Next control is visible.
    Tries LexisNexis-specific class first (most reliable), then generic fallbacks.
    """
    next_selectors = [
        'a.tocdocnext',              # LexisNexis class — most reliable
        'a:has-text("Next")', 'button:has-text("Next")',
        '[aria-label="Next"]', '[aria-label="Next page"]', '[aria-label*="next" i]',
        'a.next', 'button.next',
        '.pagination a:has-text("›")', '.pagination a:has-text(">")',
        '[data-action="next"]', '[title*="Next" i]',
    ]
    for selector in next_selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=500):
                return True, selector
        except Exception:
            continue
    return False, ''


async def navigate_to_next_page(page: Page, max_retries: int = 3) -> bool:
    """
    Click Next and wait for load. Retries up to 3x on network failures.
    Returns False when no Next control exists (end of document).
    """
    for attempt in range(max_retries):
        try:
            found, selector = await has_next_page(page)
            if not found:
                logger.info("⏹️  No Next — end of document")
                return False

            await page.locator(selector).first.click()
            await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)
            await asyncio.sleep(NAVIGATION_DELAY / 1000)
            logger.info(f"→ {page.url[:80]}...")
            return True

        except Exception as e:
            logger.warning(f"⚠️  Navigation attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                logger.error("❌ Navigation failed after all retries")
                return False


# ── Main Extraction Loop ───────────────────────────────────────────────────────

async def extract_all_documents(page: Page) -> int:
    """
    Core loop: extract → validate → save → next, until no more pages.

    PSEUDOCODE:
      while has_next and page_count < MAX_PAGES:
          if url already visited: break  (loop guard)
          data = extract_page_data(page)
          if valid: append xml record to file
          navigate_to_next_page()
      return total saved

    visited_urls set guards against infinite loops (LexisNexis occasionally
    redirects back to a visited page on the last section).
    """
    document_count = 0
    page_count     = 0
    visited_urls: set = set()

    has_more_pages = True
    while has_more_pages and page_count < MAX_PAGES:
        page_count += 1

        current_url = page.url
        if current_url in visited_urls:
            logger.warning(f"⚠️  Loop detected — stopping at: {current_url[:80]}")
            break
        visited_urls.add(current_url)

        logger.info(f"═══ PAGE {page_count} ═══")

        try:
            data = await extract_page_data(page)
            if data and validate_record(data):
                xml_record = build_xml_record(data)
                current    = OUTPUT_FILE.read_text(encoding='utf-8')
                OUTPUT_FILE.write_text(current + xml_record, encoding='utf-8')
                document_count += 1
                logger.info(f"✓ Saved document {document_count}")
            else:
                logger.warning(f"⚠️  Page {page_count} skipped (validation failed)")

            has_more_pages = await navigate_to_next_page(page)

        except Exception as e:
            logger.error(f"❌ Loop error at page {page_count}: {e}")
            await asyncio.sleep(2)
            continue

    logger.info(f"✓ Done: {page_count} pages visited, {document_count} saved")
    return document_count


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    """
    Orchestrator.

    PSEUDOCODE:
      init xml file
      launch browser → goto LANDING_PAGE
      auto_click_agree()      # handles consent dialogs
      check_for_captcha()     # prompts human if needed
      navigate_to_first_content()
      extract_all_documents()
      finalize xml file
      close browser
    """
    logger.info("=" * 70)
    logger.info("🚀 N.J.A.C. EXTRACTION STARTING")
    logger.info(f"   Output: {OUTPUT_FILE}  |  Log: {LOG_FILE}")
    logger.info("=" * 70)

    initialize_output_file()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS_MODE, slow_mo=SLOW_MOTION)
        page    = await browser.new_page()

        try:
            logger.info("📍 Loading landing page...")
            await page.goto(LANDING_PAGE, wait_until='networkidle')
            logger.info(f"✓ Loaded: {page.url}")

            await auto_click_agree(page, max_wait_seconds=25)

            if await check_for_captcha(page):
                logger.warning(f"⏳ CAPTCHA — solve in browser. Waiting {CAPTCHA_WAIT_TIME}s...")
                await asyncio.sleep(CAPTCHA_WAIT_TIME)

            if not await navigate_to_first_content(page):
                logger.error("❌ Could not reach first content page. Aborting.")
                return

            logger.info("🔄 Starting extraction loop...")
            document_count = await extract_all_documents(page)
            finalize_output_file(document_count)

            logger.info("=" * 70)
            logger.info(f"✅ COMPLETE — {document_count} documents → {OUTPUT_FILE}")
            logger.info("=" * 70)

        except Exception as e:
            logger.error(f"❌ Fatal: {e}", exc_info=True)
        finally:
            await browser.close()
            logger.info("🌐 Browser closed")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⚠️  Stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Unexpected: {e}", exc_info=True)


# ── Troubleshooting ───────────────────────────────────────────────────────────
"""
PROBLEM → SOLUTION

"No module named 'playwright'"
  → pip install playwright && playwright install chromium

Script extracts 0 documents
  → LexisNexis page structure may have changed. Check:
    - Does 'Next' button still exist? (has_next_page selectors)
    - Does #TOCTrail still exist? (hierarchy extraction)
    - Set LOG_LEVEL = logging.DEBUG and re-run

§ shows as ◆ in the XML
  → encoding='utf-8' missing on a write_text() call — check all file writes

Contents are one long unbroken line
  → inner_text() was replaced with text_content() somewhere — revert

CAPTCHA blocking every run
  → For unattended runs you need a CAPTCHA solver library.
    For manual runs: solve within the {CAPTCHA_WAIT_TIME}s window.

Script loops forever / revisits pages
  → visited_urls set should catch this. If not, URL structure changed.
    Check the WARNING log lines for "Loop detected".

Script is slow
  → SLOW_MOTION = 0, HEADLESS_MODE = True, lower PAGE_LOAD_TIMEOUT

XML file won't parse
  → Must start with <?xml ... and end with </njacDocuments>
    Check logs for where extraction crashed mid-write.

Debug screenshots
  → Check ./logs/debug_*.png — saved automatically on navigation failures
"""
