# New Jersey Administrative Code (N.J.A.C.) Python-Playwright Extraction Skill

## Overview
This skill provides comprehensive guidance for building a Python-based Playwright web scraper to extract all hierarchical legal data from the New Jersey Administrative Code on LexisNexis. All code examples are in Python 3.8+.

---

## 1. SITE STRUCTURE & HIERARCHY LEARNINGS

### 1.1 Hierarchical Data Model
The N.J.A.C. follows a rigid hierarchical structure with 10 levels:

```
Level 10 (TITLE)      → TITLE 1. ADMINISTRATIVE LAW
  ├─ Level 20 (CHAPTER)    → CHAPTER 1. UNIFORM ADMINISTRATIVE PROCEDURE RULES
    ├─ Level 30 (SUBCHAPTER) → SUBCHAPTER 1. APPLICABILITY, SCOPE, CITATION OF RULES...
      ├─ Level 40 (SECTION)    → § 1:1-1.1 Applicability; scope; special hearing rules
        ├─ Level 50-100        → Subsections (a), (b), (c), etc. with content
```

### 1.2 Key URLs & Endpoints
```
Base URL:              https://advance.lexis.com/
Landing Page:          /container?config=[CONFIG_ID]&crid=[CRID]&prid=[PRID]
Document Page:         /documentpage/?pdmfid=1000516&crid=[CRID]&nodeid=[NODEID]&...
Navigation Pattern:    Click "Next" button to sequentially access next page
```

### 1.3 Page Types Encountered
- **Chapter Notes Page**: Contains chapter metadata (authority, history, effective dates)
- **Section/Subsection Pages**: Contains detailed regulatory text with subsections (a), (b), (c), etc.
- **Navigation Pattern**: Chapter Notes → Section 1 → Section 2 → ... → Section N → Next Chapter

---

## 2. DO'S AND DON'Ts

### DO's ✅
1. **DO use `page.wait_for_load_state()`** - Pages load dynamically; always wait for navigation
2. **DO extract URL from each page** - Document URLs change with each Next click; capture `page.url` for each record
3. **DO check for "Next" button availability** - Check if button exists/is enabled before clicking (end of data detection)
4. **DO parse the breadcrumb navigation** - Use breadcrumb links to reliably identify current Title/Chapter/Subchapter
5. **DO store data incrementally** - Write to file after each page extraction (prevents data loss if script crashes)
6. **DO use text content extraction** - Parse visible text; avoid relying on hidden DOM elements
7. **DO handle document references** - Some sections reference other N.J.A.C. sections (e.g., "N.J.A.C. 1:1-11.1(c)")
8. **DO implement retry logic** - Network timeouts happen; retry failed page loads 2-3 times
9. **DO wait for dynamic content** - Use `page.wait_for_selector()` to ensure content is rendered before extraction
10. **DO validate extracted data structure** - Check that all levels are populated with meaningful content

### DON'Ts ❌
1. **DON'T rely on fixed coordinates** - LexisNexis pages are responsive; use element selectors, not coordinates
2. **DON'T skip Chapter Notes pages** - These contain important metadata (statutory authority, history, effective dates)
3. **DON'T assume all levels 50-100 are always populated** - Many sections only have Levels 10-40; empty levels are acceptable
4. **DON'T click buttons without waiting** - Always use `page.click()` with subsequent navigation waits
5. **DON'T parse HTML attributes for data** - Extract visible text content; attributes may contain formatting artifacts
6. **DON'T assume linear numbering** - Sections may skip numbers; don't expect sequential section numbers
7. **DON'T use implicit waits only** - Always use explicit waits with `page.wait_for_load_state()` or `page.wait_for_selector()`
8. **DON'T extract before page is fully loaded** - Add buffer delay or wait for content stabilization
9. **DON'T hardcode element selectors** - Selectors may change; use semantic selectors (text content, labels)
10. **DON'T forget to handle CAPTCHA** - If CAPTCHA appears, pause and allow manual resolution before continuing

---

## 3. PYTHON SETUP & INSTALLATION

### 3.1 Prerequisites
```bash
# Python 3.8 or higher
python --version

# Install required packages
pip install playwright lxml

# Install browser binaries (run once)
playwright install chromium
```

### 3.2 Project Structure
```
njac_extraction/
├── extract_njac.py          # Main extraction script
├── config.py                # Configuration file
├── utils.py                 # Utility functions
├── extractors.py            # Data extraction logic
├── output/
│   └── njac_extracted.xml   # Output file
├── logs/
│   └── extraction.log       # Log file
└── requirements.txt         # Dependencies
```

### 3.3 requirements.txt
```
playwright==1.40.0
lxml==4.9.3
requests==2.31.0
python-dateutil==2.8.2
```

---

## 4. EXTRACTION TECHNIQUES & BEST PRACTICES

### 4.1 Breadcrumb Navigation Parsing (Python)
```python
async def extract_breadcrumb_data(page):
    """Extract Title, Chapter, Subchapter from breadcrumb navigation."""
    try:
        # Wait for breadcrumb to load
        await page.wait_for_selector('a[href*="container"]', timeout=5000)
        
        # Get all breadcrumb link texts
        breadcrumb_texts = await page.locator('a[href*="#"]').all_text_contents()
        
        # Parse hierarchy (expected format: ["NJ - New Jersey...", "TITLE 1...", "CHAPTER 1...", "SUBCHAPTER 1..."])
        level10 = breadcrumb_texts[1].strip() if len(breadcrumb_texts) > 1 else ''
        level20 = breadcrumb_texts[2].strip() if len(breadcrumb_texts) > 2 else ''
        level30 = breadcrumb_texts[3].strip() if len(breadcrumb_texts) > 3 else ''
        
        return level10, level20, level30
    except Exception as e:
        logger.warning(f"Error extracting breadcrumb: {e}")
        return '', '', ''
```

### 4.2 Section Header Extraction (Python)
```python
async def extract_section_header(page):
    """Extract section header (§ X:X-X.X format)."""
    try:
        # Look for header with § symbol
        section_header = await page.locator('h1:has-text("§"), h2:has-text("§")').first.text_content()
        return section_header.strip() if section_header else ''
    except Exception as e:
        logger.warning(f"Error extracting section header: {e}")
        return ''
```

### 4.3 Content Body Extraction (Python)
```python
async def extract_content_body(page):
    """Extract full content including subsections (a), (b), (c), etc."""
    try:
        # Wait for main content area
        await page.wait_for_selector('main', timeout=5000)
        
        # Extract all text from main area
        content = await page.locator('main').text_content()
        
        # Clean up whitespace
        content = ' '.join(content.split())
        
        return content if content else ''
    except Exception as e:
        logger.warning(f"Error extracting content body: {e}")
        return ''
```

### 4.4 Next Button Detection & Navigation (Python)
```python
async def navigate_to_next_page(page, max_retries=3):
    """Click Next button and wait for page load."""
    for attempt in range(max_retries):
        try:
            # Check if Next button exists and is visible
            next_button = page.locator('a:has-text("Next")').first
            is_visible = await next_button.is_visible()
            
            if not is_visible:
                logger.info("Next button not visible. End of document reached.")
                return False
            
            # Click Next button
            await next_button.click()
            
            # Wait for navigation
            await page.wait_for_load_state('networkidle', timeout=15000)
            
            logger.info(f"Successfully navigated to next page. URL: {page.url}")
            return True
            
        except Exception as e:
            logger.warning(f"Navigation attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # Wait before retry
            else:
                logger.error("Failed to navigate to next page after retries")
                return False
```

### 4.5 Data Validation (Python)
```python
def validate_record(record):
    """Validate that extracted record has required data."""
    required_fields = ['level10', 'level20', 'source_url', 'contents']
    
    for field in required_fields:
        if not record.get(field) or not str(record[field]).strip():
            return False
    
    # Ensure contents has meaningful length
    if len(record['contents'].strip()) < 50:
        return False
    
    return True
```

### 4.6 XML Safe String Escaping (Python)
```python
def escape_xml(text):
    """Escape special XML characters."""
    if not text:
        return ''
    
    text = str(text)
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;'
    }
    
    for char, escape in replacements.items():
        text = text.replace(char, escape)
    
    return text
```

---

## 5. XML OUTPUT FORMAT SPECIFICATION

### 5.1 Single Record Structure
```xml
<document>
    <sourceURL>https://advance.lexis.com/documentpage/?pdmfid=1000516&crid=...</sourceURL>
    <level10>TITLE 1. ADMINISTRATIVE LAW</level10>
    <level20>CHAPTER 1. UNIFORM ADMINISTRATIVE PROCEDURE RULES</level20>
    <level30>SUBCHAPTER 1. APPLICABILITY, SCOPE, CITATION OF RULES, CONSTRUCTION AND RELAXATION; COMPUTATION OF TIME</level30>
    <level40>§ 1:1-1.1 Applicability; scope; special hearing rules</level40>
    <level50></level50>
    <level60></level60>
    <level70></level70>
    <level80></level80>
    <level90></level90>
    <level100></level100>
    <contents>
§ 1:1-1.1 Applicability; scope; special hearing rules

(a) Subject to any superseding Federal or State law, this chapter shall govern the procedural aspects...

(b) In the event of conflict between this chapter and any other agency rule...
    </contents>
</document>
```

### 5.2 Full File Structure
```xml
<?xml version="1.0" encoding="UTF-8"?>
<njacDocuments>
    <metadata>
        <source>New Jersey Administrative Code (N.J.A.C.)</source>
        <sourceURL>https://advance.lexis.com/container?config=...</sourceURL>
        <extractionDate>2026-05-07T10:30:00Z</extractionDate>
        <extractionTool>Playwright-Python</extractionTool>
        <totalDocuments>0</totalDocuments>
    </metadata>
    <documents>
        <document>
            <!-- Record 1 -->
        </document>
        <!-- More records ... -->
    </documents>
</njacDocuments>
```

---

## 6. COMPLETE PYTHON PLAYWRIGHT EXTRACTION SCRIPT

### 6.1 Main Extraction Script (extract_njac.py)

```python
#!/usr/bin/env python3
"""
New Jersey Administrative Code (N.J.A.C.) Extraction Tool
Extracts all Titles, Chapters, and Sections from LexisNexis
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

from playwright.async_api import async_playwright, Page, Browser
import xml.etree.ElementTree as ET
from xml.dom import minidom


# ============================================================================
# CONFIGURATION
# ============================================================================

LANDING_PAGE = 'https://advance.lexis.com/container?config=00JAA5OTY5MTdjZi1lMzYxLTQxNTEtOWFkNi0xMmU5ZTViODQ2M2MKAFBvZENhdGFsb2coFSYEAfv22IKqMT9DIHrf&crid=3f2f0aa3-f402-4b70-bcc5-939ff6217c31&prid=ae61b66e-a692-42a3-8599-4236c0739dca'

OUTPUT_DIR = Path('output')
OUTPUT_FILE = OUTPUT_DIR / 'njac_extracted.xml'
LOG_DIR = Path('logs')
LOG_FILE = LOG_DIR / 'extraction.log'

MAX_PAGES = 10000  # Safety limit
CAPTCHA_WAIT_TIME = 60  # seconds
PAGE_LOAD_TIMEOUT = 15000  # milliseconds
NAVIGATION_DELAY = 1000  # milliseconds between pages

# Create directories
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def escape_xml(text: str) -> str:
    """Escape XML special characters."""
    if not text:
        return ''
    
    text = str(text)
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;'
    }
    
    for char, escape_seq in replacements.items():
        text = text.replace(char, escape_seq)
    
    return text


def initialize_output_file():
    """Initialize XML output file with header."""
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
    OUTPUT_FILE.write_text(xml_header)
    logger.info(f"Initialized output file: {OUTPUT_FILE}")


def finalize_output_file(document_count: int):
    """Finalize XML output file with footer."""
    footer = """    </documents>
</njacDocuments>
"""
    
    # Read current content
    content = OUTPUT_FILE.read_text()
    
    # Update document count
    content = content.replace(
        '<totalDocuments>COUNTING...</totalDocuments>',
        f'<totalDocuments>{document_count}</totalDocuments>'
    )
    
    # Add footer
    content = content.rstrip() + '\n' + footer
    
    OUTPUT_FILE.write_text(content)
    logger.info(f"Finalized output file with {document_count} documents")


def build_xml_record(data: Dict[str, str]) -> str:
    """Build XML record from extracted data."""
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
    """Validate extracted record has required data."""
    required_fields = ['level10', 'level20', 'source_url', 'contents']
    
    for field in required_fields:
        if not record.get(field) or not str(record[field]).strip():
            logger.debug(f"Validation failed: missing or empty {field}")
            return False
    
    # Ensure contents has meaningful length
    if len(record['contents'].strip()) < 50:
        logger.debug(f"Validation failed: contents too short ({len(record['contents'])} chars)")
        return False
    
    return True


# ============================================================================
# PAGE EXTRACTION FUNCTIONS
# ============================================================================

async def extract_breadcrumb_data(page: Page) -> tuple[str, str, str]:
    """Extract Title, Chapter, Subchapter from breadcrumb navigation."""
    try:
        # Wait for breadcrumb links
        await page.wait_for_selector('a[href*="#"]', timeout=5000)
        
        # Get breadcrumb link texts - look for specific patterns
        level10 = ''
        level20 = ''
        level30 = ''
        
        # Try to find title text
        try:
            title_elem = await page.locator('text=/^TITLE \\d+\\./).first.text_content()
            level10 = title_elem.strip() if title_elem else ''
        except:
            pass
        
        # Try to find chapter text
        try:
            chapter_elem = await page.locator('text=/^CHAPTER \\d+\\./).first.text_content()
            level20 = chapter_elem.strip() if chapter_elem else ''
        except:
            pass
        
        # Try to find subchapter text
        try:
            subchapter_elem = await page.locator('text=/^SUBCHAPTER \\d+\\./).first.text_content()
            level30 = subchapter_elem.strip() if subchapter_elem else ''
        except:
            pass
        
        logger.debug(f"Breadcrumb extracted: Title='{level10[:50]}...', Chapter='{level20[:50]}...', Subchapter='{level30[:50]}...'")
        return level10, level20, level30
        
    except Exception as e:
        logger.warning(f"Error extracting breadcrumb: {e}")
        return '', '', ''


async def extract_section_header(page: Page) -> str:
    """Extract section header (§ X:X-X.X format)."""
    try:
        # Wait for header
        await page.wait_for_selector('h1, h2', timeout=5000)
        
        # Look for header with § symbol
        section_header = await page.locator('h1:has-text("§"), h2:has-text("§")').first.text_content()
        
        if section_header:
            header = section_header.strip()
            logger.debug(f"Section header extracted: {header[:80]}")
            return header
        
        # Fallback to any h1 or h2
        fallback = await page.locator('h1, h2').first.text_content()
        if fallback:
            return fallback.strip()
        
        return ''
        
    except Exception as e:
        logger.warning(f"Error extracting section header: {e}")
        return ''


async def extract_content_body(page: Page) -> str:
    """Extract full content including subsections."""
    try:
        # Wait for main content
        await page.wait_for_selector('main', timeout=5000)
        
        # Extract all text from main area
        content = await page.locator('main').text_content()
        
        if content:
            # Clean up excessive whitespace
            content = ' '.join(content.split())
            logger.debug(f"Content extracted: {len(content)} characters")
            return content
        
        return ''
        
    except Exception as e:
        logger.warning(f"Error extracting content body: {e}")
        return ''


async def extract_page_data(page: Page) -> Optional[Dict[str, str]]:
    """Extract all hierarchical data from current page."""
    try:
        logger.info(f"Extracting data from: {page.url}")
        
        # Extract hierarchical levels
        level10, level20, level30 = await extract_breadcrumb_data(page)
        level40 = await extract_section_header(page)
        contents = await extract_content_body(page)
        
        record = {
            'source_url': page.url,
            'level10': level10,
            'level20': level20,
            'level30': level30,
            'level40': level40,
            'level50': '',
            'level60': '',
            'level70': '',
            'level80': '',
            'level90': '',
            'level100': '',
            'contents': contents
        }
        
        return record
        
    except Exception as e:
        logger.error(f"Error extracting page data: {e}")
        return None


# ============================================================================
# NAVIGATION FUNCTIONS
# ============================================================================

async def check_for_captcha(page: Page) -> bool:
    """Check if CAPTCHA is present on page."""
    try:
        captcha_present = await page.locator('text=/CAPTCHA/i').is_visible()
        return captcha_present
    except:
        return False


async def navigate_to_first_content(page: Page) -> bool:
    """Navigate to first Chapter Notes page from TOC."""
    try:
        logger.info("Navigating to first content page...")
        
        # Look for "Title 1, Chapter 1 -- Chapter Notes" link
        chapter_notes = await page.locator('text=/Title \\d+, Chapter \\d+ -- Chapter Notes/').first
        
        if await chapter_notes.is_visible():
            await chapter_notes.click()
            await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)
            logger.info(f"Navigated to first Chapter Notes page: {page.url}")
            return True
        else:
            logger.warning("Chapter Notes link not found")
            return False
            
    except Exception as e:
        logger.error(f"Error navigating to first content: {e}")
        return False


async def has_next_page(page: Page) -> bool:
    """Check if Next button is available."""
    try:
        next_button = page.locator('a:has-text("Next")').first
        is_visible = await next_button.is_visible()
        return is_visible
    except:
        return False


async def navigate_to_next_page(page: Page, max_retries: int = 3) -> bool:
    """Click Next button and wait for page load."""
    for attempt in range(max_retries):
        try:
            # Check if Next button exists
            if not await has_next_page(page):
                logger.info("Next button not available. End of documents reached.")
                return False
            
            # Click Next button
            await page.locator('a:has-text("Next")').first.click()
            
            # Wait for page load
            await page.wait_for_load_state('networkidle', timeout=PAGE_LOAD_TIMEOUT)
            
            # Add delay to prevent rate limiting
            await asyncio.sleep(NAVIGATION_DELAY / 1000)
            
            logger.info(f"Successfully navigated to next page: {page.url}")
            return True
            
        except Exception as e:
            logger.warning(f"Navigation attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                logger.error("Failed to navigate to next page after retries")
                return False


# ============================================================================
# MAIN EXTRACTION LOOP
# ============================================================================

async def extract_all_documents(page: Page) -> int:
    """Main extraction loop - navigate through all pages and extract data."""
    document_count = 0
    page_count = 0
    
    has_more = True
    while has_more and page_count < MAX_PAGES:
        page_count += 1
        logger.info(f"Processing page {page_count}/{MAX_PAGES}...")
        
        try:
            # Extract data from current page
            data = await extract_page_data(page)
            
            # Validate and write record
            if data and validate_record(data):
                xml_record = build_xml_record(data)
                OUTPUT_FILE.write_text(OUTPUT_FILE.read_text() + xml_record)
                document_count += 1
                logger.info(f"✓ Extracted and saved document {document_count}")
            else:
                logger.warning(f"Page {page_count} validation failed, skipping")
            
            # Navigate to next page
            has_more = await navigate_to_next_page(page)
            
        except Exception as e:
            logger.error(f"Error in extraction loop at page {page_count}: {e}")
            # Try to recover
            await asyncio.sleep(2)
            continue
    
    logger.info(f"Extraction complete. Pages processed: {page_count}, Documents saved: {document_count}")
    return document_count


# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    """Main entry point."""
    logger.info("=" * 80)
    logger.info("Starting N.J.A.C. Extraction")
    logger.info("=" * 80)
    logger.info(f"Landing page: {LANDING_PAGE}")
    logger.info(f"Output file: {OUTPUT_FILE}")
    
    # Initialize output file
    initialize_output_file()
    
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False)  # Set to True for headless
        page = await browser.new_page()
        
        try:
            # Navigate to landing page
            logger.info("Navigating to landing page...")
            await page.goto(LANDING_PAGE, wait_until='networkidle')
            
            # Check for CAPTCHA
            if await check_for_captcha(page):
                logger.info("CAPTCHA detected! Please solve it manually.")
                logger.info(f"Waiting {CAPTCHA_WAIT_TIME} seconds for manual resolution...")
                await asyncio.sleep(CAPTCHA_WAIT_TIME)
                logger.info("Resuming extraction...")
            
            # Navigate to first content page
            success = await navigate_to_first_content(page)
            if not success:
                logger.warning("Could not find Chapter Notes link, attempting alternative approach")
            
            # Start main extraction loop
            document_count = await extract_all_documents(page)
            
            # Finalize output file
            finalize_output_file(document_count)
            
            logger.info("=" * 80)
            logger.info("EXTRACTION COMPLETE!")
            logger.info("=" * 80)
            logger.info(f"Total documents extracted: {document_count}")
            logger.info(f"Output file: {OUTPUT_FILE}")
            logger.info(f"Log file: {LOG_FILE}")
            
        except Exception as e:
            logger.error(f"Fatal error during extraction: {e}", exc_info=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
```

### 6.2 Configuration File (config.py)

```python
"""Configuration for N.J.A.C. extraction."""

from pathlib import Path
from datetime import datetime


class Config:
    """Main configuration class."""
    
    # URLs
    LANDING_PAGE = 'https://advance.lexis.com/container?config=00JAA5OTY5MTdjZi1lMzYxLTQxNTEtOWFkNi0xMmU5ZTViODQ2M2MKAFBvZENhdGFsb2coFSYEAfv22IKqMT9DIHrf&crid=3f2f0aa3-f402-4b70-bcc5-939ff6217c31&prid=ae61b66e-a692-42a3-8599-4236c0739dca'
    
    # File paths
    OUTPUT_DIR = Path('output')
    LOG_DIR = Path('logs')
    OUTPUT_FILE = OUTPUT_DIR / 'njac_extracted.xml'
    LOG_FILE = LOG_DIR / f'extraction_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    
    # Timeouts (milliseconds)
    PAGE_LOAD_TIMEOUT = 15000
    SELECTOR_WAIT_TIMEOUT = 5000
    NAVIGATION_DELAY = 1000  # Delay between page transitions
    
    # Limits
    MAX_PAGES = 10000
    CAPTCHA_WAIT_TIME = 60  # seconds
    
    # Browser settings
    HEADLESS = False  # Set to True for headless mode
    SLOW_MO = 100  # Slow down actions by Nms (0 for no slowdown)
    
    # Logging
    LOG_LEVEL = 'INFO'
    
    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    
    @classmethod
    def setup_directories(cls):
        """Create required directories."""
        cls.OUTPUT_DIR.mkdir(exist_ok=True)
        cls.LOG_DIR.mkdir(exist_ok=True)
```

### 6.3 Utility Functions (utils.py)

```python
"""Utility functions for N.J.A.C. extraction."""

import logging
from typing import Optional
from playwright.async_api import Page


logger = logging.getLogger(__name__)


class XMLUtils:
    """XML-related utility functions."""
    
    @staticmethod
    def escape_xml(text: str) -> str:
        """Escape XML special characters."""
        if not text:
            return ''
        
        text = str(text)
        replacements = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&apos;'
        }
        
        for char, escape_seq in replacements.items():
            text = text.replace(char, escape_seq)
        
        return text
    
    @staticmethod
    def unescape_xml(text: str) -> str:
        """Unescape XML special characters."""
        if not text:
            return ''
        
        text = str(text)
        replacements = {
            '&amp;': '&',
            '&lt;': '<',
            '&gt;': '>',
            '&quot;': '"',
            '&apos;': "'"
        }
        
        for escape_seq, char in replacements.items():
            text = text.replace(escape_seq, char)
        
        return text


class PageUtils:
    """Page interaction utility functions."""
    
    @staticmethod
    async def wait_for_selector_safe(
        page: Page,
        selector: str,
        timeout: int = 5000
    ) -> bool:
        """Safely wait for selector with error handling."""
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"Selector '{selector}' not found: {e}")
            return False
    
    @staticmethod
    async def get_text_safe(
        page: Page,
        selector: str,
        default: str = ''
    ) -> str:
        """Safely extract text from element."""
        try:
            element = page.locator(selector).first
            if await element.is_visible():
                return await element.text_content()
        except Exception as e:
            logger.debug(f"Error getting text from '{selector}': {e}")
        
        return default
    
    @staticmethod
    async def click_safe(
        page: Page,
        selector: str,
        max_retries: int = 3
    ) -> bool:
        """Safely click element with retry logic."""
        for attempt in range(max_retries):
            try:
                await page.click(selector)
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Click failed, retrying ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                else:
                    logger.warning(f"Failed to click '{selector}' after {max_retries} attempts")
                    return False
        
        return False


class StringUtils:
    """String processing utilities."""
    
    @staticmethod
    def clean_whitespace(text: str) -> str:
        """Remove excessive whitespace."""
        if not text:
            return ''
        
        # Replace multiple spaces with single space
        text = ' '.join(text.split())
        return text
    
    @staticmethod
    def truncate(text: str, length: int = 100, suffix: str = '...') -> str:
        """Truncate text to specified length."""
        if len(text) <= length:
            return text
        return text[:length - len(suffix)] + suffix
    
    @staticmethod
    def validate_section_number(text: str) -> bool:
        """Check if text contains valid section number (§ format)."""
        return '§' in text and ':' in text
```

### 6.4 Data Extraction Module (extractors.py)

```python
"""Data extraction functions for N.J.A.C."""

import logging
from typing import Dict, Optional
from playwright.async_api import Page
from utils import PageUtils, StringUtils


logger = logging.getLogger(__name__)


class NJACExtractor:
    """Main extractor class for N.J.A.C. data."""
    
    def __init__(self):
        self.page_count = 0
        self.doc_count = 0
    
    async def extract_page_data(self, page: Page) -> Optional[Dict[str, str]]:
        """Extract all hierarchical data from current page."""
        try:
            logger.info(f"Extracting data from: {page.url}")
            
            # Extract hierarchical levels
            level10 = await self._extract_title(page)
            level20 = await self._extract_chapter(page)
            level30 = await self._extract_subchapter(page)
            level40 = await self._extract_section_header(page)
            contents = await self._extract_content(page)
            
            record = {
                'source_url': page.url,
                'level10': level10,
                'level20': level20,
                'level30': level30,
                'level40': level40,
                'level50': '',
                'level60': '',
                'level70': '',
                'level80': '',
                'level90': '',
                'level100': '',
                'contents': contents
            }
            
            self.page_count += 1
            return record
            
        except Exception as e:
            logger.error(f"Error extracting page data: {e}")
            return None
    
    async def _extract_title(self, page: Page) -> str:
        """Extract TITLE level from page."""
        try:
            # Use locator with regex pattern
            title = await page.locator('text=/^TITLE \\d+\\./).first.text_content()
            return title.strip() if title else ''
        except:
            return ''
    
    async def _extract_chapter(self, page: Page) -> str:
        """Extract CHAPTER level from page."""
        try:
            chapter = await page.locator('text=/^CHAPTER \\d+\\./).first.text_content()
            return chapter.strip() if chapter else ''
        except:
            return ''
    
    async def _extract_subchapter(self, page: Page) -> str:
        """Extract SUBCHAPTER level from page."""
        try:
            subchapter = await page.locator('text=/^SUBCHAPTER \\d+\\./).first.text_content()
            return subchapter.strip() if subchapter else ''
        except:
            return ''
    
    async def _extract_section_header(self, page: Page) -> str:
        """Extract section header (§ format) from page."""
        try:
            # Wait for header
            await page.wait_for_selector('h1, h2', timeout=5000)
            
            # Look for § symbol
            section = await page.locator('h1:has-text("§"), h2:has-text("§")').first.text_content()
            
            if section:
                return section.strip()
            
            # Fallback to first h1/h2
            fallback = await page.locator('h1, h2').first.text_content()
            return fallback.strip() if fallback else ''
            
        except Exception as e:
            logger.debug(f"Error extracting section header: {e}")
            return ''
    
    async def _extract_content(self, page: Page) -> str:
        """Extract full content from page."""
        try:
            # Wait for main content
            await page.wait_for_selector('main', timeout=5000)
            
            # Extract text
            content = await page.locator('main').text_content()
            
            if content:
                # Clean whitespace
                return StringUtils.clean_whitespace(content)
            
            return ''
            
        except Exception as e:
            logger.debug(f"Error extracting content: {e}")
            return ''
    
    def validate_record(self, record: Dict[str, str]) -> bool:
        """Validate extracted record."""
        required_fields = ['level10', 'level20', 'source_url', 'contents']
        
        for field in required_fields:
            if not record.get(field) or not str(record[field]).strip():
                logger