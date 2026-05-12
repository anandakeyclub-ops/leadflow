"""
diagnose_pipeline.py - End-to-end diagnostic for the leadflow pipeline.

Usage:
  python diagnose_pipeline.py              # full report
  python diagnose_pipeline.py --match      # deep match analysis only
  python diagnose_pipeline.py --fix        # auto-fix common issues
  python diagnose_pipeline.py --sample 200 # larger match sample
"""
import argparse
import sys
sys.path.insert(0, ".")

from app.core.db import get_connection
from app.services.matching import calculate_match, normalize_name

SEP  = "=" * 70
SEP2 = "-" * 70

def query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

def scalar(conn, sql, params=None):
    rows = query(conn, sql, params)
    return rows[0][0] if rows else None

def check_table_health(conn):
    print(f"\n{SEP}\nSECTION 1: TABLE HEALTH\n{SEP}")

    print("\nCounties:")
    for row in query(conn, "SELECT id, county_name FROM counties ORDER BY id"):
        print(f"  id={row[0]}  {row[1]}")

    rp  = query(conn, "SELECT COUNT(*), COUNT(DISTINCT county_id) FROM raw_permits")[0]
    np_ = query(conn, """SELECT COUNT(*), COUNT(DISTINCT county_id),
        COUNT(CASE WHEN owner_name IS NOT NULL AND owner_name != ''
                    AND owner_name !~ '^\\d+$' AND length(trim(owner_name))>=3 THEN 1 END),
        COUNT(CASE WHEN address_1 IS NOT NULL AND address_1 != '' THEN 1 END)
        FROM normalized_permits""")[0]
    rl  = query(conn, "SELECT COUNT(*), COUNT(DISTINCT county_id) FROM raw_liens")[0]
    nl  = query(conn, """SELECT COUNT(*), COUNT(DISTINCT county_id),
        COUNT(CASE WHEN debtor_name IS NOT NULL AND debtor_name != ''
                    AND debtor_name !~ '^\\d+$' AND length(trim(debtor_name))>=3 THEN 1 END)
        FROM normalized_liens""")[0]
    ml  = query(conn, """SELECT COUNT(*), COUNT(DISTINCT county_id),
        COUNT(CASE WHEN match_confidence='high' THEN 1 END),
        COUNT(CASE WHEN match_confidence='medium' THEN 1 END)
        FROM matched_leads""")[0]

    print(f"\n  raw_permits       : {rp[0]:>6} rows | {rp[1]} counties")
    print(f"  normalized_permits: {np_[0]:>6} rows | {np_[1]} counties | {np_[2]} real owner_name | {np_[3]} with address")
    print(f"  raw_liens         : {rl[0]:>6} rows | {rl[1]} counties")
    print(f"  normalized_liens  : {nl[0]:>6} rows | {nl[1]} counties | {nl[2]} real debtor_name")
    print(f"  matched_leads     : {ml[0]:>6} rows | {ml[1]} counties | {ml[2]} high | {ml[3]} medium")

def check_county_alignment(conn):
    print(f"\n{SEP}\nSECTION 2: COUNTY ID ALIGNMENT\n{SEP}")

    pc = query(conn, "SELECT county_id, COUNT(*) FROM normalized_permits GROUP BY county_id ORDER BY county_id")
    lc = query(conn, "SELECT county_id, COUNT(*) FROM normalized_liens GROUP BY county_id ORDER BY county_id")
    pids = {r[0] for r in pc}
    lids = {r[0] for r in lc}

    print("\nPermit county_ids:")
    for cid, cnt in pc:
        name = scalar(conn, "SELECT county_name FROM counties WHERE id=%s", [cid]) or "?"
        mark = "✓ MATCHES LIENS" if cid in lids else "✗ NO LIENS"
        print(f"  county_id={cid} ({name}): {cnt:>5} permits  {mark}")

    print("\nLien county_ids:")
    for cid, cnt in lc:
        name = scalar(conn, "SELECT county_name FROM counties WHERE id=%s", [cid]) or "?"
        real = scalar(conn, "SELECT COUNT(*) FROM normalized_liens WHERE county_id=%s AND debtor_name !~ '^\\d+$' AND length(trim(coalesce(debtor_name,'')))>=3", [cid])
        mark = "✓ MATCHES PERMITS" if cid in pids else "✗ NO PERMITS"
        print(f"  county_id={cid} ({name}): {cnt:>5} liens ({real} real names)  {mark}")

    overlap = pids & lids
    if not overlap:
        print("\n  ⚠ CRITICAL: No county_id overlap — run --fix to auto-correct")
    else:
        print(f"\n  ✓ Overlapping: {overlap}")
        for cid in overlap:
            p = scalar(conn, "SELECT COUNT(*) FROM normalized_permits WHERE county_id=%s", [cid])
            l = scalar(conn, "SELECT COUNT(*) FROM normalized_liens WHERE county_id=%s AND debtor_name !~ '^\\d+$' AND length(trim(coalesce(debtor_name,'')))>=3", [cid])
            name = scalar(conn, "SELECT county_name FROM counties WHERE id=%s", [cid]) or "?"
            ratio = f"  ratio 1:{round(p/l) if l else '∞'}"
            status = "✓ good volume" if l >= 200 else f"⚠ need more liens (have {l}, want 200+)"
            print(f"  {name}: {p} permits vs {l} real liens{ratio}  {status}")

def check_data_quality(conn):
    print(f"\n{SEP}\nSECTION 3: DATA QUALITY SAMPLES\n{SEP}")

    print("\nTop 10 permits (owner_name):")
    rows = query(conn, """SELECT county_id, owner_name, address_1, permit_type, issued_date
        FROM normalized_permits WHERE owner_name IS NOT NULL AND owner_name != ''
        AND owner_name !~ '^\\d+$' AND length(trim(owner_name))>=3
        ORDER BY issued_date DESC NULLS LAST LIMIT 10""")
    for r in rows:
        print(f"  [{r[0]}] {str(r[1] or ''):<26} | {str(r[2] or ''):<26} | {str(r[3] or ''):<16} | {r[4]}")
    if not rows:
        print("  ⚠ No valid permit owner names!")

    print("\nTop 10 liens (debtor_name):")
    rows = query(conn, """SELECT county_id, debtor_name, address_1, filing_type, filed_date
        FROM normalized_liens WHERE debtor_name IS NOT NULL AND debtor_name != ''
        AND debtor_name !~ '^\\d+$' AND length(trim(debtor_name))>=3
        ORDER BY filed_date DESC NULLS LAST LIMIT 10""")
    for r in rows:
        print(f"  [{r[0]}] {str(r[1] or ''):<26} | {str(r[2] or ''):<26} | {str(r[3] or ''):<16} | {r[4]}")
    if not rows:
        print("  ⚠ No valid lien debtor names! Run lien scraper.")

    bad_p = scalar(conn, "SELECT COUNT(*) FROM normalized_permits WHERE owner_name IS NULL OR owner_name='' OR owner_name ~ '^\\d+$' OR length(trim(owner_name))<3")
    bad_l = scalar(conn, "SELECT COUNT(*) FROM normalized_liens WHERE debtor_name IS NULL OR debtor_name='' OR debtor_name ~ '^\\d+$' OR length(trim(debtor_name))<3")
    print(f"\n  Bad permit names: {bad_p}  |  Bad lien names: {bad_l}")

    pd_ = query(conn, "SELECT MIN(issued_date), MAX(issued_date) FROM normalized_permits WHERE issued_date IS NOT NULL")[0]
    ld_ = query(conn, "SELECT MIN(filed_date), MAX(filed_date) FROM normalized_liens WHERE filed_date IS NOT NULL")[0]
    print(f"  Permit dates: {pd_[0]} → {pd_[1]}")
    print(f"  Lien dates  : {ld_[0]} → {ld_[1]}")

def check_matching(conn, sample_size=50):
    print(f"\n{SEP}\nSECTION 4: MATCH SIMULATION ({sample_size} permits vs 500 liens)\n{SEP}")

    permits = query(conn, """SELECT id, county_id, owner_name, address_1
        FROM normalized_permits WHERE owner_name IS NOT NULL AND owner_name != ''
        AND owner_name !~ '^\\d+$' AND length(trim(owner_name))>=3
        ORDER BY issued_date DESC NULLS LAST LIMIT %s""", [sample_size])

    liens = query(conn, """SELECT id, county_id, debtor_name, address_1
        FROM normalized_liens WHERE debtor_name IS NOT NULL AND debtor_name != ''
        AND debtor_name !~ '^\\d+$' AND length(trim(debtor_name))>=3 LIMIT 500""")

    if not permits:
        print("  ⚠ No valid permits")
        return
    if not liens:
        print("  ⚠ No valid liens — run lien scraper")
        return

    hits = []
    county_skips = compared = 0

    for p in permits:
        p_id, p_cid, p_name, p_addr = p
        for l in liens:
            l_id, l_cid, l_name, l_addr = l
            if p_cid != l_cid:
                county_skips += 1
                continue
            compared += 1
            r = calculate_match(p_name, l_name, p_addr or "", l_addr or "")
            if r["match_score"] > 40:
                hits.append({
                    "pname": p_name, "lname": l_name,
                    "score": r["match_score"], "conf": r["match_confidence"],
                    "np": normalize_name(p_name), "nl": normalize_name(l_name),
                })

    print(f"\n  Compared: {compared} same-county pairs  |  County skips: {county_skips}")

    if county_skips > 0 and compared == 0:
        print("  ⚠ ALL skipped — county_id mismatch! Run --fix")
        return

    if not hits:
        print(f"\n  ⚠ No matches > 40 in {compared} pairs")
        print(f"\n  Sample permit names → normalized:")
        for p in permits[:6]:
            print(f"    '{p[2]}' → '{normalize_name(p[2])}'")
        print(f"\n  Sample lien names → normalized:")
        for l in liens[:6]:
            print(f"    '{l[2]}' → '{normalize_name(l[2])}'")
        print(f"\n  Likely cause: only {len(liens)} liens vs {len(permits)} permits.")
        print(f"  Need 500+ liens for statistical name overlap.")
        print(f"  Run: python -m app.workers.scrape_palm_beach_liens --headless false --days-back 365")
    else:
        hits.sort(key=lambda x: x["score"], reverse=True)
        print(f"\n  {len(hits)} pairs score > 40:")
        print(f"  {'Permit':<26} {'Lien':<26} {'Score':>6}  Conf")
        print(f"  {SEP2}")
        for h in hits[:20]:
            print(f"  {h['pname']:<26} {h['lname']:<26} {h['score']:>6.1f}  {h['conf']}")
            if h['np'] != h['pname'] or h['nl'] != h['lname']:
                print(f"    → '{h['np']}' vs '{h['nl']}'")
        above = [h for h in hits if h["conf"] in ("high","medium")]
        print(f"\n  Would write {len(above)} leads ({sum(1 for h in above if h['conf']=='high')} high, {sum(1 for h in above if h['conf']=='medium')} medium)")

def auto_fix(conn):
    print(f"\n{SEP}\nSECTION 5: AUTO-FIX\n{SEP}")
    conn.autocommit = False
    fixes = 0

    with conn.cursor() as cur:
        cur.execute("DELETE FROM normalized_liens WHERE debtor_name ~ '^\\d+$' OR length(trim(coalesce(debtor_name,''))) < 3")
        n = cur.rowcount
        if n: print(f"  ✓ Deleted {n} garbage lien names"); fixes += n

    with conn.cursor() as cur:
        cur.execute("DELETE FROM normalized_permits WHERE owner_name ~ '^\\d+$' OR length(trim(coalesce(owner_name,''))) < 3")
        n = cur.rowcount
        if n: print(f"  ✓ Deleted {n} garbage permit names"); fixes += n

    # County ID alignment
    pids = {r[0] for r in query(conn, "SELECT DISTINCT county_id FROM normalized_permits")}
    lids = {r[0] for r in query(conn, "SELECT DISTINCT county_id FROM normalized_liens")}
    if not (pids & lids):
        for p_cid in pids:
            p_name = scalar(conn, "SELECT county_name FROM counties WHERE id=%s", [p_cid])
            for l_cid in lids:
                l_name = scalar(conn, "SELECT county_name FROM counties WHERE id=%s", [l_cid])
                if p_name and l_name and p_name.lower() == l_name.lower() and p_cid != l_cid:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE raw_liens SET county_id=%s WHERE county_id=%s", [p_cid, l_cid])
                        cur.execute("UPDATE normalized_liens SET county_id=%s WHERE county_id=%s", [p_cid, l_cid])
                        print(f"  ✓ Repointed {cur.rowcount} liens: county_id {l_cid}→{p_cid} ({p_name})")
                    fixes += 1

    conn.commit()
    print(f"\n  {fixes} fix(es) applied" if fixes else "  No fixes needed")

    # Always print next steps
    l_cnt = scalar(conn, "SELECT COUNT(*) FROM normalized_liens WHERE debtor_name !~ '^\\d+$' AND length(trim(coalesce(debtor_name,'')))>=3")
    ml_cnt = scalar(conn, "SELECT COUNT(*) FROM matched_leads")
    print(f"\n  Next steps:")
    if (l_cnt or 0) < 200:
        print(f"  1. Get more liens ({l_cnt} real names, need 200+):")
        print(f"     python -m app.workers.scrape_palm_beach_liens --headless false --days-back 365")
    else:
        print(f"  1. Run matching:")
        print(f"     python -m app.workers.match_and_score")
    if (ml_cnt or 0) > 0:
        print(f"  2. Enrich + email:")
        print(f"     python -m app.workers.enrich_palm_beach_from_dbpr")
        print(f"     python -m app.workers.generate_email_list")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match",   action="store_true")
    parser.add_argument("--liens",   action="store_true")
    parser.add_argument("--permits", action="store_true")
    parser.add_argument("--fix",     action="store_true")
    parser.add_argument("--sample",  type=int, default=50)
    args = parser.parse_args()

    conn = get_connection()
    try:
        full = not any([args.match, args.liens, args.permits, args.fix])
        if full or args.permits or args.liens:
            check_table_health(conn)
            check_county_alignment(conn)
            check_data_quality(conn)
        if full or args.match:
            check_matching(conn, sample_size=args.sample)
        if args.fix:
            auto_fix(conn)
        print(f"\n{SEP}\nDIAGNOSIS COMPLETE\n{SEP}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
