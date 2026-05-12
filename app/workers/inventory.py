"""
inventory.py
============
LeadFlow database inventory — liens, permits, PDFs, matches.

Run:
  python inventory.py
  python -m app.workers.inventory
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR  = BASE_DIR / "data" / "raw"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt(n) -> str:
    return f"{int(n or 0):,}"

def section(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")

def row(label: str, value, width: int = 38):
    print(f"  {label:<{width}} {value}")


# ---------------------------------------------------------------------------
# PDF file counts from disk
# ---------------------------------------------------------------------------
def count_pdfs() -> dict[str, int]:
    counts = {}
    for county_dir in RAW_DIR.iterdir():
        if not county_dir.is_dir():
            continue
        total = 0
        # Check all possible PDF locations
        for subpath in [
            county_dir / "liens" / "pdfs",
            county_dir / "irs_liens" / "pdfs",   # Miami-Dade
            county_dir / "pdfs",
        ]:
            if subpath.exists():
                total += len(list(subpath.glob("*.pdf")))
        if total > 0:
            counts[county_dir.name] = total
    return counts


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------
def main():
    conn = get_connection()
    pdf_counts = count_pdfs()

    print(f"\n{'═' * 60}")
    print(f"  LeadFlow DB Inventory  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═' * 60}")

    try:
        with conn.cursor() as cur:

            # ── COUNTIES ────────────────────────────────────────────────────
            section("COUNTIES IN DB")
            cur.execute("""
                SELECT county_name, state, active, created_at::date
                FROM counties
                ORDER BY county_name
            """)
            counties = cur.fetchall()
            if counties:
                print(f"  {'County':<20} {'State':<6} {'Active':<8} {'Created'}")
                print(f"  {'-'*20} {'-'*5} {'-'*7} {'-'*10}")
                for name, state, active, created in counties:
                    print(f"  {str(name):<20} {str(state):<6} {str(active):<8} {created}")
            else:
                print("  No counties found")

            # ── LIENS BY COUNTY ─────────────────────────────────────────────
            section("LIENS BY COUNTY")
            cur.execute("""
                SELECT
                    c.county_name,
                    COUNT(nl.id)                                    AS total_liens,
                    COUNT(nl.id) FILTER (
                        WHERE nl.lien_type = 'federal_tax_lien')    AS federal,
                    COUNT(nl.id) FILTER (
                        WHERE nl.lien_type = 'state_tax_lien')      AS state,
                    MIN(nl.filed_date)                              AS earliest,
                    MAX(nl.filed_date)                              AS latest
                FROM normalized_liens nl
                JOIN counties c ON nl.county_id = c.id
                GROUP BY c.county_name
                ORDER BY total_liens DESC
            """)
            lien_rows = cur.fetchall()
            if lien_rows:
                print(f"  {'County':<16} {'Total':>7} {'Federal':>8} {'State':>7} "
                      f"{'Earliest':<12} {'Latest':<12} {'PDFs on disk':>12}")
                print(f"  {'-'*16} {'-'*7} {'-'*8} {'-'*7} {'-'*11} {'-'*11} {'-'*12}")
                total_liens = 0
                for name, total, fed, state, earliest, latest in lien_rows:
                    pdfs = pdf_counts.get(str(name).lower().replace("-","_")
                                         .replace(" ","_"), 0)
                    # also try raw name
                    if pdfs == 0:
                        for k, v in pdf_counts.items():
                            if str(name).lower().replace(" ","_").replace("-","_") in k.lower():
                                pdfs = v
                                break
                    print(f"  {str(name):<16} {fmt(total):>7} {fmt(fed):>8} "
                          f"{fmt(state):>7} {str(earliest or '—'):<12} "
                          f"{str(latest or '—'):<12} {fmt(pdfs):>12}")
                    total_liens += (total or 0)
                print(f"  {'TOTAL':<16} {fmt(total_liens):>7}")
            else:
                print("  No liens found")

            # ── PERMITS BY COUNTY ────────────────────────────────────────────
            section("PERMITS BY COUNTY")
            cur.execute("""
                SELECT
                    c.county_name,
                    COUNT(np.id)            AS total,
                    MIN(np.issued_date)     AS earliest,
                    MAX(np.issued_date)     AS latest
                FROM normalized_permits np
                JOIN counties c ON np.county_id = c.id
                GROUP BY c.county_name
                ORDER BY total DESC
            """)
            permit_rows = cur.fetchall()
            if permit_rows:
                print(f"  {'County':<16} {'Total':>8} {'Earliest':<12} {'Latest'}")
                print(f"  {'-'*16} {'-'*8} {'-'*11} {'-'*11}")
                total_permits = 0
                for name, total, earliest, latest in permit_rows:
                    print(f"  {str(name):<16} {fmt(total):>8} "
                          f"{str(earliest or '—'):<12} {str(latest or '—')}")
                    total_permits += (total or 0)
                print(f"  {'TOTAL':<16} {fmt(total_permits):>8}")
            else:
                print("  No permits found")

            # ── CONTACTS / EMAIL READINESS ───────────────────────────────────
            section("CONTACTS & EMAIL READINESS")

            # Contacts table (permit-based matches)
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE email IS NOT NULL
                        AND email NOT LIKE '%@example.com'
                        AND email NOT LIKE '%.invalid') AS real_emails
                FROM contacts
            """)
            ct = cur.fetchone()
            permit_emails = ct[0] if ct else 0

            # lien_dbpr_contacts (direct lien→DBPR matches)
            try:
                cur.execute("""
                    SELECT
                        COUNT(*)                            AS total,
                        COUNT(*) FILTER (
                            WHERE confidence = 'high')      AS high_conf,
                        COUNT(*) FILTER (
                            WHERE confidence = 'medium')    AS med_conf,
                        COUNT(DISTINCT county_id)           AS counties
                    FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL AND email != ''
                """)
                ldc = cur.fetchone()
                dbpr_emails  = ldc[0] if ldc else 0
                dbpr_high    = ldc[1] if ldc else 0
                dbpr_med     = ldc[2] if ldc else 0
                dbpr_counties= ldc[3] if ldc else 0
            except Exception:
                dbpr_emails = dbpr_high = dbpr_med = dbpr_counties = 0

            total_emailable = permit_emails + dbpr_emails
            row("Permit-matched emails:",   fmt(permit_emails))
            row("Lien→DBPR emails:",        fmt(dbpr_emails))
            row("  — High confidence:",     fmt(dbpr_high))
            row("  — Medium confidence:",   fmt(dbpr_med))
            row("  — Counties covered:",    fmt(dbpr_counties))
            row("TOTAL emailable:",         fmt(total_emailable))

            # ── OUTREACH ─────────────────────────────────────────────────────
            section("OUTREACH EVENTS")
            try:
                cur.execute("""
                    SELECT
                        COUNT(*)                                            AS total,
                        COUNT(*) FILTER (WHERE event_type = 'email_sent')  AS sent,
                        COUNT(DISTINCT lead_id)                             AS unique_leads
                    FROM outreach_events
                """)
                oe = cur.fetchone()
                if oe and oe[0]:
                    row("Total events:",     fmt(oe[0]))
                    row("Emails sent:",      fmt(oe[1]))
                    row("Unique leads:",     fmt(oe[2]))
                else:
                    print("  No outreach events yet")
            except Exception:
                conn.rollback()
                print("  No outreach events yet")

            # ── PDF FILES ON DISK ────────────────────────────────────────────
            section("PDF FILES ON DISK")
            total_pdfs = 0
            for county, count in sorted(pdf_counts.items()):
                row(f"{county}:", fmt(count))
                total_pdfs += count
            if pdf_counts:
                print(f"  {'─'*38}")
                row("TOTAL:", fmt(total_pdfs))
            else:
                print("  No PDF directories found")

            # ── SUMMARY ──────────────────────────────────────────────────────
            section("SUMMARY")
            cur.execute("SELECT COUNT(*) FROM normalized_liens")
            nl = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM normalized_permits")
            np_ = cur.fetchone()[0] or 0
            cur.execute("""
                SELECT COUNT(*) FROM contacts
                WHERE email IS NOT NULL
                AND email NOT LIKE '%@example.com'
            """)
            em = cur.fetchone()[0] or 0
            try:
                cur.execute("""
                    SELECT COUNT(*) FROM lien_dbpr_contacts
                    WHERE email IS NOT NULL AND email != ''
                """)
                dbpr_em = cur.fetchone()[0] or 0
            except Exception:
                dbpr_em = 0

            row("Total liens:",           fmt(nl))
            row("Total permits:",         fmt(np_))
            row("Emailable contacts:",    fmt(em + dbpr_em))
            row("  — Permit matched:",    fmt(em))
            row("  — Lien→DBPR:",         fmt(dbpr_em))
            row("PDFs on disk:",          fmt(total_pdfs))

    finally:
        conn.close()

    print(f"\n{'═' * 60}\n")


if __name__ == "__main__":
    main()