"""
export_contacts.py
==================
Exports all enriched contacts from all sources into one CSV.

Sources (in priority order):
1. DBPR — highest quality, licensed contractors with verified email/phone
2. Sunbiz — registered agent name + address
3. Web contacts — YellowPages, Manta, BBB emails

Usage:
  python export_contacts.py
  python export_contacts.py --min-confidence 0.5
  python export_contacts.py --county miami-dade
"""
import argparse, csv
from datetime import datetime
from pathlib import Path

try:
    from app.core.db import get_connection
except ImportError:
    import sys; sys.exit("Run from leadflow directory")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", default=None)
    parser.add_argument("--min-confidence", type=float, default=0.45)
    args = parser.parse_args()

    conn = get_connection()
    rows = []

    try:
        with conn.cursor() as cur:

            # ── Source 1: DBPR ────────────────────────────────────────────
            county_filter = "AND c.county_name ILIKE %s" if args.county else ""
            county_val = [f"%{args.county}%"] if args.county else []

            cur.execute(f"""
                SELECT
                    nl.debtor_name,
                    d.email,
                    d.phone,
                    d.license_number,
                    d.trade         AS license_type,
                    d.dbpr_score    AS match_score,
                    c.county_name,
                    nl.filed_date,
                    nl.lien_type,
                    nl.pdf_path,
                    'DBPR' as source
                FROM lien_dbpr_contacts d
                JOIN normalized_liens nl ON nl.id = d.lien_id
                JOIN counties c ON c.id = nl.county_id
                WHERE d.email IS NOT NULL
                AND d.dbpr_score >= %s
                {county_filter}
                ORDER BY nl.filed_date DESC NULLS LAST
            """, [args.min_confidence] + county_val)

            for row in cur.fetchall():
                rows.append({
                    "name":           row[0],
                    "email":          row[1],
                    "phone":          row[2],
                    "license_number": row[3],
                    "license_type":   row[4],
                    "match_score":    row[5],
                    "county":         row[6],
                    "filed_date":     row[7],
                    "lien_type":      row[8],
                    "pdf_path":       row[9],
                    "source":         row[10],
                    "website":        "",
                })

            dbpr_count = len(rows)
            print(f"  DBPR contacts:    {dbpr_count}")

            # ── Source 2: Web contacts (YellowPages/Manta/BBB) ───────────
            # Get leads not already in DBPR results
            dbpr_lien_ids = set()
            cur.execute("SELECT lien_id FROM lien_dbpr_contacts WHERE email IS NOT NULL")
            dbpr_lien_ids = {r[0] for r in cur.fetchall()}

            try:
                cur.execute(f"""
                    SELECT
                        nl.debtor_name,
                        ce.email,
                        ce.phone,
                        ce.website,
                        ce.source,
                        c.county_name,
                        nl.filed_date,
                        nl.lien_type,
                        nl.pdf_path
                    FROM lien_contact_enrichment ce
                    JOIN normalized_liens nl ON nl.id = ce.normalized_lien_id
                    JOIN counties c ON c.id = nl.county_id
                    WHERE ce.email IS NOT NULL
                    AND ce.normalized_lien_id NOT IN (
                        SELECT lien_id FROM lien_dbpr_contacts
                        WHERE email IS NOT NULL
                    )
                    {county_filter}
                    ORDER BY nl.filed_date DESC NULLS LAST
                """, county_val)

                for row in cur.fetchall():
                    rows.append({
                        "name":           row[0],
                        "email":          row[1],
                        "phone":          row[2] or "",
                        "license_number": "",
                        "license_type":   "",
                        "match_score":    "",
                        "county":         row[5],
                        "filed_date":     row[6],
                        "lien_type":      row[7],
                        "pdf_path":       row[8],
                        "source":         f"WEB_{row[4].upper()}",
                        "website":        row[3] or "",
                    })
                web_count = len(rows) - dbpr_count
                print(f"  Web contacts:     {web_count}")
            except Exception as e:
                print(f"  Web contacts:     0 (table may not exist yet: {e})")

            # ── Source 3: Sunbiz (address only, no email — skip for email campaign) ─
            # Sunbiz gives registered agent but rarely email
            # Only include if they have an email from website scraping
            sunbiz_count = 0
            try:
                cur.execute(f"""
                    SELECT COUNT(*) FROM lien_sunbiz_contacts
                    WHERE normalized_lien_id NOT IN (
                        SELECT normalized_lien_id FROM lien_dbpr_contacts WHERE email IS NOT NULL
                    )
                """)
                sunbiz_count = cur.fetchone()[0]
                print(f"  Sunbiz matches:   {sunbiz_count} (address only, no email)")
            except Exception:
                pass

    finally:
        conn.close()

    if not rows:
        print("\n  No contacts found yet — run enrichment first")
        return

    # Deduplicate by email
    seen_emails = set()
    unique_rows = []
    for r in rows:
        email = (r["email"] or "").lower().strip()
        if email and email not in seen_emails:
            seen_emails.add(email)
            unique_rows.append(r)

    # Export CSV
    out_dir = Path("data") / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"leadflow_contacts_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    fieldnames = [
        "name", "email", "phone", "county", "filed_date",
        "lien_type", "source", "license_number", "license_type",
        "match_score", "website", "pdf_path"
    ]

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(unique_rows)

    print(f"\n{'='*50}")
    print(f"  Total contacts:   {len(unique_rows)}")
    print(f"  DBPR:             {dbpr_count}")
    print(f"  Web sources:      {len(unique_rows) - dbpr_count}")
    print(f"  Exported to:      {out_file}")

    # Breakdown by county
    print(f"\n  By county:")
    from collections import Counter
    county_counts = Counter(r["county"] for r in unique_rows)
    for county, count in sorted(county_counts.items(), key=lambda x: -x[1]):
        print(f"    {county:<20} {count}")

if __name__ == "__main__":
    main()