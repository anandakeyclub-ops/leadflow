# LeadFlow — TaxCase Review data engine

Scrapers + enrichment that feed the lien-outreach email sequence. Multi-state
lien/license collection lives in `scripts/data_engine/` and `scripts/scrapers/`.

## Environment variables (`.env`)

Add these to the `.env` file in the repo root.

### CourtListener (Illinois federal tax liens — recommended)

The IL Secretary of State UCC site is behind a WAF that blocks automated
browsers, so the Illinois lien source uses the **CourtListener** federal docket
API instead (no WAF). It needs a free API token:

1. Register a free account at <https://www.courtlistener.com/>
2. Get your token at <https://www.courtlistener.com/profile/api/>
3. Add it to `.env`:

   ```
   COURTLISTENER_TOKEN=your_token_here
   ```

`collect_liens('il')` uses CourtListener first when this token is set, and falls
back to the SOS UCC Selenium scraper if it is not.

### Georgia GSCCCA (Georgia liens)

GSCCCA gates its lien index behind a login **and a CAPTCHA**, which blocks fully
automated login. Credentials:

```
GA_GSCCCA_USERNAME=your_gsccca_username
GA_GSCCCA_PASSWORD=your_gsccca_password
```

See "Georgia scraper" below for the manual / saved-session workflows that get
around the CAPTCHA.

### Other (already configured)

`PDL_API_KEY`, `GOOGLE_SEARCH_API_KEY` / `GOOGLE_CSE_ID`, `VALUESERP_KEY`,
`SERPAPI_KEY`, county-portal logins, `DATABASE_URL`, `ANTHROPIC_API_KEY`, etc.

## Georgia scraper (`scripts/scrapers/georgia_scraper.py`)

GSCCCA requires solving a CAPTCHA once. Two workflows:

```bash
# Option A — manual CAPTCHA: opens a visible browser, pre-fills your login,
# pauses for you to solve the CAPTCHA, then automates the FTL search.
python scripts/scrapers/georgia_scraper.py --manual

# Option B — save a session once, then reuse it headlessly until it expires:
python scripts/scrapers/georgia_scraper.py --save-session   # log in manually, saves cookies
python scripts/scrapers/georgia_scraper.py --use-session    # reuse cookies, no login

# Preview without writing to the DB:
python scripts/scrapers/georgia_scraper.py --manual --dry-run
```

Searches Federal Tax Liens (instrument code 3) for Fulton, Gwinnett, DeKalb,
and Cobb → `normalized_liens` (state='GA').

## Illinois scraper (`scripts/scrapers/illinois_scraper.py`)

```bash
# CourtListener federal dockets (needs COURTLISTENER_TOKEN):
python scripts/scrapers/illinois_scraper.py --courtlistener --dry-run
python scripts/scrapers/illinois_scraper.py --courtlistener

# SOS UCC Selenium scraper (fallback; blocked by WAF in most environments):
python scripts/scrapers/illinois_scraper.py --dry-run

# IDFPR contractor licenses -> normalized_contacts (state='IL'):
python scripts/scrapers/illinois_scraper.py --licenses
```

## Data engine

```bash
python scripts/data_engine/data_collector.py --stats          # per-state summary
python scripts/data_engine/data_collector.py --state il       # full IL pipeline
python scripts/data_engine/run_daily.py                       # today's scheduled states
```
