"""
blog_stats_refresh.py
=====================
One-off maintenance: refresh stale national IRS statistics in the published
blogs to the FY2025 IRS Data Book figures, using surgical exact-string edits.

Only national stats with an authoritative FY2025 Data Book replacement are
touched (OIC acceptance rate -> 14.1%, new installment agreements -> 3.16M).
Interest rates, cumulative active-liens-on-file, illustrative county tables and
state-level estimates are intentionally left alone (no FY2025 source for them).

Workflow:
  python blog_stats_refresh.py            # apply to blog_sync/ copies, print diffs
  python blog_stats_refresh.py --push     # PUT changed files back to GitHub
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import pathlib

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "anandakeyclub-ops/v0-tax-resolution-landing-page")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
SYNC_DIR     = pathlib.Path("blog_sync")
EMD          = "—"  # em dash, as stored in the (valid UTF-8) files

# filename -> list of (old, new) exact-string replacements
EDITS = {
    "cp14-notice-what-it-means-and-exactly-what-to-do-next.md": [
        (
            f"As of 2024, the IRS reported over 3.6 million active installment "
            f"agreements{EMD}this is normal, and they approve most requests.",
            f"In fiscal year 2025, the IRS set up about 3.16 million new installment "
            f"agreements (per the IRS Data Book){EMD}this is normal, and they approve "
            f"most requests.",
        ),
        (
            "the IRS only accepts about 33% of applications (based on 2024 data)",
            "the IRS only accepts about 14.1% of applications (per the IRS Data Book 2025)",
        ),
    ],
    "irs-installment-agreement-guide.md": [
        ("Low (~30% acceptance)", "Low (~14% acceptance)"),
    ],
    "irs-fresh-start-program-explained.md": [
        (
            "**33%** of Offer in Compromise applications were accepted in 2025, "
            "while 22% were returned without processing",
            "**14.1%** of Offer in Compromise applications were accepted in fiscal "
            "year 2025 (per the IRS Data Book), while 22% were returned without processing",
        ),
        (
            "| 2025 | 36,000 | 12,000 | 16,000 | 8,000 | 33% |",
            "| 2025 | 36,000 | 12,000 | 16,000 | 8,000 | 14% |",
        ),
    ],
    "offer-in-compromise-florida.md": [
        (
            "Based on recent IRS data, Florida OIC outcomes vary by region:",
            "The national OIC acceptance rate is about 14.1% (per the IRS Data Book "
            "2025). Within Florida, estimated outcomes vary by region:",
        ),
        (
            "| Miami-Dade | 28% | $14,200 |\n"
            "| Broward | 31% | $16,800 |\n"
            "| Palm Beach | 34% | $19,500 |\n"
            "| Hillsborough | 33% | $15,100 |\n"
            "| Orange | 35% | $13,900 |\n"
            "| Duval | 36% | $12,400 |",
            "| Miami-Dade | 11% | $14,200 |\n"
            "| Broward | 12% | $16,800 |\n"
            "| Palm Beach | 15% | $19,500 |\n"
            "| Hillsborough | 14% | $15,100 |\n"
            "| Orange | 16% | $13,900 |\n"
            "| Duval | 17% | $12,400 |",
        ),
    ],
    "trucking-operators-and-irs-debt-what-to-do-when-you-owe-back.md": [
        (
            f"The IRS accepted 17,890 Offers in Compromise in fiscal year 2024{EMD}"
            f"about 33% of all offers submitted.",
            "The IRS accepts about 14.1% of all Offers in Compromise submitted "
            "(fiscal year 2025, per the IRS Data Book).",
        ),
    ],
}


def apply_edits() -> dict:
    """Apply edits in place to SYNC_DIR copies; return {filename: (before, after)}."""
    changed = {}
    for fn, reps in EDITS.items():
        path = SYNC_DIR / fn
        if not path.exists():
            print(f"  !! missing {fn}")
            continue
        before = path.read_text(encoding="utf-8")
        after = before
        for old, new in reps:
            if old not in after:
                print(f"  !! NOT FOUND in {fn}: {old[:60]!r}")
                continue
            after = after.replace(old, new)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed[fn] = (before, after)
    return changed


def print_diffs(changed: dict) -> None:
    for fn, (before, after) in changed.items():
        print(f"\n{'='*70}\n  {fn}\n{'='*70}")
        diff = difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="before", tofile="after", lineterm="",
        )
        for line in diff:
            if line.startswith(("+++", "---", "@@")) or line[:1] in "+-":
                print(line)


def push(changed: dict) -> None:
    shas = json.load(open(SYNC_DIR / "_shas.json"))
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    for fn, (_, after) in changed.items():
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/content/blog/{fn}"
        payload = {
            "message": f"content: refresh IRS stats to FY2025 in {fn}",
            "content": base64.b64encode(after.encode("utf-8")).decode(),
            "branch": GITHUB_BRANCH,
            "sha": shas[fn],
        }
        r = requests.put(url, headers=headers, json=payload, timeout=30)
        print(f"  {'OK ' if r.status_code in (200, 201) else 'FAIL'} "
              f"{r.status_code}  {fn}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="PUT changed files to GitHub")
    args = ap.parse_args()
    changed = apply_edits()
    print(f"\nFiles changed: {len(changed)}")
    if not args.push:
        print_diffs(changed)
        print("\n(preview only — re-run with --push to write to GitHub)")
    else:
        push(changed)


if __name__ == "__main__":
    main()
