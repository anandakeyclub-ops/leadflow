"""
patch_miami_dade.py
===================
Patches scrape_miami_dade_liens.py in-place without needing to copy the full file.
Run from the leadflow root directory.

Usage:
  python patch_miami_dade.py
"""
from pathlib import Path
import re, ast

target = Path("app/workers/scrape_miami_dade_liens.py")
if not target.exists():
    print(f"ERROR: {target} not found")
    exit(1)

src = target.read_text(encoding="utf-8")

changes = 0

# Fix 1: Add doc_type param to search_with_recaptcha signature
if 'def search_with_recaptcha(driver, start: date, end: date) ->' in src:
    src = src.replace(
        'def search_with_recaptcha(driver, start: date, end: date) -> Optional[str]:',
        'def search_with_recaptcha(driver, start: date, end: date, doc_type="FEDERAL TAX LIEN  - FTL") -> Optional[str]:'
    )
    changes += 1
    print("✓ Fix 1: Added doc_type param to search_with_recaptcha")
else:
    print("  Fix 1: Already applied or signature different")

# Fix 2: Remove hardcoded doc_type inside the function
if '    doc_type  = "FEDERAL TAX LIEN  - FTL"' in src:
    src = src.replace(
        '    doc_type  = "FEDERAL TAX LIEN  - FTL"',
        '    # doc_type passed as parameter'
    )
    changes += 1
    print("✓ Fix 2: Removed hardcoded doc_type inside function")
else:
    print("  Fix 2: Already applied")

# Fix 3: Add DOC_TYPES list if missing
if 'DOC_TYPES = [' not in src:
    src = src.replace(
        'DOC_TYPE    = "FEDERAL TAX LIEN - FTL"',
        '''DOC_TYPES = [
    ("FEDERAL TAX LIEN  - FTL", "federal_tax_lien"),
    ("STATE TAX LIEN  - STL",   "state_tax_lien"),
]
DOC_TYPE    = "FEDERAL TAX LIEN  - FTL"  # kept for compatibility'''
    )
    changes += 1
    print("✓ Fix 3: Added DOC_TYPES list")
else:
    print("  Fix 3: DOC_TYPES already present")

# Fix 4: Update scrape loop to iterate DOC_TYPES if not already done
if 'for doc_type_str, lien_type_tag in DOC_TYPES:' not in src:
    # Find the simple qs = search_with_recaptcha call and wrap it
    old = '            qs = search_with_recaptcha(driver, current, chunk_end)'
    new = '''            added_chunk = 0
            for doc_type_str, lien_type_tag in DOC_TYPES:
                qs = search_with_recaptcha(driver, current, chunk_end, doc_type_str)

                if qs:
                    time.sleep(1)
                    rows = fetch_results_via_browser(driver, qs)
                    if not rows:
                        print(f"    No rows for {doc_type_str}")
                    for row in rows:
                        rec = parse_row(row)
                        if rec and rec.instrument_number not in seen:
                            rec.raw_payload["_lien_type"] = lien_type_tag
                            seen.add(rec.instrument_number)
                            all_records.append(rec)
                            added_chunk += 1
                else:
                    print(f"    No qs for {doc_type_str}")
                time.sleep(2)
            print(f"    +{added_chunk} new records (total: {len(all_records)})")'''

    if old in src:
        # Also need to remove the old if qs: block that follows
        # Find and replace the full old block
        old_full = old + '''

            if qs:
                time.sleep(1)
                # Use browser fetch — qs token is tied to browser's server session
                rows = fetch_results_via_browser(driver, qs)
                if not rows:
                    print("    Browser fetch returned no rows — check JSON dict keys above")

                added = 0
                for row in rows:
                    rec = parse_row(row)
                    if rec and rec.instrument_number not in seen:
                        seen.add(rec.instrument_number)
                        all_records.append(rec)
                        added += 1

                print(f"    +{added} new records (running total: {len(all_records)})")'''
        if old_full in src:
            src = src.replace(old_full, new)
            changes += 1
            print("✓ Fix 4: Updated scrape loop to iterate DOC_TYPES")
        else:
            src = src.replace(old, new)
            changes += 1
            print("✓ Fix 4: Added DOC_TYPES loop (partial)")
    else:
        print("  Fix 4: Loop already updated")
else:
    print("  Fix 4: DOC_TYPES loop already present")

# Validate syntax
try:
    ast.parse(src)
    print(f"\n✓ Syntax OK — {changes} change(s) applied")
except SyntaxError as e:
    print(f"\n✗ SYNTAX ERROR: {e}")
    exit(1)

target.write_text(src, encoding="utf-8")
print(f"✓ Saved: {target}")

# Final verification
final = target.read_text(encoding="utf-8")
checks = [
    ("doc_type param in signature", 'doc_type="FEDERAL TAX LIEN' in final),
    ("DOC_TYPES list defined",      "DOC_TYPES = [" in final),
    ("loop iterates DOC_TYPES",     "for doc_type_str, lien_type_tag in DOC_TYPES:" in final),
    ("state lien included",         "STATE TAX LIEN" in final),
]
print("\nVerification:")
all_ok = True
for label, ok in checks:
    print(f"  {'✓' if ok else '✗'} {label}")
    if not ok: all_ok = False

if all_ok:
    print("\n✓ Miami-Dade script is ready. Run:")
    print("  python -m app.workers.scrape_miami_dade_liens --days-back 180 --no-headless")
else:
    print("\n✗ Some fixes didn't apply — paste output for further help")
