"""
fix_env_and_spam.py
===================
1. Fixes .env to have only one TRACKING_BASE_URL (the ngrok one)
2. Removes spam trap emails from lien_dbpr_contacts
3. Verifies tracking pixel URL is correct

Run: python fix_env_and_spam.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV  = BASE / ".env"

# ── Step 1: Fix .env ──────────────────────────────────────────────────────────
print("=== Step 1: Fix .env TRACKING_BASE_URL ===")

lines    = ENV.read_text(encoding="utf-8").splitlines()
kept     = []
tracking = "TRACKING_BASE_URL=https://deflator-rover-outtakes.ngrok-free.dev"
added    = False
removed  = 0

for line in lines:
    if line.strip().startswith("TRACKING_BASE_URL"):
        removed += 1
        if not added:
            kept.append(tracking)
            added = True
            print(f"  Keeping : {tracking}")
        else:
            print(f"  Removing: {line.strip()}")
    else:
        kept.append(line)

if not added:
    kept.append(tracking)
    print(f"  Added   : {tracking}")

ENV.write_text("\n".join(kept) + "\n", encoding="utf-8")
print(f"  Fixed: removed {removed} duplicate(s), kept ngrok URL\n")

# ── Step 2: Remove spam trap emails ──────────────────────────────────────────
print("=== Step 2: Remove spam trap emails ===")

sys.path.insert(0, str(BASE))
try:
    from app.core.db import get_connection
    conn = get_connection()
    cur  = conn.cursor()

    # Find spam traps
    cur.execute("""
        SELECT email FROM lien_dbpr_contacts
        WHERE email ILIKE '%do.not.spam%'
           OR email ILIKE '%spam%'
           OR email ILIKE '%honeypot%'
           OR email ILIKE '%trap%'
           OR email ILIKE '%noemail%'
           OR email ILIKE '%invalid%'
           OR email ILIKE '%fake%'
           OR email ILIKE '%test@%'
           OR email ILIKE '%example.com%'
    """)
    spam_emails = [r[0] for r in cur.fetchall()]

    if spam_emails:
        print(f"  Found {len(spam_emails)} spam trap email(s):")
        for e in spam_emails:
            print(f"    {e}")

        cur.execute("""
            UPDATE lien_dbpr_contacts
            SET email = NULL
            WHERE email ILIKE '%do.not.spam%'
               OR email ILIKE '%spam%'
               OR email ILIKE '%honeypot%'
               OR email ILIKE '%trap%'
               OR email ILIKE '%noemail%'
               OR email ILIKE '%invalid%'
               OR email ILIKE '%fake%'
               OR email ILIKE '%test@%'
               OR email ILIKE '%example.com%'
        """)
        conn.commit()
        print(f"  Removed {cur.rowcount} spam trap email(s)")
    else:
        print("  No spam trap emails found")

    # Also mark do.not.spam in email_sends as failed
    cur.execute("""
        UPDATE email_sends
        SET status = 'spam_trap'
        WHERE to_email ILIKE '%do.not.spam%'
           OR to_email ILIKE '%spam%'
    """)
    conn.commit()
    print(f"  Flagged {cur.rowcount} email_sends as spam_trap")
    conn.close()

except Exception as e:
    print(f"  DB error: {e}")

# ── Step 3: Verify tracking URL ───────────────────────────────────────────────
print("\n=== Step 3: Verify tracking URL ===")

# Reload .env
from dotenv import load_dotenv, dotenv_values
vals = dotenv_values(ENV)
tracking_url = vals.get("TRACKING_BASE_URL", "NOT SET")
print(f"  TRACKING_BASE_URL = {tracking_url}")

if "deflator-rover-outtakes" in tracking_url:
    print("  ✅ Correct — ngrok URL set")
elif "localhost" in tracking_url or "127.0.0.1" in tracking_url:
    print("  ❌ Still localhost — fix failed, check .env manually")
else:
    print(f"  ⚠ Unexpected value: {tracking_url}")

print("\n=== Done ===")
print("Restart any running Python processes to pick up the new .env")
print("Then test: python -m app.workers.send_email_sequence --auto --limit 1 --dry-run")
print("Should show: Tracking: https://deflator-rover-outtakes.ngrok-free.dev")
