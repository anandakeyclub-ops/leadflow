# analyze_logs.py
# LeadFlow Pipeline Log Analyzer
# Run from: C:\Users\Dana\Desktop\leadflow

import json
import glob
import os
from datetime import datetime

log_files = sorted(glob.glob('logs/pipeline/*.jsonl'), reverse=True)[:14]

print("=" * 70)
print("  LeadFlow Pipeline Log Analyzer")
print("=" * 70)

total_errors = 0
total_runs = 0
email_stats = {"sent": 0, "failed": 0, "runs": 0}
scraper_stats = {"total_liens": 0, "runs": 0}

for log_file in log_files:
    date = os.path.basename(log_file).replace('.jsonl', '')
    entries = []
    try:
        with open(log_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as e:
        print(f"\n  Could not read {log_file}: {e}")
        continue

    if not entries:
        continue

    print(f"\n{'=' * 70}")
    print(f"  {date}  ({len(entries)} runs)")
    print(f"{'=' * 70}")

    for entry in entries:
        run_type = entry.get('run_type', 'unknown')
        status   = entry.get('status', '?')
        duration = entry.get('duration', 0)
        started  = entry.get('started', '')[:16].replace('T', ' ')
        metrics  = entry.get('metrics', {})
        steps    = entry.get('steps', [])
        errors   = entry.get('errors', [])

        total_runs += 1
        icon = "✅" if status == "ok" else "❌"

        print(f"\n  {icon} [{started}] {run_type} — {status} ({duration:.1f}s)")

        # Email sequence details
        if run_type == "email_sends":
            email_stats["runs"] += 1
            sent   = metrics.get('total_sent', 0)
            failed = metrics.get('total_failed', 0)
            s1     = metrics.get('step1_sent', 0)
            s2     = metrics.get('step2_sent', 0)
            s3     = metrics.get('step3_sent', 0)
            email_stats["sent"]   += sent
            email_stats["failed"] += failed
            if sent > 0 or failed > 0:
                print(f"     Sent: {sent} | Failed: {failed}")
                print(f"     Step1: {s1} | Step2: {s2} | Step3: {s3}")
            for step in steps:
                if step.get('status') == 'error':
                    err = step.get('error', '')[:100]
                    print(f"     ERROR in {step['name']}: {err}")

        # Scraper details
        elif 'scraper' in run_type or 'scrape' in run_type:
            scraper_stats["runs"] += 1
            total = metrics.get('total', 0)
            counties = metrics.get('counties', [])
            scraper_stats["total_liens"] += total
            if total:
                print(f"     Counties: {counties} | Liens: {total}")
            for step in steps:
                detail = step.get('detail', '')
                if detail:
                    s_icon = "✅" if step.get('status') == 'ok' else "❌"
                    print(f"     {s_icon} {step['name']}: {detail}")

        # Blog/publish details
        elif 'blog' in run_type or 'publish' in run_type:
            published = metrics.get('published', 0)
            if published:
                print(f"     Published: {published} posts")

        # Generic error display
        for err in errors:
            total_errors += 1
            step = err.get('step', 'unknown')
            msg  = err.get('error', '')[:120]
            print(f"     ⚠ {step}: {msg}")

# Summary
print(f"\n{'=' * 70}")
print(f"  SUMMARY — Last {len(log_files)} days")
print(f"{'=' * 70}")
print(f"  Total runs    : {total_runs}")
print(f"  Total errors  : {total_errors}")
print(f"  Email runs    : {email_stats['runs']} | Sent: {email_stats['sent']} | Failed: {email_stats['failed']}")
print(f"  Scraper runs  : {scraper_stats['runs']} | Liens collected: {scraper_stats['total_liens']}")
print(f"{'=' * 70}\n")
