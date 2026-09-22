#!/usr/bin/env python3
"""
Build a static preview of the CUNY Math Seminars site.

The preview is self-contained: the seminar JSON is inlined into
Preview/index.html, so you can open it directly with a browser
(file:// URL) without running a server.

Usage:
    python dev.py              # build Preview/ from existing data
    python dev.py --scrape     # run scraper first, then build Preview/
    python dev.py --open       # build and open Preview/index.html in browser
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREVIEW = ROOT / "Preview"
DATA_FILE = ROOT / "data" / "seminars.json"


def run_scraper() -> bool:
    print("▶ Running scraper.py …")
    result = subprocess.run([sys.executable, str(ROOT / "scraper.py")])
    if result.returncode != 0:
        print("✖ Scraper failed. Continuing with existing data.", file=sys.stderr)
        return False
    print("✔ Scraper finished.")
    return True


def load_data() -> dict:
    if not DATA_FILE.exists():
        print(f"⚠  {DATA_FILE.relative_to(ROOT)} not found.")
        return {"last_updated": None, "seminars": []}
    try:
        with DATA_FILE.open(encoding="utf-8") as f:
            payload = json.load(f)
        n = len(payload.get("seminars", []))
        updated = payload.get("last_updated") or "unknown"
        print(f"ℹ  Loaded {n} seminars (last updated: {updated}).")
        return payload
    except Exception as exc:
        print(f"⚠  Could not parse {DATA_FILE.name}: {exc}")
        return {"last_updated": None, "seminars": []}


def build_preview(payload: dict) -> None:
    """Copy site files into Preview/ and inline the JSON data."""
    if PREVIEW.exists():
        shutil.rmtree(PREVIEW)
    PREVIEW.mkdir(parents=True)

    # Copy CSS and JS as-is
    for name in ["style.css", "script.js"]:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, PREVIEW / name)
        else:
            print(f"⚠  Missing {name}")

    # Read index.html and inject the JSON payload before </head>
    index_src = ROOT / "index.html"
    if not index_src.exists():
        print("⚠  Missing index.html")
        return

    html = index_src.read_text(encoding="utf-8")
    json_text = json.dumps(payload, ensure_ascii=False)
    inline = (
        f"<script>window.__SEMINARS_DATA__ = {json_text};</script>\n"
    )
    if "</head>" in html:
        html = html.replace("</head>", inline + "</head>", 1)
    else:
        html = inline + html

    (PREVIEW / "index.html").write_text(html, encoding="utf-8")

    # Also copy the raw JSON for reference (harmless if unused)
    (PREVIEW / "data").mkdir(exist_ok=True)
    (PREVIEW / "data" / "seminars.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    size_kb = (PREVIEW / "index.html").stat().st_size / 1024
    print(f"✔ Preview built at {PREVIEW}  (index.html: {size_kb:.1f} KB)")
    print(f"  Open {PREVIEW / 'index.html'} in your browser.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scrape", action="store_true",
                        help="Run scraper.py before building preview")
    parser.add_argument("--open", action="store_true",
                        help="Open the preview in your browser")
    args = parser.parse_args()

    os.chdir(ROOT)

    if args.scrape:
        run_scraper()

    payload = load_data()
    build_preview(payload)

    if args.open:
        webbrowser.open((PREVIEW / "index.html").as_uri())

    return 0


if __name__ == "__main__":
    sys.exit(main())
