"""
check_versions.py
=================
Verifies that the correct updated versions of LeadFlow worker scripts
are deployed in app/workers/. Run this after updating any script.

Usage:
  python check_versions.py
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# (filename, signature_string, description_of_fix)
CHECKS = [
    (
        "app/workers/scrape_pinellas_liens.py",
        "disclaimer_dismissed = False",
        "Disclaimer fix — tries each XPath separately"
    ),
    (
        "app/workers/scrape_pinellas_liens.py",
        "By.NAME, field_id",
        "set_field By.NAME fallback"
    ),
    (
        "app/workers/scrape_pinellas_liens.py",
        "make_driver(visible=args.visible)",
        "make_driver arg name fix"
    ),
    (
        "app/workers/scrape_miami_dade_liens.py",
        "def search_with_recaptcha",
        "Combined reCAPTCHA + search function"
    ),
    (
        "app/workers/scrape_miami_dade_liens.py",
        "FEDERAL TAX LIEN  - FTL",
        "Double-space doc type fix"
    ),
    (
        "app/workers/scrape_miami_dade_liens.py",
        "doc_type: str =",
        "search_with_recaptcha accepts doc_type param"
    ),
    (
        "app/workers/scrape_miami_dade_liens.py",
        "DOC_TYPES = [",
        "Miami-Dade searches both federal + state liens"
    ),
    (
        "app/workers/scrape_miami_dade_liens.py",
        "STATE TAX LIEN",
        "Miami-Dade state tax lien included"
    ),
    (
        "app/workers/scrape_hillsborough_liens.py",
        "--include-state",
        "Federal-only by default fix"
    ),
    (
        "app/workers/scrape_polk_liens.py",
        "TX LN",
        "Polk federal+state tax liens only"
    ),
    (
        "app/workers/scrape_duval_liens.py",
        "[Duval Liens] Scraping",
        "Duval county label fix"
    ),
    (
        "app/workers/scrape_duval_liens.py",
        "disclaimer_dismissed",
        "Duval disclaimer handling"
    ),
    (
        "app/workers/scrape_palm_beach_liens.py",
        "erec.mypalmbeachclerk.com",
        "Palm Beach correct portal URL"
    ),
    (
        "app/workers/scrape_palm_beach_liens.py",
        "LN TX",
        "Palm Beach federal tax lien doc type"
    ),
    (
        "app/workers/scrape_palm_beach_liens.py",
        "LN ST",
        "Palm Beach state tax lien included"
    ),
    (
        "app/workers/scrape_palm_beach_liens.py",
        "reCAPTCHA detected",
        "Palm Beach reCAPTCHA handler"
    ),
    (
        "app/workers/scrape_palm_beach_liens.py",
        "def download_pdf(",
        "Palm Beach PDF download"
    ),
]

print("\nLeadFlow Script Version Check")
print("=" * 60)
all_ok = True
for filepath, signature, description in CHECKS:
    full_path = BASE_DIR / filepath
    if not full_path.exists():
        print(f"  MISSING  {filepath}")
        all_ok = False
        continue

    content = full_path.read_text(encoding="utf-8", errors="ignore")
    if signature in content:
        print(f"  ✓ OK     {filepath.split('/')[-1]:40} {description}")
    else:
        print(f"  ✗ OLD    {filepath.split('/')[-1]:40} {description}")
        print(f"           Expected: {signature!r}")
        all_ok = False

print("=" * 60)
if all_ok:
    print("  All scripts are up to date!\n")
else:
    print("  Some scripts need to be updated — copy from outputs folder.\n")
    print("  Files to copy:")
    seen = set()
    for filepath, signature, _ in CHECKS:
        full_path = BASE_DIR / filepath
        if not full_path.exists():
            continue
        content = full_path.read_text(encoding="utf-8", errors="ignore")
        if signature not in content and filepath not in seen:
            seen.add(filepath)
            fname = filepath.split("/")[-1]
            print(f"    {fname}")