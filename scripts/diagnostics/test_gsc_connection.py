"""
test_gsc_connection.py
======================
Tests Google Search Console API connection and pulls
basic performance data for taxcasereview.org.

Usage:
  python test_gsc_connection.py
"""
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from pathlib import Path
from datetime import date, timedelta
import json

TOKEN_FILE   = Path("data/credentials/gsc-token.json")
SCOPES       = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "sc-domain:taxcasereview.org"

def get_gsc_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return build("searchconsole", "v1", credentials=creds)

def main():
    print("\n[GSC Connection Test]")
    print(f"  Site: {SITE_URL}\n")

    service = get_gsc_service()

    # ── 1. List verified sites ────────────────────────────────────────────────
    print("── Verified Sites ──")
    sites = service.sites().list().execute()
    for s in sites.get("siteEntry", []):
        print(f"  {s['siteUrl']}  ({s['permissionLevel']})")

    # ── 2. Pull last 28 days performance ──────────────────────────────────────
    print("\n── Last 28 Days Performance ──")
    end_date   = date.today() - timedelta(days=3)  # GSC has 3-day delay
    start_date = end_date - timedelta(days=28)

    response = service.searchanalytics().query(
        siteUrl=SITE_URL,
        body={
            "startDate":  str(start_date),
            "endDate":    str(end_date),
            "dimensions": ["query"],
            "rowLimit":   10,
            "orderBy":    [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
        }
    ).execute()

    rows = response.get("rows", [])
    if rows:
        print(f"  Top 10 queries by clicks ({start_date} → {end_date}):\n")
        print(f"  {'Query':<45} {'Clicks':>7} {'Impr':>8} {'CTR':>7} {'Pos':>6}")
        print(f"  {'─'*45} {'─'*7} {'─'*8} {'─'*7} {'─'*6}")
        for row in rows:
            query  = row["keys"][0][:44]
            clicks = int(row.get("clicks", 0))
            impr   = int(row.get("impressions", 0))
            ctr    = round(row.get("ctr", 0) * 100, 1)
            pos    = round(row.get("position", 0), 1)
            print(f"  {query:<45} {clicks:>7,} {impr:>8,} {ctr:>6.1f}% {pos:>6.1f}")
    else:
        print("  No data returned — site may be new or not indexed yet")

    # ── 3. Pull by page ───────────────────────────────────────────────────────
    print("\n── Top 10 Pages by Clicks ──")
    page_response = service.searchanalytics().query(
        siteUrl=SITE_URL,
        body={
            "startDate":  str(start_date),
            "endDate":    str(end_date),
            "dimensions": ["page"],
            "rowLimit":   10,
            "orderBy":    [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
        }
    ).execute()

    page_rows = page_response.get("rows", [])
    if page_rows:
        print(f"  {'Page':<55} {'Clicks':>7} {'Impr':>8}")
        print(f"  {'─'*55} {'─'*7} {'─'*8}")
        for row in page_rows:
            page   = row["keys"][0].replace("https://taxcasereview.org", "")[:54]
            clicks = int(row.get("clicks", 0))
            impr   = int(row.get("impressions", 0))
            print(f"  {page:<55} {clicks:>7,} {impr:>8,}")
    else:
        print("  No page data yet")

    # ── 4. Summary totals ─────────────────────────────────────────────────────
    print("\n── Summary Totals (last 28 days) ──")
    total_response = service.searchanalytics().query(
        siteUrl=SITE_URL,
        body={
            "startDate": str(start_date),
            "endDate":   str(end_date),
            "rowLimit":  1,
        }
    ).execute()

    if total_response.get("rows"):
        r = total_response["rows"][0]
        print(f"  Total clicks      : {int(r.get('clicks', 0)):,}")
        print(f"  Total impressions : {int(r.get('impressions', 0)):,}")
        print(f"  Average CTR       : {round(r.get('ctr', 0)*100, 2)}%")
        print(f"  Average position  : {round(r.get('position', 0), 1)}")

    print(f"\n✅ GSC connection working correctly\n")

if __name__ == "__main__":
    main()
