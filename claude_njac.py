#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════════
    NEW JERSEY ADMINISTRATIVE CODE (N.J.A.C.) EXTRACTION TOOL

    Purpose: Extract all Titles, Chapters, and Sections from LexisNexis
             and save as structured XML format

    Author: Your Name
    Created: 2026-05-08
    Language: Python 3.8+
═════════════════════════════════════════════════════════════════════════════

SETUP INSTRUCTIONS:
───────────────────

1. Install Python 3.8 or higher (https://www.python.org/)

2. Install required packages via command line:

   Windows PowerShell:
   $ pip install playwright
   $ playwright install chromium

   macOS/Linux Terminal:
   $ pip install playwright
   $ playwright install chromium

3. Run this script:
   $ python extract_njac.py

4. When CAPTCHA appears, solve it manually in the browser window
   (Script will wait 60 seconds automatically)

5. Output will be saved to: ./output/njac_extracted.xml

═════════════════════════════════════════════════════════════════════════════
"""

# ============================================================================
# SECTION 1: IMPORTS
# ============================================================================
# These are Python libraries we need. Think of them as tools in a toolbox.
# - asyncio: Allows us to do things asynchronously (wait for web pages to load)
# - logging: Keeps track of what the script is doing (for debugging)
# - datetime: Helps us work with dates and times
# - pathlib.Path: Makes working with file paths easier
# - playwright: The main tool for controlling the browser

import asyncio  # For handling asynchronous operations
import logging  # For tracking what the script is doing
import os       # For operating system operations
from datetime import datetime  # For timestamps
from pathlib import Path  # For file path handling
from typing import Dict, Optional, Tuple  # For type hints (makes code clearer)

# Main browser automation library - controls Chromium browser
from playwright.async_api import async_playwright, Page, Browser

# ============================================================================
# SECTION 2: CONFIGURATION
# ============================================================================
# All settings are here so you can easily change them without editing code

# The URL of the N.J.A.C. on LexisNexis
# NOTE: This URL contains session parameters. If it breaks, go to
# https://advance.lexis.com/ and find the N.J.A.C. link
LANDING_PAGE = (
    'https://advance.lexis.com/container?'
    'config=00JAA5OTY5MTdjZi1lMzYxLTQxNTEtOWFkNi0xMmU5ZTViODQ2M2MKAFBvZENhdGFsb2coFSYEAfv22IKqMT9DIHrf'
    '&crid=3f2f0aa3-f402-4b70-bcc5-939ff6217c31'
    '&prid=ae61b66e-a692-42a3-8599-4236c0739dca'
)

# Where to save the extracted data
OUTPUT_DIR = Path('output')  # Creates ./output/ folder
OUTPUT_FILE = OUTPUT_DIR / 'njac_extracted.xml'  # Full path: ./output/njac_extracted.xml

# Where to save log files (useful for debugging)
LOG_DIR = Path('logs')  # Creates ./logs/ folder
LOG_FILE = LOG_DIR / 'extraction.log'  # Full path: ./logs/extraction.log

# Safety limits to prevent accidental infinite loops
MAX_PAGES = 10000  # Will stop after extracting 10,000 pages (safety limit)
CAPTCHA_WAIT_TIME = 60  # Waits 60 seconds for you to solve CAPTCHA manually

# Timeouts (how long to wait for things to happen)
PAGE_LOAD_TIMEOUT = 15000  # 15 seconds to load a page (in milliseconds)
NAVIGATION_DELAY = 1000    # Wait 1 second between page transitions (prevents rate limiting)

# Browser settings
HEADLESS_MODE = False  # Set to True if you don't want to see the browser window
SLOW_MOTION = 100      # Slow down actions by 100ms (good for debugging)

# Logging level - options: DEBUG (most verbose), INFO, WARNING, ERROR
LOG_LEVEL = logging.INFO

# ============================================================================
# SECTION 3: CREATE OUTPUT DIRECTORIES
# ============================================================================
# Creates the folders where we'll save our files
OUTPUT_DIR.mkdir(exist_ok=True)  # exist_ok=True means "don't error if it already exists"
LOG_DIR.mkdir(exist_ok=True)


# ============================================================================
# SECTION 4: SETUP LOGGING
# ============================================================================
# This creates a logger that prints messages to both the console and a file
# Educational note: Logging is better than print() because it includes timestamps
#                  and can be saved to a file for later analysis

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    # Saves logs to a file
    handlers=[
        logging.FileHandler(LOG_FILE),
        # Also prints to console (so you see it in real-time)
        logging.StreamHandler()
    ]
)

# Create a logger object that we'll use throughout the script
logger = logging.getLogger(__name__)


# ============================================================================
# SECTION 5: UTILITY FUNCTIONS - XML HANDLING
# ============================================================================
# These functions handle XML-specific operations
# Educational note: XML is a structured format for storing data.
#                  Certain characters need to be "escaped" in XML:
#                  & becomes &amp;, < becomes &lt;, etc.

def escape_xml(text: str) -> str:
    """
    Convert special XML characters to safe versions.

    Why? XML uses <> and & for tags, so if your data contains these
    characters, they need to be converted to escape sequences.

    Example:
        Input: "5 < 10 & true"
        Output: "5 &lt; 10 &amp; true"

    Args:
        text: The text to escape

    Returns:
        The safely escaped text ready for XML
    """
    if not text:
        return ''

    # Convert to string (in case it's not already)
    text = str(text)

    # Dictionary of replacements - order matters! & must be first
    replacements = {
        '&': '&amp;',    # Must be first!
        '<': '&lt;',     # Less than
        '>': '&gt;',     # Greater than
        '"': '&quot;',   # Double quote
        "'": '&apos;'    # Single quote (apostrophe)
    }

    # Replace each special character with its escape sequence
    for char, escape_seq in replacements.items():
        text = text.replace(char, escape_seq)

    return text


def initialize_output_file():
    """
    Create the XML file with the header.

    Educational note: Every XML file needs a header that declares it's XML
                     and sets the encoding. This function creates the skeleton
                     that we'll fill in as we extract data.
    """
    # The XML header - every XML file should start like this
    xml_header = f"""<?xml version="1.0" encoding="UTF-8"?>
<njacDocuments>
    <metadata>
        <source>New Jersey Administrative Code (N.J.A.C.)</source>
        <sourceURL>{LANDING_PAGE}</sourceURL>
        <extractionDate>{datetime.utcnow().isoformat()}Z</extractionDate>
        <extractionTool>Playwright-Python</extractionTool>
        <totalDocuments>COUNTING...</totalDocuments>
    </metadata>
    <documents>
"""

    # Write the header to the file
    OUTPUT_FILE.write_text(xml_header)
    logger.info(f"✓ Initialized output file: {OUTPUT_FILE}")


def finalize_output_file(document_count: int):
    """
    Complete the XML file with closing tags and update document count.

    Educational note: After we're done extracting, we need to:
                     1. Close all XML tags properly
                     2. Update the total document count
                     This ensures the XML is "well-formed" (valid)
    """
    # Closing XML tags
    footer = """    </documents>
</njacDocuments>
"""

    # Read what we've written so far
    content = OUTPUT_FILE.read_text()

    # Replace the placeholder "COUNTING..." with actual number
    content = content.replace(
        '<totalDocuments>COUNTING...</totalDocuments>',
        f'<totalDocuments>{document_count}</totalDocuments>'
    )

    # Add the closing tags
    content = content.rstrip() + '\n' + footer

    # Write it back
    OUTPUT_FILE.write_text(content)
    logger.info(f"✓ Finalized output file with {document_count} documents")


def build_xml_record(data: Dict[str, str]) -> str:
    """
    Convert one page's data into an XML record.

    Educational note: This creates a single <document> block with all the
                     extracted information. Each extracted page becomes one
                     <document> in our XML file.

    Args:
        data: Dictionary with keys like 'level10', 'level20', etc.

    Returns:
        A properly formatted XML string ready to append to file

    Example output:
        <document>
            <sourceURL>https://...</sourceURL>
            <level10>TITLE 1. ADMINISTRATIVE LAW</level10>
            ...
        </document>
    """
    # Build the XML record - note the indentation is for readability
    record = f"""        <document>
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
    return record


def validate_record(record: Dict[str, str]) -> bool:
    """
    Check if extracted data is complete and meaningful.

    Educational note: Not all pages have complete data. We validate to make
                     sure we only save records that have the required fields.
                     This prevents saving incomplete or garbage data.

    A valid record must have:
        - Title (level10)
        - Chapter (level20)
        - Source URL
        - Content body with at least 50 characters

    Args:
        record: Dictionary of extracted data

    Returns:
        True if record is valid, False otherwise
    """
    # Check that required fields exist and aren't empty
    required_fields = ['level10', 'level20', 'source_url', 'contents']

    for field in required_fields:
        # If field is missing or empty, validation fails
        if not record.get(field) or not str(record[field]).strip():
            logger.debug(f"❌ Validation failed: missing or empty {field}")
            return False

    # Make sure contents has meaningful length (not just "a" or "test")
    if len(record['contents'].strip()) < 50:
        logger.debug(f"❌ Validation failed: contents too short ({len(record['contents'])} chars)")
        return False

    # All checks passed!
    logger.debug(f"✓ Record validated successfully")
    return True


# ============================================================================
# SECTION 6: WEB PAGE EXTRACTION FUNCTIONS
# ============================================================================
# These functions extract data from web pages using Playwright
# Educational note: "async def" means this function is asynchronous - it can
#                  pause and wait for things (like page loads) without freezing

async def extract_title_level(page: Page) -> str:
    """
    Extract the TITLE level (Level 10).

    The title appears in the page with a pattern like:
        "TITLE 1. ADMINISTRATIVE LAW"

    Educational note: We use "text=/^TITLE \\d+\\./" which is a regex pattern:
                     ^ = start of text
                     TITLE = literal word "TITLE"
                     \\d+ = one or more digits
                     \\. = a period (escaped because . is special in regex)
    """
    try:
        # Look for text matching the pattern
        # The .first gets the first match if there are multiple
        title_element = await page.locator('text=/^TITLE \\d+\\./').first.text_content()

        if title_element:
            return title_element.strip()  # .strip() removes extra spaces
        return ''
    except Exception as e:
        # If something goes wrong, log it but don't crash
        logger.debug(f"Could not extract title: {e}")
        return ''


async def extract_chapter_level(page: Page) -> str:
    """
    Extract the CHAPTER level (Level 20).

    Pattern: "CHAPTER 1. UNIFORM ADMINISTRATIVE PROCEDURE RULES"
    """
    try:
        chapter_element = await page.locator('text=/^CHAPTER \\d+\\./').first.text_content()
        if chapter_element:
            return chapter_element.strip()
        return ''
    except Exception as e:
        logger.debug(f"Could not extract chapter: {e}")
        return ''


async def extract_subchapter_level(page: Page) -> str:
    """
    Extract the SUBCHAPTER level (Level 30).

    Pattern: "SUBCHAPTER 1. APPLICABILITY, SCOPE, CITATION OF RULES..."
    """
    try:
        subchapter_element = await page.locator('text=/^SUBCHAPTER \\d+\\./').first.text_content()
        if subchapter_element:
            return subchapter_element.strip()
        return ''
    except Exception as e:
        logger.debug(f"Could not extract subchapter: {e}")
        return ''


async def extract_section_header(page: Page) -> str:
    """
    Extract the SECTION level (Level 40).

    Pattern: "§ 1:1-1.1 Applicability; scope; special hearing rules"

    Educational note: The § symbol is special - it's the "section sign" used
                     in legal documents. We look for it to identify sections.
    """
    try:
        # Wait up to 5 seconds for a heading element to appear
        await page.wait_for_selector('h1, h2', timeout=5000)

        # Look for heading with § symbol
        section_element = await page.locator('h1:has-text("§"), h2:has-text("§")').first.text_content()

        if section_element:
            return section_element.strip()

        # Fallback: if no § symbol, return the first heading
        fallback = await page.locator('h1, h2').first.text_content()
        if fallback:
            return fallback.strip()

        return ''
    except Exception as e:
        logger.debug(f"Could not extract section header: {e}")
        return ''


async def extract_content_body(page: Page) -> str:
    """
    Extract the main content (the actual text of the regulation).

    Educational note: The <main> tag in HTML is used for the primary content
                     of the page. We extract all text from inside it.
    """
    try:
        # Wait for main content area to load
        await page.wait_for_selector('main', timeout=5000)

        # Extract all text from the main area
        content = await page.locator('main').text_content()

        if content:
            # Clean up excessive whitespace
            # ' '.join(content.split()) removes all extra spaces and newlines
            content = ' '.join(content.split())
            logger.debug(f"Extracted {len(content)} characters of content")
            return content

        return ''
    except Exception as e:
        logger.debug(f"Could not extract content body: {e}")
        return ''


async def extract_page_data(page: Page) -> Optional[Dict[str, str]]:
    """
    Extract ALL hierarchical data from the current page.

    This is the main extraction function. It:
    1. Waits for page to load
    2. Extracts all levels (10-40)
    3. Extracts the content body
    4. Returns everything as a dictionary

    Args:
        page: The Playwright page object

    Returns:
        Dictionary with keys like 'level10', 'level20', etc.
        Returns None if extraction fails
    """
    try:
        logger.info(f"📄 Extracting data from: {page.url}")

        # Extract each level
        level10 = await extract_title_level(page)
        level20 = await extract_chapter_level(page)
        level30 = await extract_subchapter_level(page)
        level40 = await extract_section_header(page)
        contents = await extract_content_body(page)

        # Build the record dictionary
        # Levels 50-100 are left empty (they may not exist on most pages)
        record = {
            'source_url': page.url,
            'level10': level10,
            'level20': level20,
            'level30': level30,
            'level40': level40,
            'level50': '',  # Empty - may be filled if present
            'level60': '',
            'level70': '',
            'level80': '',
            'level90': '',
            'level100': '',
            'contents': contents
        }

        return record

    except Exception as e:
        logger.error(f"❌ Error extracting page data: {e}")
        return None


# ============================================================================
# SECTION 7: NAVIGATION FUNCTIONS
# ============================================================================
# These functions handle moving between pages

async def check_for_captcha(page: Page) -> bool:
    """
    Check if CAPTCHA protection is active on this page.

    Educational note: CAPTCHA (Completely Automated Public Turing test to tell
                     Computers and Humans Apart) is a security measure.
                     It prevents automated scripts from accessing the site.
                     If we hit it, we pause and wait for human input.
    """
    try:
        # Look for any text containing "CAPTCHA"
        captcha_present = await page.locator('text=/CAPTCHA/i').is_visible()

        if captcha_present:
            logger.warning("🚨 CAPTCHA detected! Manual action required.")

        return captcha_present
    except:
        # If we can't find it, assume it's not there
        return False


async def navigate_to_first_content(page: Page) -> bool:
    """
    From the Table of Contents page, navigate to the first content page.

    The first content page is usually "Title 1, Chapter 1 -- Chapter Notes"

    Educational note: The Table of Contents (TOC) is just a list of links.
                     We need to click on one link to start viewing actual content.
    """
    try:
        logger.info("🔗 Navigating to first content page...")

        # Look for the Chapter Notes link
        # This uses a regex pattern to find text like "Title 1, Chapter 1 -- Chapter Notes"
        chapter_notes = page.locator('text=/Title \\d+, Chapter \\d+ -- Chapter Notes/').first

        # Check if the link is visible (exists on page)
        if await chapter_notes.is_visible():
            # Click the link
            await chapter_notes.click()

            # Wait for the page to fully load
            # 'networkidle' means no network activity for a while (page is done loading)
            await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)

            logger.info(f"✓ Successfully navigated to: {page.url}")
            return True
        else:
            logger.warning("⚠️  Chapter Notes link not found")
            return False

    except Exception as e:
        logger.error(f"❌ Error navigating to first content: {e}")
        return False


async def has_next_page(page: Page) -> bool:
    """
    Check if the "Next" button is available on the current page.

    Educational note: We check if the button exists AND is visible.
                     If it's grayed out or hidden, it returns False.
                     When there's no Next button, we've reached the end of data.
    """
    try:
        # Look for a link with text "Next"
        next_button = page.locator('a:has-text("Next")').first

        # Check if it's actually visible on the page
        is_visible = await next_button.is_visible()

        return is_visible
    except:
        # If we can't find the button, assume we're at the end
        return False


async def navigate_to_next_page(page: Page, max_retries: int = 3) -> bool:
    """
    Click the "Next" button and wait for the next page to load.

    Educational note: Navigation can fail due to network issues.
                     If it fails, we retry (up to 3 times) before giving up.
                     This makes the script more robust.

    Args:
        page: The Playwright page object
        max_retries: How many times to try before giving up

    Returns:
        True if successful, False if failed or no more pages
    """
    for attempt in range(max_retries):
        try:
            # First, check if Next button even exists
            if not await has_next_page(page):
                logger.info("⏹️  No more pages - end of document reached")
                return False

            # Click the Next button
            await page.locator('a:has-text("Next")').first.click()

            # Wait for page to load
            await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)

            # Add a small delay to be nice to the server (don't hammer it)
            await asyncio.sleep(NAVIGATION_DELAY / 1000)

            logger.info(f"→ Navigated to next page: {page.url[:80]}...")
            return True

        except Exception as e:
            logger.warning(f"⚠️  Navigation attempt {attempt + 1}/{max_retries} failed: {e}")

            # If not the last attempt, wait a bit and retry
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                logger.error("❌ Failed to navigate after all retry attempts")
                return False


# ============================================================================
# SECTION 8: MAIN EXTRACTION LOOP
# ============================================================================
# This is where all the pieces come together

async def extract_all_documents(page: Page) -> int:
    """
    Main extraction loop - keep clicking Next and extracting data until
    we reach the end of the document.

    Educational note: This is the "heart" of the script. It:
                     1. Extracts data from current page
                     2. Validates it
                     3. Saves it to file
                     4. Clicks Next
                     5. Repeats until no more pages

    Args:
        page: The Playwright page object

    Returns:
        Total number of documents successfully extracted
    """
    document_count = 0
    page_count = 0

    # Keep looping while there are more pages
    has_more_pages = True
    while has_more_pages and page_count < MAX_PAGES:
        page_count += 1
        logger.info(f"═══ PAGE {page_count}/{MAX_PAGES} ═══")

        try:
            # Extract data from current page
            data = await extract_page_data(page)

            # Only save if data is valid
            if data and validate_record(data):
                # Convert data to XML and append to file
                xml_record = build_xml_record(data)

                # Read current file, add new record, write back
                current_content = OUTPUT_FILE.read_text()
                OUTPUT_FILE.write_text(current_content + xml_record)

                document_count += 1
                logger.info(f"✓ Document {document_count} saved successfully")
            else:
                logger.warning(f"⚠️  Page {page_count} validation failed - skipping")

            # Try to navigate to next page
            has_more_pages = await navigate_to_next_page(page)

        except Exception as e:
            logger.error(f"❌ Error in extraction loop at page {page_count}: {e}")
            # Try to recover and continue
            await asyncio.sleep(2)
            continue

    logger.info(f"✓ Extraction loop complete: {page_count} pages processed, {document_count} saved")
    return document_count


# ============================================================================
# SECTION 9: MAIN EXECUTION FUNCTION
# ============================================================================
# This is where everything starts

async def main():
    """
    Main entry point - orchestrates the entire extraction process.

    Steps:
    1. Initialize the output file
    2. Launch browser
    3. Navigate to landing page
    4. Handle CAPTCHA if needed
    5. Navigate to first content
    6. Run extraction loop
    7. Finalize output file
    8. Close browser

    Educational note: The 'async with' statement ensures the browser closes
                     properly even if an error occurs (using finally).
    """
    logger.info("=" * 80)
    logger.info("🚀 STARTING N.J.A.C. EXTRACTION")
    logger.info("=" * 80)
    logger.info(f"Landing page: {LANDING_PAGE}")
    logger.info(f"Output file: {OUTPUT_FILE}")
    logger.info(f"Log file: {LOG_FILE}")
    logger.info("=" * 80)

    # Initialize output file with XML header
    initialize_output_file()

    # Create a browser context using Playwright
    # 'async with' ensures it closes properly when we're done
    async with async_playwright() as p:
        # Launch the Chromium browser
        browser = await p.chromium.launch(
            headless=HEADLESS_MODE,  # Set to False to see the browser
            slow_mo=SLOW_MOTION      # Slows down actions for easier observation
        )

        # Create a new page (tab) in the browser
        page = await browser.new_page()

        try:
            # ===== STEP 1: Navigate to landing page =====
            logger.info("📍 Navigating to landing page...")
            await page.goto(LANDING_PAGE, wait_until='networkidle')
            logger.info(f"✓ Loaded: {page.url}")

            # ===== STEP 2: Check for CAPTCHA =====
            if await check_for_captcha(page):
                logger.warning(f"⏳ CAPTCHA detected - waiting {CAPTCHA_WAIT_TIME} seconds")
                logger.warning("   Please solve the CAPTCHA in the browser window...")

                # Wait for manual CAPTCHA resolution
                await asyncio.sleep(CAPTCHA_WAIT_TIME)
                logger.info("✓ CAPTCHA wait period over, resuming extraction...")

            # ===== STEP 3: Navigate to first content page =====
            success = await navigate_to_first_content(page)

            if not success:
                logger.warning("⚠️  Could not find Chapter Notes link")
                logger.info("   Attempting alternative approach...")

            # ===== STEP 4: Run main extraction loop =====
            logger.info("🔄 Starting main extraction loop...")
            document_count = await extract_all_documents(page)

            # ===== STEP 5: Finalize output =====
            finalize_output_file(document_count)

            # ===== SUMMARY =====
            logger.info("=" * 80)
            logger.info("✅ EXTRACTION COMPLETE!")
            logger.info("=" * 80)
            logger.info(f"Total documents extracted: {document_count}")
            logger.info(f"Output saved to: {OUTPUT_FILE}")
            logger.info(f"Log saved to: {LOG_FILE}")
            logger.info("=" * 80)

        except Exception as e:
            # If anything goes wrong, log it and continue to cleanup
            logger.error(f"❌ Fatal error during extraction: {e}", exc_info=True)

        finally:
            # Always close the browser, even if an error occurred
            await browser.close()
            logger.info("🌐 Browser closed")


# ============================================================================
# SECTION 10: SCRIPT ENTRY POINT
# ============================================================================
# This code runs when the script is executed

if __name__ == '__main__':
    # 'asyncio.run()' starts the async event loop and runs main()
    # Educational note: Python's async functions need an event loop.
    #                  This is like starting the engine of a car.
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Allows Ctrl+C to stop the script gracefully
        logger.info("\n⚠️  Script interrupted by user (Ctrl+C)")
    except Exception as e:
        # Catch any errors we didn't anticipate
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)


# ============================================================================
# TROUBLESHOOTING GUIDE
# ============================================================================
"""
COMMON ISSUES & SOLUTIONS:

1. PROBLEM: "ModuleNotFoundError: No module named 'playwright'"
   SOLUTION: Install playwright
   $ pip install playwright
   $ playwright install chromium

2. PROBLEM: "Connection refused" or "Connection timeout"
   SOLUTION: The LexisNexis website is slow or down
   - Try increasing PAGE_LOAD_TIMEOUT from 15000 to 30000
   - Try again later

3. PROBLEM: Script extracts 0 documents
   SOLUTION: The page structure may have changed
   - Check if the "Next" button still has text "Next"
   - Check if level extraction patterns still match the page
   - Try manually visiting the site to see what changed

4. PROBLEM: CAPTCHA is always blocking extraction
   SOLUTION: CAPTCHA solver required for unattended runs
   - For manual runs: solve during the 60-second wait
   - For automated runs: implement a CAPTCHA solver library

5. PROBLEM: Script runs very slowly
   SOLUTION: Adjust these settings:
   - Change SLOW_MOTION from 100 to 0 (removes slowdown)
   - Change PAGE_LOAD_TIMEOUT from 15000 to 10000 (shorter wait)
   - Change HEADLESS_MODE from False to True (no browser window = faster)

6. PROBLEM: XML file is corrupted/won't open
   SOLUTION: Check the XML structure
   - The file should start with <?xml version...
   - It should end with </njacDocuments>
   - Check the log file to see where extraction failed

7. PROBLEM: Script crashes with "BrowserError"
   SOLUTION: Browser process crashed
   - Close other applications to free up memory
   - Make sure you have 1GB+ free disk space
   - Try restarting your computer

8. PROBLEM: Nothing happens when I run the script
   SOLUTION: Check these things:
   - Python is installed: run "python --version" in terminal
   - Required packages installed: run "pip list | grep playwright"
   - Output folder has write permissions
   - Try adding more verbose logging (set LOG_LEVEL = logging.DEBUG)

═══════════════════════════════════════════════════════════════════════════

GETTING HELP:

1. Check the log file at: ./logs/extraction.log
2. Read the ERROR and WARNING messages
3. Google the error message
4. Search on Stack Overflow
5. Check Playwright documentation: https://playwright.dev/python/

═══════════════════════════════════════════════════════════════════════════
"""
