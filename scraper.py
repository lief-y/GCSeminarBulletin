#!/usr/bin/env python3
"""
CUNY Mathematics Seminars scraper (async, parallel, strict validation).

Main page → seminar list (day, name, time, room, website).
Each seminar website → speaker, title, abstract (only if valid).
Outputs data/seminars.json.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

BASE_URL = "https://www.gc.cuny.edu/mathematics/seminars-and-events/seminars"
OUTPUT_PATH = os.path.join("data", "seminars.json")
DEBUG_DUMP = os.path.join("data", "debug_page.txt")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MAX_PARALLEL = 5
MAIN_PAGE_WAIT_MS = 3000
DETAIL_WAIT_MS = 1500
DETAIL_TIMEOUT_MS = 20000

# ---------------------------------------------------------------------------
# Block / junk detection
# ---------------------------------------------------------------------------

BLOCK_MARKERS = [
    "you have been blocked",
    "cloudflare ray id",
    "attention required",
    "checking your browser",
    "enable javascript and cookies",
    "just a moment...",
    "access denied",
    "this site can't be reached",
    "domain is for sale",
]


def is_blocked_page(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in BLOCK_MARKERS)


# ---------------------------------------------------------------------------
# Step 1: Extract seminar list from the main CUNY page
# ---------------------------------------------------------------------------

EXTRACT_JS_LINKS = r"""
() => {
    const timeRe = /\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)/;
    const roomRe = /(Room\s+[A-Za-z0-9.\-]+|Online|Hybrid|Hyflex)/i;
    const dayRe = /^(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)(\s+SEMINARS?)?$/i;

    const seminars = [];
    let currentDay = null;

    for (const el of document.querySelectorAll('*')) {
        const fullText = el.textContent.trim();

        if (/^H[1-6]$/.test(el.tagName) && dayRe.test(fullText)) {
            currentDay = fullText.replace(/\s+SEMINARS?$/i, '').trim();
            currentDay = currentDay.charAt(0).toUpperCase() + currentDay.slice(1).toLowerCase();
            continue;
        }
        if (!currentDay) continue;

        if (el.querySelector('a[href]') && el.querySelectorAll('*').length > 3) {
            if (fullText.length > 400) continue;
        }
        if (!timeRe.test(fullText)) continue;

        const link = el.querySelector('a[href]');
        if (!link) continue;

        const url = link.href;
        const name = link.textContent.trim();
        if (!name) continue;

        const tMatch = fullText.match(
            /(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\s*[–-]\s*\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))/i
        );
        const time = tMatch ? tMatch[1] : '';
        const rMatch = fullText.match(roomRe);
        const room = rMatch ? rMatch[1] : '';

        seminars.push({ day: currentDay, name, time, room, website: url });
    }

    const seen = new Set();
    return seminars.filter(s => {
        const k = `${s.day}|${s.name}|${s.time}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
    });
}
"""


async def extract_seminars(page):
    try:
        result = await page.evaluate(EXTRACT_JS_LINKS)
        return result or []
    except Exception as e:
        print(f"  Extraction failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

SPEAKER_BAD = re.compile(
    r"(cloudflare|affiliation|mailing list|abstract|"
    r"we will|i will|will be sent|please email|please contact|"
    r"you do not need|no seminar|tba|previous semester|"
    r"location:|room:|title:|speaker:)",
    re.IGNORECASE,
)

TITLE_BAD = re.compile(
    r"(mailing list|please email|will be sent|previous semester|"
    r"click here|for more information|you do not need|"
    r"please contact|affiliation|location:|room:|speaker:|"
    r"^abstract:?$|^here\?$|^and$|^of a talk|^tba$)",
    re.IGNORECASE,
)

ABSTRACT_BAD = re.compile(
    r"(mailing list|previous semester|click here|you do not need to register|"
    r"please contact the organizers|please email neil katz|"
    r"location:\s*thesis room|access google drive)",
    re.IGNORECASE,
)

MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
    re.IGNORECASE,
)


def is_valid_speaker(text: str) -> bool:
    if not text:
        return False
    text = text.strip().rstrip(".")
    if not (3 <= len(text) <= 100):
        return False
    if SPEAKER_BAD.search(text):
        return False
    words = text.split()
    if not (1 < len(words) <= 8):
        return False
    if not text[0].isupper():
        return False
    # Reject sentence-like text
    preps = len(re.findall(r"\b(and|or|the|of|to|in|a|an|with|by)\b", text, re.I))
    if preps > 2:
        return False
    # Reject if any word (besides first) is all lowercase — sign of a fragment
    if any(w.islower() and len(w) > 3 for w in words[1:]):
        # Allow lowercase particles like "de", "van", "von"
        allowed = {"de", "van", "von", "di", "da", "la", "le", "del"}
        if not all(w.lower() in allowed for w in words[1:] if w.islower()):
            return False
    return True


def is_valid_title(text: str) -> bool:
    if not text:
        return False
    text = text.strip()
    if not (8 <= len(text) <= 300):
        return False
    if TITLE_BAD.search(text):
        return False
    if not text[0].isupper() and not text[0].isdigit():
        return False
    # Reject schedule-like strings (3+ month names)
    if len(MONTH_RE.findall(text)) >= 3:
        return False
    # Reject URLs
    if "http" in text.lower():
        return False
    return True


def is_valid_abstract(text: str) -> bool:
    if not text:
        return False
    text = text.strip()
    if not (80 <= len(text) <= 6000):
        return False
    if ABSTRACT_BAD.search(text):
        return False
    # Reject schedule listings (2+ "Month Day" patterns)
    if len(re.findall(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
        text,
    )) >= 2:
        return False
    return True


def clean(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def clean_room(room: str) -> str:
    """Remove label bleed like 'Room 4102Meets' → 'Room 4102'."""
    if not room:
        return ""
    room = re.sub(r"Meets.*$", "", room, flags=re.IGNORECASE).strip()
    room = re.sub(r"\s+", " ", room)
    return room


# ---------------------------------------------------------------------------
# Step 2: Website enrichment
# ---------------------------------------------------------------------------

SPEAKER_SELECTORS = [
    "[class*='speaker' i]", "[id*='speaker' i]",
    "[class*='Speaker']",
]

TITLE_SELECTORS = [
    "[class*='talk-title']", "[class*='talkTitle']",
    "[class*='title' i]:not(nav):not(header):not(footer)",
]

ABSTRACT_SELECTORS = [
    "section#abstract", "div.abstract", "p.abstract",
    "div[class*='abstract' i]", "p[class*='abstract' i]",
    "div[class*='Abstract']",
]


async def _query_first_valid(page, selectors, validator):
    for sel in selectors:
        try:
            for el in await page.query_selector_all(sel):
                txt = clean(await el.inner_text())
                if txt and validator(txt):
                    return txt
        except Exception:
            continue
    return ""


async def _label_walk(page, label: str, stop_labels: list) -> str:
    """Find text after 'Label:' in the page, stopping at the next label."""
    js = r"""
    ([label, stop]) => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const regex = new RegExp(label + "\\s*[:\\s]\\s*(.+?)(?=\\n|$)", "i");
        const stopRe = stop.length
            ? new RegExp("(?:" + stop.join("|") + ")\\s*[:\\s]", "i")
            : null;
        while (walker.nextNode()) {
            const t = walker.currentNode.textContent.trim();
            if (!t) continue;
            const m = t.match(regex);
            if (m) {
                let result = m[1].trim();
                if (stopRe) {
                    const s = result.search(stopRe);
                    if (s > 0) result = result.slice(0, s).trim();
                }
                return result.slice(0, 500);
            }
        }
        return "";
    }
    """
    try:
        return clean(await page.evaluate(js, [label, stop_labels]))
    except Exception:
        return ""


async def extract_from_website(page, seminar):
    url = seminar.get("website") or ""
    if not url.startswith("http"):
        return
    if url.lower().endswith(".pdf"):
        return

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
        await page.wait_for_timeout(DETAIL_WAIT_MS)

        try:
            body = await page.inner_text("body")
        except Exception:
            return

        if is_blocked_page(body):
            print(f"    ⊘ blocked: {seminar['name'][:60]}")
            return

        # ----- Speaker -----
        if not seminar.get("speaker"):
            speaker = await _query_first_valid(page, SPEAKER_SELECTORS, is_valid_speaker)
            if not speaker:
                speaker = await _label_walk(page, "Speaker[s]?", ["Title", "Abstract", "Location", "Room"])
            if speaker and is_valid_speaker(speaker):
                seminar["speaker"] = speaker

        # ----- Title -----
        if not seminar.get("title"):
            title = await _query_first_valid(page, TITLE_SELECTORS, is_valid_title)
            if not title:
                title = await _label_walk(page, "Title", ["Abstract", "Speaker", "Location"])
            if title and is_valid_title(title):
                seminar["title"] = title

        # ----- Abstract -----
        if not seminar.get("abstract"):
            abstract = await _query_first_valid(page, ABSTRACT_SELECTORS, is_valid_abstract)
            if not abstract:
                abstract = await _label_walk(page, "Abstract", ["Speaker", "Title", "Location", "References"])
            if abstract and is_valid_abstract(abstract):
                seminar["abstract"] = abstract

        status = "✓" if any(seminar.get(k) for k in ("speaker", "title", "abstract")) else "·"
        print(f"    {status} {seminar['name'][:60]}")

    except PWTimeout:
        print(f"    ⌛ timeout: {seminar['name'][:60]}")
    except Exception as e:
        print(f"    ✗ {seminar['name'][:60]}: {e}")


# ---------------------------------------------------------------------------
# Resource blocking
# ---------------------------------------------------------------------------

BLOCKED_TYPES = {"image", "media", "font", "stylesheet"}


async def block_resources(route):
    try:
        if route.request.resource_type in BLOCKED_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        try:
            await route.continue_()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedupe(seminars):
    seen = set()
    out = []
    for s in seminars:
        key = (
            s.get("day", ""),
            s.get("name", ""),
            re.sub(r"\s+", "", s.get("time", "")).lower(),
            s.get("speaker", ""),
            s.get("title", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run():
    os.makedirs("data", exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        await Stealth().apply_stealth_async(context)
        await context.route("**/*", block_resources)

        page = await context.new_page()
        print(f"Loading {BASE_URL}")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        except PWTimeout:
            print("  timeout on main page")
        await page.wait_for_timeout(MAIN_PAGE_WAIT_MS)

        try:
            body_text = await page.inner_text("body")
        except Exception:
            body_text = ""

        if is_blocked_page(body_text):
            print("✖ Cloudflare block on main page.")
            await browser.close()
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "seminars": [],
                    "error": "cloudflare_block",
                }, f, indent=2, ensure_ascii=False)
            return

        print("  Extracting seminars…")
        seminars = await extract_seminars(page)
        print(f"  Found {len(seminars)} seminars")
        await page.close()

        # Clean rooms and filter out PDF-only / malformed sites
        for s in seminars:
            s["room"] = clean_room(s.get("room", ""))

        if seminars:
            print(f"  Enriching {len(seminars)} seminars (max {MAX_PARALLEL} parallel)…")
            sem = asyncio.Semaphore(MAX_PARALLEL)

            async def enrich(s):
                async with sem:
                    page2 = await context.new_page()
                    try:
                        await extract_from_website(page2, s)
                    finally:
                        await page2.close()

            await asyncio.gather(*(enrich(s) for s in seminars))

        seminars = dedupe(seminars)

        for s in seminars:
            s.setdefault("speaker", "")
            s.setdefault("title", "")
            s.setdefault("abstract", None)
            s.setdefault("location", s.get("room", ""))

        payload = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "seminars": seminars,
        }
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Wrote {OUTPUT_PATH} ({len(seminars)} seminars)")

        await browser.close()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        sys.exit(1)
