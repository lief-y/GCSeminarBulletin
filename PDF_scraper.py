#!/usr/bin/env python3
"""
CUNY Mathematics Seminars scraper (PDF-based).

Reads the weekly bulletin PDF at data/bulletin.pdf and produces
data/seminars.json.

The user replaces data/bulletin.pdf each week with the latest bulletin.

Usage:
    python PDF_scraper.py                # parse PDF only (fast, clean)
    python PDF_scraper.py --enrich       # also fetch abstracts from websites
    python PDF_scraper.py --pdf PATH     # use a different PDF file
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import pdfplumber

PDF_PATH = os.path.join("data", "bulletin.pdf")
OUTPUT_PATH = os.path.join("data", "seminars.json")

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

DATE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(\d{1,2})",
    re.IGNORECASE,
)

TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\s*[–\-—]\s*\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))",
    re.IGNORECASE,
)

ROOM_RE = re.compile(
    r"((?:Room|Rm\.?)\s+[A-Za-z0-9.\-]+"
    r"|Online"
    r"|Hyflex\s*(?:[,;]\s*)?(?:Room\s+[A-Za-z0-9.\-]+)?"
    r"|Hybrid\s*(?:[,;]\s*)?(?:Room\s+[A-Za-z0-9.\-]+)?)",
    re.IGNORECASE,
)

URL_START_RE = re.compile(r"https?://", re.IGNORECASE)
URL_BODY_RE = re.compile(
    r"https?://[a-zA-Z0-9.\-]+(?::\d+)?"
    r"(?:[/?#][a-zA-Z0-9.\-/_~%+@!$&'()*;=:#\[\]]*)?",
    re.IGNORECASE,
)

NA_RE = re.compile(r"^\s*n/?a\.?\s*$", re.IGNORECASE)

STATUS_RE = re.compile(
    r"^(not\s+meeting|no\s+seminar|no\s+classes|"
    r"cancelled|canceled|tba|seminar\s+starts|"
    r"will\s+not\s+meet|does\s+not\s+meet)",
    re.IGNORECASE,
)

HONORIFIC_RE = re.compile(
    r"^((?:Prof\.|Professor|Dr\.|Mr\.|Ms\.|Mrs\.)\s+)"
)

# Name pieces: "Emma", "R.", "O'Bryant", "Blum-Smith", "Éamonn", "Yoon-Joo"
NAME_WORD = r"[A-ZÀ-Ý][a-zA-Zà-ÿ\-']*\.?"
INITIAL_WORD = r"[A-ZÀ-Ý]\."

INSTITUTION_WORDS = {
    "university", "college", "institute", "school", "academy",
    "center", "centre", "laboratory", "faculty", "department",
    "polytechnic", "conservatory", "seminary", "scholar",
    "professor", "institution",
}

LOWERCASE_CONNECTORS = {
    "of", "and", "the", "in", "on", "for", "to", "with", "at",
    "from", "by", "or", "a", "an",
}

# Words that shouldn't begin a title when the "name" is a single word
TITLE_STARTERS = {
    "introduction", "introducing", "notes", "towards", "toward",
    "some", "new", "recent", "about", "from", "into",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clean(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def is_na(s: str) -> bool:
    return bool(NA_RE.match(clean(s)))


def infer_year(month_name: str) -> int:
    now = datetime.now()
    try:
        month_num = datetime.strptime(month_name[:3], "%b").month
    except Exception:
        return now.year
    if month_num + 6 < now.month:
        return now.year + 1
    return now.year


def parse_date_header(text: str):
    m = DATE_RE.search(text)
    if not m:
        return None, None
    day_name, month_name, day_num = m.groups()
    year = infer_year(month_name)
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(f"{month_name} {day_num} {year}").date()
        return day_name.capitalize(), dt.isoformat()
    except Exception:
        return day_name.capitalize(), None


def extract_url(text: str) -> str:
    """Extract a URL from a PDF cell, undoing line-wrap spaces."""
    if not text:
        return ""
    m = URL_START_RE.search(text)
    if not m:
        return ""
    rest = text[m.start():]
    rest = re.sub(r"\s+", "", rest)  # collapse line-wraps inside the URL
    m2 = URL_BODY_RE.match(rest)
    if not m2:
        return ""
    return m2.group(0).rstrip(".,;:)")


# ---------------------------------------------------------------------------
# Speaker / Title splitting
# ---------------------------------------------------------------------------


def _validate(name: str, title: str) -> bool:
    """Return True if a candidate (name, title) split looks reasonable."""
    if not title:
        # Empty title is fine for multi-word names ("Maciej Grześkowiak")
        return " " in name

    first_word_orig = title.split()[0]
    first_word = first_word_orig.lower().rstrip(".,:;!?")

    # Title can't start with a lowercase connector like "of", "and", "in"
    if first_word_orig[0].islower() and first_word in LOWERCASE_CONNECTORS:
        return False

    # 1-word names must produce a substantive title
    if " " not in name:
        if len(title.split()) < 2:
            return False
        if first_word in TITLE_STARTERS:
            return False
        if not (title[0].isupper() or title[0].isdigit()):
            return False

    return True


def _split_affil_and_title(rest: str):
    """
    Given the text after 'Name, ' split into (affiliation, title).

    Heuristic: affiliation ends at an institution keyword (University,
    College, Institute, ...), and any immediately-following 'of X' or
    parenthetical "(...)" is part of it. The rest is the title.
    """
    tokens = re.findall(r"\([^)]*\)|[^\s,()]+", rest)
    affil_words = []
    i = 0
    seen_inst = False

    while i < len(tokens):
        tk = tokens[i]

        if tk.startswith("("):
            affil_words.append(tk)
            i += 1
            if seen_inst:
                break
            continue

        # Stop at first lowercase non-connector → that's the title
        if not tk[:1].isupper() and tk.lower() not in (
            "of", "the", "at", "in", "and", "for", "&",
        ):
            break

        affil_words.append(tk)
        i += 1

        if tk.lower().rstrip(".") in INSTITUTION_WORDS:
            seen_inst = True
            # If a ")" appears within the next few tokens, include it
            for j in range(i, min(i + 5, len(tokens))):
                if ")" in tokens[j]:
                    affil_words.extend(tokens[i:j + 1])
                    i = j + 1
                    break
            else:
                # Otherwise just include one connector ("of X")
                if i < len(tokens) and tokens[i].lower() in (
                    "of", "the", "at", "in", "and", "for",
                ):
                    affil_words.append(tokens[i])
                    i += 1
            break

    affil = " ".join(affil_words)
    title = " ".join(tokens[i:])
    return affil, title


def split_speaker_title(raw: str):
    """
    Split a 'Speaker & Title' cell into (speaker, title).

    - NA / status messages (e.g. "Not meeting this week.") are preserved
      verbatim in the title slot.
    - Names may include middle initials ("Emma R. Hasson"), honorifics
      ("Prof. Yueqiao Wu"), parenthetical affiliations, and comma
      affiliations ("Kevin O'Bryant, College of Staten Island (CUNY)").
    - When the split fails, the raw value is preserved in the title slot
      so nothing is lost.
    """
    text = clean(raw)
    if not text:
        return "", ""

    if is_na(text) or STATUS_RE.match(text):
        return "", text

    prefix = ""
    m = HONORIFIC_RE.match(text)
    if m:
        prefix = m.group(1)
        text = text[m.end():]

    # Try longest, most specific patterns first
    name_patterns = [
        rf"{NAME_WORD}\s+{INITIAL_WORD}\s+{NAME_WORD}",  # Emma R. Hasson
        rf"{NAME_WORD}\s+{NAME_WORD}",                    # Sridhar Ramesh
        rf"{NAME_WORD}",                                   # Wuxuan
    ]

    for pattern in name_patterns:
        # (1) Comma-affiliation form: "Name, Affiliation Title"
        m = re.match(
            rf"^({pattern})(\s*\([^)]+\))?,\s*(.+)$",
            text,
        )
        if m:
            name = m.group(1)
            paren = m.group(2) or ""
            rest = m.group(3).strip()
            affil, title = _split_affil_and_title(rest)
            speaker = prefix + name + paren
            if affil:
                speaker += ", " + affil
            if _validate(name, title):
                return speaker.strip(), title.strip()

        # (2) Non-comma form: "Name (Affiliation) Title"
        m = re.match(
            rf"^({pattern})(\s*\([^)]+\))?(?:\s+(.+))?$",
            text,
        )
        if m:
            name = m.group(1)
            paren = m.group(2) or ""
            title = (m.group(3) or "").strip()
            speaker = (prefix + name + paren).strip()
            if _validate(name, title):
                return speaker, title

    # Nothing worked — preserve the raw value
    return "", text


# ---------------------------------------------------------------------------
# Table row parsing
# ---------------------------------------------------------------------------


def parse_seminar_row(cells, current_date, current_day):
    if not cells:
        return None

    time_loc = clean(cells[0]) if len(cells) > 0 else ""
    seminar_name = clean(cells[1]) if len(cells) > 1 else ""
    speaker_title = clean(cells[2]) if len(cells) > 2 else ""
    website_field = clean(cells[3]) if len(cells) > 3 else ""

    low = time_loc.lower()
    if "time" in low and "location" in low:
        return None
    if not time_loc and not seminar_name:
        return None

    time_match = TIME_RE.search(time_loc)
    time_str = time_match.group(1) if time_match else ""

    room_match = ROOM_RE.search(time_loc)
    room_str = clean(room_match.group(1)) if room_match else ""

    website = (
        extract_url(website_field)
        or extract_url(speaker_title)
        or extract_url(time_loc)
    )

    speaker, title = split_speaker_title(speaker_title)

    return {
        "date": current_date,
        "day": current_day,
        "time": time_str,
        "room": room_str,
        "location": room_str,
        "seminar": seminar_name,
        "speaker": speaker,
        "title": title,
        "speaker_title_raw": speaker_title,
        "website": website,
        "abstract": None,
    }


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------


def parse_pdf(pdf_path: str):
    seminars = []
    current_date = None
    current_day = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""

            d, dt = parse_date_header(page_text)
            if d and dt:
                current_day, current_date = d, dt

            try:
                tables = page.extract_tables()
            except Exception:
                tables = []

            for table in tables:
                for row in table:
                    if not row:
                        continue
                    cells = [clean(c) for c in row]
                    non_empty = [c for c in cells if c]
                    if not non_empty:
                        continue

                    if len(non_empty) <= 1:
                        d2, dt2 = parse_date_header(non_empty[0])
                        if d2 and dt2:
                            current_day, current_date = d2, dt2
                        continue

                    d2, dt2 = parse_date_header(non_empty[0])
                    if d2 and dt2 and len(non_empty) <= 2:
                        current_day, current_date = d2, dt2
                        continue

                    entry = parse_seminar_row(cells, current_date, current_day)
                    if entry:
                        seminars.append(entry)

    seen = set()
    unique = []
    for s in seminars:
        key = (
            s.get("date"),
            s.get("time"),
            s.get("seminar"),
            s.get("speaker_title_raw"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


# ---------------------------------------------------------------------------
# Optional: website enrichment for abstracts (unchanged)
# ---------------------------------------------------------------------------


async def _enrich_one(context, seminar, semaphore):
    from playwright.async_api import TimeoutError as PWTimeout

    url = seminar.get("website") or ""
    if not url.startswith("http") or url.lower().endswith(".pdf"):
        return

    async with semaphore:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(800)
            try:
                body = await page.inner_text("body")
            except Exception:
                return
            if re.search(
                r"cloudflare|you have been blocked|attention required",
                body, re.IGNORECASE,
            ):
                return
            m = re.search(
                r"Abstract[:\s]*\n?(.{100,3000}?)(?:\n\s*\n|$)",
                body, re.IGNORECASE | re.DOTALL,
            )
            if m:
                abstract = clean(m.group(1))
                if 80 <= len(abstract) <= 4000:
                    seminar["abstract"] = abstract
        except PWTimeout:
            pass
        except Exception:
            pass
        finally:
            await page.close()


async def enrich_with_abstracts(seminars):
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await Stealth().apply_stealth_async(context)

        async def block_resources(route):
            try:
                if route.request.resource_type in {"image", "media", "font", "stylesheet"}:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await context.route("**/*", block_resources)
        sem = asyncio.Semaphore(5)
        await asyncio.gather(*(_enrich_one(context, s, sem) for s in seminars))
        await browser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pdf", default=PDF_PATH,
                        help=f"Path to the bulletin PDF (default: {PDF_PATH})")
    parser.add_argument("--enrich", action="store_true",
                        help="Also fetch abstracts from seminar websites (slow)")
    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)

    if not os.path.exists(args.pdf):
        print(f"✖ PDF not found: {args.pdf}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {args.pdf} …")
    seminars = parse_pdf(args.pdf)
    print(f"  Parsed {len(seminars)} seminar entries")

    with_url = sum(1 for s in seminars if s.get("website"))
    with_speaker = sum(1 for s in seminars if s.get("speaker"))
    with_title = sum(1 for s in seminars if s.get("title"))
    print(f"  {with_url} with website, {with_speaker} with speaker, "
          f"{with_title} with title")

    if args.enrich and seminars:
        print("  Fetching abstracts from seminar websites …")
        try:
            asyncio.run(enrich_with_abstracts(seminars))
            filled = sum(1 for s in seminars if s.get("abstract"))
            print(f"  {filled} abstracts retrieved")
        except Exception as exc:
            print(f"  ⚠ Enrichment failed: {exc}", file=sys.stderr)

    payload = {
        "last_updated": datetime.now(timezone.utc).strftime("%B %d, %Y"),
        "source": os.path.basename(args.pdf),
        "seminars": seminars,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        sys.exit(1)
