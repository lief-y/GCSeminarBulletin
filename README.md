# CUNY Math Seminars Website

A static site that displays CUNY Graduate Center mathematics seminars
in a two-week timetable. Data is scraped from
https://www.gc.cuny.edu/mathematics/seminars-and-events/seminars
by a Playwright-based Python script and refreshed automatically by
GitHub Actions every Monday at noon ET.

## Structure

```
.
├── .github/workflows/scrape.yml   # Scheduled scraping + commit
├── data/seminars.json             # Generated data consumed by the site
├── scraper.py                     # Scraper (Playwright + BeautifulSoup)
├── dev.py                         # Local dev server helper
├── index.html / style.css / script.js
├── requirements.txt
└── README.md
```

## Local development

Install dependencies once:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### Quick preview (existing data)

```bash
python dev.py
```

Opens `http://localhost:8000/` in your browser. Stops with `Ctrl+C`.

### Regenerate data, then preview

```bash
python dev.py --scrape
```

### Custom port / no browser

```bash
python dev.py --port 9000 --no-open
```

> Note: opening `index.html` directly with `file://` will **not** work,
> because the browser blocks `fetch("data/seminars.json")` on the
> `file://` protocol. Always use `dev.py` (or any static server).

## GitHub Pages

In your repository settings, set Pages to deploy from the
`main` branch, `/ (root)` folder. The site will be live at
`https://<your-username>.github.io/<your-repo>/`.

## Triggering the workflow manually

Go to **Actions → Scrape CUNY Seminars → Run workflow**.
