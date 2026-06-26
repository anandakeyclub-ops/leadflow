"""
broken_link_finder.py
=====================
Broken-link prospecting for TaxCase Review. Crawls competitor/resource pages,
finds dead outbound links (404/410/dead host), and where TaxCase Review has a
relevant replacement page, drafts a polite "broken link" outreach email.

This is white-hat broken-link building: only read-only GET/HEAD requests to
public pages; nothing is emailed automatically (drafts only).

CLI:
  python scripts/outreach/broken_link_finder.py --seed                  # write targets JSON
  python scripts/outreach/broken_link_finder.py --discover-competitors  # CSE top-10 (needs Google CSE)
  python scripts/outreach/broken_link_finder.py --run --limit 3         # scan N targets, write CSVs
  python scripts/outreach/broken_link_finder.py --run --limit 3 --draft # also draft outreach (Claude)

Logs via PipelineLogger("broken_links"). Wired into weekly_intelligence.py to
run Sundays alongside the weekly report.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

try:
    from scripts.outreach.outreach_db import record_outreach
except Exception:
    try:
        from outreach_db import record_outreach
    except Exception:
        record_outreach = None

BASE_DIR     = Path(__file__).resolve().parents[2]
TARGETS_JSON = BASE_DIR / "data" / "outreach" / "link_targets.json"
BROKEN_CSV   = BASE_DIR / "data" / "outreach" / "broken_links.csv"
OUTREACH_CSV = BASE_DIR / "data" / "outreach" / "broken_link_outreach.csv"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
UA = "Mozilla/5.0 (compatible; TaxCaseReviewLinkBot/1.0; +https://taxcasereview.org)"

MAX_LINKS_PER_PAGE = 40   # politeness/runtime cap
LINK_TIMEOUT       = 7
SITE_URL           = "https://taxcasereview.org"

# Broken = clearly dead. We deliberately ignore 403/429/999 (bot blocks) to
# avoid false positives — only 404/410 and dead hosts count.
BROKEN_STATUSES = {404, 410}

# Relevant-replacement routing: keyword (in broken URL or anchor) -> TCR page.
REPLACEMENT_MAP = [
    (("offer in compromise", "offer-in-compromise", "oic", "settle"),
     f"{SITE_URL}/resolution/offer-in-compromise", "Offer in Compromise guide"),
    (("lien", "nftl", "notice of federal tax lien"),
     f"{SITE_URL}/irs-liens", "federal tax lien help page"),
    (("cp14", "cp501", "cp503", "cp504", "lt11", "notice", "levy", "garnishment"),
     f"{SITE_URL}/irs-notices", "IRS notices resource"),
    (("contractor", "construction", "subcontractor", "tradesman", "1099"),
     f"{SITE_URL}/contractors", "contractor tax help page"),
    (("tax resolution", "tax relief", "back taxes", "tax debt", "installment"),
     f"{SITE_URL}/quiz", "IRS resolution assessment"),
]

# ── Seed targets ──────────────────────────────────────────────────────────────
def _t(url, category):
    return {"url": url, "category": category}

SEED_TARGETS = [
    # IRS resource pages
    _t("https://www.irs.gov/businesses/small-businesses-self-employed", "irs_resource"),
    _t("https://www.irs.gov/individuals/understanding-a-federal-tax-lien", "irs_resource"),
    # Contractor association resource sections
    _t("https://www.abc.org/News-Media", "association"),
    _t("https://www.agc.org/learn", "association"),
    _t("https://www.nahb.org/advocacy/industry-issues", "association"),
    _t("https://www.floridacontractors.org/resources", "association"),
    # Small-business tax resources
    _t("https://www.score.org/resource/business-taxes", "small_business"),
    # Competitor sites populated by --discover-competitors (two SERP queries).
]

COMPETITOR_QUERIES = ["IRS tax resolution contractor", "IRS lien help florida"]


# ── Targets JSON ──────────────────────────────────────────────────────────────
def seed_targets(force: bool = False) -> int:
    TARGETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    if TARGETS_JSON.exists() and not force:
        print(f"  Targets already exist: {TARGETS_JSON} (use --seed --force to overwrite)")
        return 0
    import json
    TARGETS_JSON.write_text(json.dumps(SEED_TARGETS, indent=2), encoding="utf-8")
    print(f"  Seeded {len(SEED_TARGETS)} targets -> {TARGETS_JSON} "
          f"(run --discover-competitors to add top-10 competitor sites)")
    return len(SEED_TARGETS)


def load_targets() -> list[dict]:
    import json
    if not TARGETS_JSON.exists():
        seed_targets()
    return json.loads(TARGETS_JSON.read_text(encoding="utf-8"))


def save_targets(targets: list[dict]) -> None:
    import json
    TARGETS_JSON.write_text(json.dumps(targets, indent=2), encoding="utf-8")


def discover_competitors() -> int:
    key = os.getenv("GOOGLE_SEARCH_API_KEY", "")
    cse = os.getenv("GOOGLE_CSE_ID", "")
    if not key or not cse:
        print("  No Google CSE creds — cannot discover competitors.")
        return 0
    targets = load_targets()
    have = {t["url"] for t in targets}
    added = 0
    for q in COMPETITOR_QUERIES:
        try:
            r = requests.get("https://www.googleapis.com/customsearch/v1",
                             params={"key": key, "cx": cse, "q": q, "num": 10}, timeout=15)
            items = r.json().get("items", []) if r.status_code == 200 else []
        except Exception as e:
            print(f"  CSE error: {e}"); items = []
        for it in items:
            link = it.get("link", "")
            host = urlparse(link).netloc
            if not link or "taxcasereview.org" in host:
                continue
            # store the homepage/section URL once per domain
            base = f"{urlparse(link).scheme}://{host}"
            if base not in have:
                targets.append({"url": base, "category": "competitor"})
                have.add(base); added += 1
    save_targets(targets)
    print(f"  Added {added} competitor targets (from {len(COMPETITOR_QUERIES)} SERPs)")
    return added


# ── Crawl + link checks ─────────────────────────────────────────────────────────
def fetch_page(url: str) -> tuple[int, str, str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=LINK_TIMEOUT)
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
        return r.status_code, r.text, title
    except Exception as e:
        return 0, "", f"(fetch error: {e})"


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    out = []
    seen = set()
    for m in re.finditer(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                         html, re.I | re.S):
        href = m.group(1).strip()
        anchor = re.sub(r"<[^>]+>", "", m.group(2))
        anchor = re.sub(r"\s+", " ", anchor).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(base_url, href)
        if not absu.startswith("http"):
            continue
        # only OUTBOUND links (different host than the source page)
        if urlparse(absu).netloc == urlparse(base_url).netloc:
            continue
        if absu in seen:
            continue
        seen.add(absu)
        out.append((absu, anchor))
    return out


def check_link(url: str) -> int | str:
    # Two attempts so a transient timeout from THIS crawler isn't logged as a
    # dead link. "DEAD" means unreachable from here after a retry — treat as a
    # candidate to verify manually, distinct from a definitive 404/410.
    for attempt in range(2):
        try:
            r = requests.head(url, headers={"User-Agent": UA}, timeout=LINK_TIMEOUT,
                              allow_redirects=True)
            if r.status_code in (405, 403):  # some servers reject HEAD — retry GET
                r = requests.get(url, headers={"User-Agent": UA}, timeout=LINK_TIMEOUT,
                                allow_redirects=True, stream=True)
            return r.status_code
        except requests.exceptions.RequestException:
            if attempt == 0:
                time.sleep(1.0)
                continue
            return "DEAD"


def is_broken(status) -> bool:
    return status == "DEAD" or (isinstance(status, int) and status in BROKEN_STATUSES)


def relevant_replacement(broken_url: str, anchor: str) -> tuple[str, str, str] | None:
    blob = f"{broken_url} {anchor}".lower()
    for keywords, repl_url, label in REPLACEMENT_MAP:
        if any(k in blob for k in keywords):
            cat = label
            return cat, repl_url, label
    return None


# ── Claude outreach draft ─────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 400) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def draft_outreach(page_title: str, source_page: str, broken_url: str,
                   anchor: str, repl_url: str, repl_label: str) -> str:
    prompt = f"""Write a brief, professional broken-link outreach email.

From: Romy Cruz, Licensed Tax Professional & licensed Enrolled Agent, TaxCase Review (taxcasereview.org)
To: the editor/webmaster of this page: "{page_title}" ({source_page})
Point out this broken link on their page:
  - broken URL: {broken_url}
  - link text: "{anchor or '(no anchor text)'}"
Suggest our relevant page as a replacement: {repl_url} (our {repl_label}).

Requirements:
- Subject line: "Broken link on {page_title[:60]}"
- Body UNDER 100 words. Professional, helpful, not spammy. Mention you were
  reading their resource, note the dead link specifically, offer our page as a
  possible replacement (their call), and sign off as Romy Cruz, TaxCase Review.
Output as:
SUBJECT: ...
BODY:
..."""
    return call_claude(prompt)


# ── CSV ──────────────────────────────────────────────────────────────────────
BROKEN_COLS   = ["source_page", "broken_url", "anchor_text", "http_status", "date_found"]
OUTREACH_COLS = ["source_page", "page_title", "broken_url", "anchor_text",
                 "replacement_category", "replacement_url", "subject",
                 "drafted", "sent", "date_found"]


def _append_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


# ── Main scan ────────────────────────────────────────────────────────────────
def run(limit: int, do_draft: bool, logger=None) -> dict:
    targets = load_targets()[:limit] if limit else load_targets()
    today = date.today().isoformat()
    broken_rows, outreach_rows = [], []
    pages_ok = 0

    for t in targets:
        url = t["url"]
        status, html, title = fetch_page(url)
        if status != 200 or not html:
            print(f"  [skip] {url} (status {status})")
            continue
        pages_ok += 1
        links = extract_links(html, url)[:MAX_LINKS_PER_PAGE]
        print(f"  [scan] {url}\n         title='{title[:60]}' | {len(links)} outbound links checked")
        for absu, anchor in links:
            st = check_link(absu)
            time.sleep(0.2)
            if not is_broken(st):
                continue
            broken_rows.append({"source_page": url, "broken_url": absu,
                                "anchor_text": anchor, "http_status": st,
                                "date_found": today})
            print(f"         BROKEN [{st}] {absu[:70]}  (\"{anchor[:30]}\")")
            repl = relevant_replacement(absu, anchor)
            if repl:
                cat, repl_url, label = repl
                subject = f"Broken link on {title[:60]}"
                drafted = ""
                if do_draft:
                    try:
                        draft = draft_outreach(title, url, absu, anchor, repl_url, label)
                        drafted = "Yes"
                        print("\n" + "-" * 68)
                        print(f"OUTREACH DRAFT — {url}")
                        print("-" * 68)
                        print(draft)
                        print("-" * 68 + "\n")
                    except Exception as e:
                        print(f"         draft failed: {e}")
                outreach_rows.append({
                    "source_page": url, "page_title": title, "broken_url": absu,
                    "anchor_text": anchor, "replacement_category": cat,
                    "replacement_url": repl_url, "subject": subject,
                    "drafted": drafted or "No", "sent": "", "date_found": today})
                # System of record: backlink_outreach DB table (one row per source page).
                if record_outreach is not None:
                    record_outreach(
                        urlparse(url).netloc or url, "broken_link",
                        subject=subject, status="drafted" if drafted == "Yes" else "pending",
                        published_url=repl_url,
                        notes=f"broken: {absu} ({cat})")

    # broken_links.csv keeps the raw dead-link findings; outreach opportunities
    # now live in the backlink_outreach table (not OUTREACH_CSV).
    _append_csv(BROKEN_CSV, BROKEN_COLS, broken_rows)
    print(f"\n  Pages scanned: {pages_ok} | broken links: {len(broken_rows)} | "
          f"replaceable opportunities: {len(outreach_rows)}")
    if logger:
        logger.finish({"pages_scanned": pages_ok, "broken_links": len(broken_rows),
                       "opportunities": len(outreach_rows)})
    return {"pages_scanned": pages_ok, "broken_links": len(broken_rows),
            "opportunities": len(outreach_rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Broken-link finder + outreach drafter")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--discover-competitors", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--draft", action="store_true", help="Also draft outreach emails (Claude)")
    ap.add_argument("--limit", type=int, default=0, help="Scan only first N targets")
    args = ap.parse_args()

    if args.seed:
        seed_targets(force=args.force); return
    if args.discover_competitors:
        discover_competitors(); return
    if not args.run:
        ap.print_help(); return

    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("broken_links"); logger.start()
    except Exception:
        logger = None
    run(limit=args.limit, do_draft=args.draft, logger=logger)


if __name__ == "__main__":
    main()
