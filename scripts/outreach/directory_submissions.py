"""
directory_submissions.py
========================
One-time directory-submission kit builder for TaxCase Review.

Generates (does NOT submit anything):
  - data/outreach/directory_list.csv         tracking sheet for all directories
  - data/outreach/directory_submission_kit.md copy/paste kit + manual steps
  - data/outreach/api_submissions.py          API templates (GBP, Bing, Foursquare)

Run:
  python scripts/outreach/directory_submissions.py

Nothing here makes a network call or submits a listing. It only compiles the
target list and the copy-paste assets so submissions can be done (manually or
via API) after review.
"""
from __future__ import annotations

import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = BASE_DIR / "data" / "outreach"

# ── Business info ───────────────────────────────────────────────────────────────
BUSINESS = {
    "name":        "TaxCase Review",
    "url":         "https://taxcasereview.org",
    "phone":       "(561) 247-0678",
    "email":       "info@taxcasereview.org",
    "contact":     "Romy Cruz, Enrolled Agent",
    "categories":  ["Tax Resolution", "Tax Relief", "IRS Help",
                    "Small Business Tax", "Contractor Tax"],
}

# Three description lengths. Limits are inclusive ceilings; build() reports the
# real length of each so you can confirm they fit each directory's field cap.
DESCRIPTIONS = {
    50: "IRS tax resolution for contractors & businesses.",
    150: ("IRS tax resolution for contractors & small business owners. "
          "Former IRS officers & licensed tax pros. Serving FL, TX, AZ, GA, NY, IL."),
    300: ("TaxCase Review helps contractors & small business owners resolve IRS "
          "debt: liens, levies, payroll/941 problems & back taxes. Led by former "
          "IRS officers & licensed tax pros (EA Romy Cruz). Free 60-second risk "
          "assessment. Serving FL, TX, AZ, GA, NY, IL. Call (561) 247-0678."),
}

# ── Directory targets ───────────────────────────────────────────────────────────
# priority: high  = high-authority + directly relevant, do first
#           medium = relevant but lower authority / may need paid membership
#           low    = weak backlink value, poor fit, or membership-gated; do last
# requires_account / has_api are booleans. `note` carries honest caveats.
DIRECTORIES = [
    # ── Tax / Enrolled-Agent specific ───────────────────────────────────────────
    {"name": "NAEA — Find a Tax Expert", "group": "Tax",
     "url": "https://www.naea.org",
     "submission_url": "https://taxexperts.naea.org",
     "priority": "high", "requires_account": True, "has_api": False,
     "categories": "Enrolled Agent · Tax Resolution",
     "note": "Highest-fit directory — Romy is an EA. Requires NAEA membership."},
    {"name": "IRS RPO — Directory of Federal Tax Return Preparers", "group": "Tax",
     "url": "https://irs.treasury.gov/rpo/rpo.jsf",
     "submission_url": "https://irs.treasury.gov/rpo/rpo.jsf",
     "priority": "high", "requires_account": False, "has_api": False,
     "categories": "Enrolled Agent",
     "note": "EAs are auto-listed via PTIN/EA credential — verify the listing is "
             "present and complete rather than 'submitting'. Free, authoritative."},
    {"name": "NATP — Find a Tax Pro", "group": "Tax",
     "url": "https://www.natptax.com",
     "submission_url": "https://www.natptax.com/membership",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Professional · Tax Preparation",
     "note": "Requires NATP membership to appear in the public directory."},
    {"name": "TaxCure — Tax Pro Directory", "group": "Tax",
     "url": "https://taxcure.com",
     "submission_url": "https://taxcure.com/professionals",
     "priority": "high", "requires_account": True, "has_api": False,
     "categories": "Tax Resolution · IRS Help",
     "note": "Resolution-specific directory — strong topical relevance/backlink."},
    {"name": "TaxBuzz", "group": "Tax",
     "url": "https://www.taxbuzz.com",
     "submission_url": "https://www.taxbuzz.com/find-the-best-tax-preparer",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Preparation · Tax Resolution",
     "note": "Free professional profile; relevant niche."},
    {"name": "CPAdirectory", "group": "Tax",
     "url": "https://www.cpadirectory.com",
     "submission_url": "https://www.cpadirectory.com",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Preparation · Accounting",
     "note": "Accepts EAs/tax pros, not just CPAs — free basic listing."},

    # ── General business / local SEO ────────────────────────────────────────────
    {"name": "Google Business Profile", "group": "General",
     "url": "https://www.google.com/business",
     "submission_url": "https://business.google.com/create",
     "priority": "high", "requires_account": True, "has_api": True,
     "categories": "Tax Consultant · Tax Preparation Service",
     "note": "Single highest-impact listing for local SEO. API requires OAuth + "
             "Business Profile API access approval (see api_submissions.py)."},
    {"name": "Bing Places for Business", "group": "General",
     "url": "https://www.bingplaces.com",
     "submission_url": "https://www.bingplaces.com",
     "priority": "high", "requires_account": True, "has_api": True,
     "categories": "Tax Consultant · Financial Service",
     "note": "Can import directly from Google Business Profile. 'API' = bulk "
             "upload / partner API (approval-gated); template in api_submissions.py."},
    {"name": "Apple Business Connect", "group": "General",
     "url": "https://businessconnect.apple.com",
     "submission_url": "https://businessconnect.apple.com",
     "priority": "high", "requires_account": True, "has_api": False,
     "categories": "Professional Services · Finance",
     "note": "Powers Apple Maps/Siri — free, high value, manual UI only."},
    {"name": "Yelp for Business", "group": "General",
     "url": "https://www.yelp.com",
     "submission_url": "https://biz.yelp.com/signup",
     "priority": "high", "requires_account": True, "has_api": False,
     "categories": "Tax Services · Financial Services",
     "note": "High-authority backlink + review channel. Free claim."},
    {"name": "Better Business Bureau (BBB)", "group": "General",
     "url": "https://www.bbb.org",
     "submission_url": "https://www.bbb.org/get-listed",
     "priority": "high", "requires_account": True, "has_api": False,
     "categories": "Tax Return Preparation · Tax Consultant",
     "note": "Free basic listing; accreditation is paid (optional). Trust signal."},
    {"name": "Manta", "group": "General",
     "url": "https://www.manta.com",
     "submission_url": "https://www.manta.com/claim",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Services · Small Business",
     "note": "Free small-business listing/backlink."},
    {"name": "Yellow Pages (YP)", "group": "General",
     "url": "https://www.yellowpages.com",
     "submission_url": "https://accounts.yellowpages.com",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Return Preparation",
     "note": "Free basic listing; large legacy directory."},
    {"name": "Foursquare Places", "group": "General",
     "url": "https://foursquare.com",
     "submission_url": "https://location.foursquare.com/products/places/",
     "priority": "medium", "requires_account": True, "has_api": True,
     "categories": "Financial Service",
     "note": "Feeds many apps/maps. Places API supports programmatic add (template "
             "in api_submissions.py)."},
    {"name": "Hotfrog", "group": "General",
     "url": "https://www.hotfrog.com",
     "submission_url": "https://www.hotfrog.com/add-your-business",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Tax Services",
     "note": "Free listing/backlink."},
    {"name": "Cylex", "group": "General",
     "url": "https://www.cylex.us.com",
     "submission_url": "https://www.cylex.us.com",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Tax Consultant",
     "note": "Low-authority but free and quick."},

    # ── Legal / financial (attorney-leaning — fit caveats) ───────────────────────
    {"name": "Avvo", "group": "Legal",
     "url": "https://www.avvo.com",
     "submission_url": "https://www.avvo.com/claim-your-profile",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Tax · Legal",
     "note": "Attorney-focused. EA (non-attorney) may not qualify — verify before "
             "spending time."},
    {"name": "FindLaw Business Directory", "group": "Legal",
     "url": "https://www.findlaw.com",
     "submission_url": "https://lawyermarketing.findlaw.com",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Tax Law · Tax Resolution",
     "note": "Primarily attorney listings and largely paid — low fit for an EA."},
    {"name": "Justia", "group": "Legal",
     "url": "https://www.justia.com",
     "submission_url": "https://lawyers.justia.com",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Tax Law",
     "note": "Free but attorney-oriented (Justia Lawyer Directory). Fit caveat for "
             "a non-attorney EA."},

    # ── Small-business orgs ──────────────────────────────────────────────────────
    {"name": "SBA Local Assistance", "group": "SmallBiz",
     "url": "https://www.sba.gov",
     "submission_url": "https://www.sba.gov/local-assistance",
     "priority": "low", "requires_account": False, "has_api": False,
     "categories": "n/a",
     "note": "NOT a business-listing directory — it's a resource finder. No "
             "backlink/listing available. Included because requested; expect zero "
             "SEO value."},
    {"name": "SCORE", "group": "SmallBiz",
     "url": "https://www.score.org",
     "submission_url": "https://www.score.org/volunteer",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "n/a",
     "note": "Mentor directory, not a business-listing directory. Only relevant if "
             "Romy volunteers as a mentor (then she gets a profile). No standard "
             "listing path."},
    {"name": "Greater Miami Chamber of Commerce", "group": "SmallBiz",
     "url": "https://www.miamichamber.com",
     "submission_url": "https://www.miamichamber.com/membership",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Financial Services · Tax",
     "note": "Member directory backlink; paid membership required."},
    {"name": "Dallas Regional Chamber", "group": "SmallBiz",
     "url": "https://www.dallaschamber.org",
     "submission_url": "https://www.dallaschamber.org/membership",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Financial Services · Tax",
     "note": "Member directory backlink; paid membership required."},
    {"name": "Metro Atlanta Chamber", "group": "SmallBiz",
     "url": "https://www.metroatlantachamber.com",
     "submission_url": "https://www.metroatlantachamber.com/join",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Financial Services · Tax",
     "note": "Member directory backlink; paid membership required."},
    {"name": "Greater Phoenix Chamber", "group": "SmallBiz",
     "url": "https://www.phoenixchamber.com",
     "submission_url": "https://www.phoenixchamber.com/membership",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Financial Services · Tax",
     "note": "Member directory backlink; paid membership required."},
    {"name": "Greater Houston Partnership", "group": "SmallBiz",
     "url": "https://www.houston.org",
     "submission_url": "https://www.houston.org/membership",
     "priority": "medium", "requires_account": True, "has_api": False,
     "categories": "Financial Services · Tax",
     "note": "Member directory backlink; paid membership required."},

    # ── Contractor orgs (audience-fit, not service-provider listings) ────────────
    {"name": "NAHB (National Assoc. of Home Builders)", "group": "Contractor",
     "url": "https://www.nahb.org",
     "submission_url": "https://www.nahb.org/othersite/join",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Affiliate / Associate Member",
     "note": "Membership org for builders, not a service-provider directory. Low "
             "listing value; relevant only as an associate member for audience reach."},
    {"name": "AGC (Associated General Contractors)", "group": "Contractor",
     "url": "https://www.agc.org",
     "submission_url": "https://www.agc.org/membership",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Associate Member",
     "note": "Contractor membership org; associate membership only. Low listing fit."},
    {"name": "ABC Florida (Associated Builders & Contractors)", "group": "Contractor",
     "url": "https://www.abc.org",
     "submission_url": "https://www.abc.org/Membership",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Associate Member",
     "note": "FL chapter associate membership — audience reach more than backlink."},
    {"name": "ABC Texas / Houston chapter", "group": "Contractor",
     "url": "https://www.abctexo.org",
     "submission_url": "https://www.abctexo.org/membership",
     "priority": "low", "requires_account": True, "has_api": False,
     "categories": "Associate Member",
     "note": "TX chapter associate membership — audience reach more than backlink."},
]

CSV_COLUMNS = ["directory_name", "url", "submission_url", "priority",
               "requires_account", "has_api", "submitted", "submitted_date",
               "backlink_url"]


def _yn(v: bool) -> str:
    return "Yes" if v else "No"


def write_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for d in DIRECTORIES:
            w.writerow({
                "directory_name":   d["name"],
                "url":              d["url"],
                "submission_url":   d["submission_url"],
                "priority":         d["priority"],
                "requires_account": _yn(d["requires_account"]),
                "has_api":          _yn(d["has_api"]),
                "submitted":        "",
                "submitted_date":   "",
                "backlink_url":     "",
            })


def write_kit(path: Path) -> None:
    nap = (f"**Business name:** {BUSINESS['name']}  \n"
           f"**Website:** {BUSINESS['url']}  \n"
           f"**Phone:** {BUSINESS['phone']}  \n"
           f"**Email:** {BUSINESS['email']}  \n"
           f"**Primary contact:** {BUSINESS['contact']}  \n")
    cats = ", ".join(BUSINESS["categories"])

    groups: dict[str, list] = {}
    for d in DIRECTORIES:
        groups.setdefault(d["group"], []).append(d)
    group_titles = {
        "Tax": "Tax / Enrolled-Agent directories",
        "General": "General business & local SEO",
        "Legal": "Legal / financial directories",
        "SmallBiz": "Small-business organizations",
        "Contractor": "Contractor organizations",
    }

    lines = []
    lines.append("# TaxCase Review — Directory Submission Kit\n")
    lines.append("> Generated by `scripts/outreach/directory_submissions.py`. "
                 "Nothing has been submitted. Review, then submit manually or via "
                 "`api_submissions.py`.\n")

    lines.append("## NAP (keep identical everywhere — consistency drives local SEO)\n")
    lines.append(nap + "\n")
    lines.append(f"**Categories:** {cats}\n")

    lines.append("## Business descriptions (copy/paste)\n")
    for limit in (50, 150, 300):
        text = DESCRIPTIONS[limit]
        lines.append(f"**≤{limit} chars** (actual {len(text)}):\n\n> {text}\n")

    lines.append("## Category tags by directory\n")
    lines.append("| Directory | Suggested category |\n|---|---|")
    for d in DIRECTORIES:
        lines.append(f"| {d['name']} | {d['categories']} |")
    lines.append("")

    lines.append("## Submission targets & manual steps\n")
    lines.append("Priority key: **high** = do first (authority + fit) · "
                 "**medium** = relevant, may need paid membership · "
                 "**low** = weak/poor-fit, do last.\n")
    for g in ["Tax", "General", "Legal", "SmallBiz", "Contractor"]:
        if g not in groups:
            continue
        lines.append(f"### {group_titles[g]}\n")
        for d in groups[g]:
            flags = []
            if d["requires_account"]:
                flags.append("account required")
            if d["has_api"]:
                flags.append("API available")
            flag_str = f" _({', '.join(flags)})_" if flags else ""
            lines.append(f"- **{d['name']}** — `{d['priority'].upper()}`{flag_str}")
            lines.append(f"  - Submit at: {d['submission_url']}")
            lines.append(f"  - Category: {d['categories']}")
            lines.append(f"  - Note: {d['note']}")
        lines.append("")

    lines.append("## Generic manual submission checklist\n")
    lines.append("1. Use the **exact NAP** above — do not vary name/phone/address.\n"
                 "2. Pick the description that fits the field cap (50 / 150 / 300).\n"
                 "3. Choose the closest category from the table.\n"
                 "4. Website: always link to `https://taxcasereview.org` (or `/quiz` "
                 "where a single landing link is allowed).\n"
                 "5. After it goes live, paste the live listing URL into "
                 "`directory_list.csv` → `backlink_url`, set `submitted=Yes` and "
                 "`submitted_date`.\n")

    lines.append("## Honesty notes / do-not-waste-time\n")
    lines.append("- **SBA Local Assistance** and **SCORE** are not business-listing "
                 "directories (resource finder / mentor directory). No backlink — "
                 "skip unless Romy wants to volunteer as a SCORE mentor.\n"
                 "- **Avvo / FindLaw / Justia** are attorney-oriented; an Enrolled "
                 "Agent may not qualify. Verify eligibility before investing time.\n"
                 "- **NAHB / AGC / ABC** are membership orgs for builders/contractors, "
                 "not service-provider directories — value is audience reach, not a "
                 "clean backlink.\n"
                 "- **Chambers of commerce** require paid membership to appear in "
                 "the member directory.\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_api_templates(path: Path) -> None:
    code = '''\
"""
api_submissions.py  (GENERATED — templates, not wired to credentials)
=====================================================================
Programmatic-submission templates for the directories that expose an API.
None of these run automatically — each needs credentials/approval and an
explicit call. Review before use.

Targets:
  - Google Business Profile  (OAuth2 + Business Profile API access approval)
  - Bing Places              (bulk upload / partner API — approval-gated)
  - Foursquare Places        (Places API "add place")
"""
from __future__ import annotations

import os

BUSINESS = {
    "name":    "TaxCase Review",
    "url":     "https://taxcasereview.org",
    "phone":   "+15612470678",
    "email":   "info@taxcasereview.org",
    "primary_category": "Tax Consultant",
}


# ── Google Business Profile ──────────────────────────────────────────────────
# Requires: an approved Google Business Profile API project (the Business
# Profile APIs are allowlisted — request access in Google Cloud Console),
# OAuth2 credentials, and an existing/verified location to be created via the
# UI first in most cases. Listing CREATE via API is restricted; UPDATE is the
# common supported path. Template uses google-api-python-client.
def google_business_profile_upsert(account_id: str, location: dict) -> dict:
    """
    Create/patch a Business Profile location.
    `location` follows the Business Information API `locations` resource schema.
    Returns the API response. Raises if creds/scopes are missing.
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = os.getenv("GBP_OAUTH_TOKEN")
    if not token:
        raise RuntimeError("GBP_OAUTH_TOKEN not set — run the OAuth flow first "
                           "and ensure Business Profile API access is approved.")
    creds = Credentials(token=token)
    svc = build("mybusinessbusinessinformation", "v1", credentials=creds)
    parent = f"accounts/{account_id}"
    # CREATE is access-restricted for many categories; PATCH an existing
    # location instead when possible.
    return svc.accounts().locations().create(
        parent=parent, body=location).execute()


def google_location_payload() -> dict:
    return {
        "title": BUSINESS["name"],
        "phoneNumbers": {"primaryPhone": BUSINESS["phone"]},
        "websiteUri": BUSINESS["url"],
        "categories": {"primaryCategory": {"name": "categories/gcid:tax_consultant"}},
        # storefrontAddress required for a verified location:
        # "storefrontAddress": {"regionCode": "US", "addressLines": [...],
        #                        "locality": "...", "administrativeArea": "...",
        #                        "postalCode": "..."},
    }


# ── Bing Places ──────────────────────────────────────────────────────────────
# Bing Places has no simple public "create listing" REST endpoint. Two paths:
#   1) Import from a verified Google Business Profile (UI, fastest).
#   2) Bulk upload a tab-delimited file for multi-location chains, or the
#      Partner API which requires a partnership/approval.
# This emits a single-row bulk-upload file you can submit in the Bing Places UI.
def write_bing_bulk_file(path: str = "data/outreach/bing_places_bulk.txt") -> str:
    headers = ["StoreName", "AddressLine1", "City", "StateOrProvince",
               "PostalCode", "Country", "Phone", "BusinessUrl",
               "Categories", "Description"]
    row = [BUSINESS["name"], "", "", "", "", "US", BUSINESS["phone"],
           BUSINESS["url"], "Tax Consultant",
           "IRS tax resolution for contractors & small business owners."]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\\t".join(headers) + "\\n")
        f.write("\\t".join(row) + "\\n")
    return path


# ── Foursquare Places ────────────────────────────────────────────────────────
# Foursquare Places API supports adding a place. Requires a Foursquare service
# API key (FSQ_API_KEY). Note: adding a place is moderated.
def foursquare_add_place() -> dict:
    import requests
    key = os.getenv("FSQ_API_KEY")
    if not key:
        raise RuntimeError("FSQ_API_KEY not set.")
    resp = requests.post(
        "https://api.foursquare.com/v3/places",
        headers={"Authorization": key, "Content-Type": "application/json"},
        json={
            "name": BUSINESS["name"],
            "tel": BUSINESS["phone"],
            "url": BUSINESS["url"],
        },
        timeout=20,
    )
    return {"status": resp.status_code, "body": resp.text[:500]}


if __name__ == "__main__":
    print("Templates only. Set credentials and call a function explicitly.")
'''
    path.write_text(code, encoding="utf-8")


def print_table() -> None:
    order = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(DIRECTORIES, key=lambda d: (order[d["priority"]], d["group"]))
    print(f"\n{'#':>2}  {'PRIORITY':8} {'ACCT':4} {'API':3} {'DIRECTORY':44} SUBMISSION URL")
    print("-" * 130)
    for i, d in enumerate(rows, 1):
        print(f"{i:>2}  {d['priority'].upper():8} "
              f"{('Y' if d['requires_account'] else 'N'):4} "
              f"{('Y' if d['has_api'] else 'N'):3} "
              f"{d['name'][:44]:44} {d['submission_url']}")
    # summary
    by_pri = {"high": 0, "medium": 0, "low": 0}
    api_n = sum(1 for d in DIRECTORIES if d["has_api"])
    for d in DIRECTORIES:
        by_pri[d["priority"]] += 1
    print("-" * 130)
    print(f"  Total: {len(DIRECTORIES)} directories  |  "
          f"high {by_pri['high']} · medium {by_pri['medium']} · low {by_pri['low']}  |  "
          f"{api_n} with API")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "directory_list.csv"
    kit_path = OUT_DIR / "directory_submission_kit.md"
    api_path = OUT_DIR / "api_submissions.py"

    # Report description lengths (soft check against the named caps).
    print("Description lengths:")
    for limit, text in DESCRIPTIONS.items():
        flag = "OK" if len(text) <= limit else f"OVER by {len(text) - limit}"
        print(f"  <={limit}: {len(text)} chars [{flag}]")

    write_csv(csv_path)
    write_kit(kit_path)
    write_api_templates(api_path)

    print(f"\nWrote:\n  {csv_path}\n  {kit_path}\n  {api_path}")
    print_table()
    print("\nNothing submitted. Review the kit before submitting anything.")


if __name__ == "__main__":
    main()
