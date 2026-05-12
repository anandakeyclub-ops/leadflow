# app/workers/tcr_abandon_worker.py
import time, json
from datetime import datetime, timezone
from app.core.db import get_connection

# Corrected order: quiz → booking → payment
RULES = [
    {
        "name":        "lp_abandon",
        "trigger":     "lp_view",
        "stop":        "quiz_start",
        "timeout_min": 30,
        "sequence":    "lp_abandon",
        "req_status":  "VISITOR",
    },
    {
        "name":        "quiz_abandon",
        "trigger":     "quiz_start",
        "stop":        "quiz_complete",
        "timeout_min": 30,
        "sequence":    "quiz_abandon",
        "req_status":  "QUIZ_STARTED",
    },
    {
        "name":        "no_booking",
        "trigger":     "quiz_complete",
        "stop":        "booking_complete",
        "timeout_min": 15,
        "sequence":    "no_booking",
        "req_status":  "QUIZ_COMPLETED",
    },
    {
        "name":        "no_payment",
        "trigger":     "booking_complete",
        "stop":        "payment_success",
        "timeout_min": 30,
        "sequence":    "no_payment",
        "req_status":  "BOOKING_COMPLETED",
    },
]

def check_abandons():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for rule in RULES:
                cur.execute(f"""
                    SELECT DISTINCT l.id, l.email, l.status
                    FROM tcr_leads l
                    JOIN tcr_events e ON e.lead_id = l.id
                        AND e.event_type = %s
                        AND e.event_time < NOW() - INTERVAL '{rule["timeout_min"]} minutes'
                    WHERE l.status = %s
                    AND l.unsubscribed_at IS NULL
                    AND l.current_sequence IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM tcr_events e2
                        WHERE e2.lead_id = l.id
                        AND e2.event_type = %s
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM tcr_events e3
                        WHERE e3.lead_id = l.id
                        AND e3.event_type = 'payment_success'
                    )
                """, (rule["trigger"], rule["req_status"], rule["stop"]))

                candidates = cur.fetchall()
                for (lead_id, email, status) in candidates:
                    print(f"[tcr worker] {rule['name']}: {email}")
                    _start_sequence(cur, lead_id, rule["sequence"])

            conn.commit()
    finally:
        conn.close()


def _start_sequence(cur, lead_id, sequence_key):
    # Safety check
    cur.execute("""
        SELECT unsubscribed_at, paid_at, status FROM tcr_leads WHERE id = %s
    """, (lead_id,))
    lead = cur.fetchone()
    if not lead or lead[0]:  # unsubscribed
        return
    if lead[1] and sequence_key != "no_payment":  # already paid
        return
    if lead[2] == "CUSTOMER":
        return

    # Load steps
    cur.execute("""
        SELECT step_number, delay_hours FROM tcr_email_sequences
        WHERE sequence_key = %s AND active = TRUE
        ORDER BY step_number
    """, (sequence_key,))
    steps = cur.fetchall()

    for step_number, delay_hours in steps:
        cur.execute("""
            INSERT INTO tcr_scheduled_emails
                (lead_id, sequence_key, step_number, send_at)
            VALUES (%s, %s, %s, NOW() + %s * INTERVAL '1 hour')
            ON CONFLICT DO NOTHING
        """, (lead_id, sequence_key, step_number, float(delay_hours)))

    cur.execute("""
        UPDATE tcr_leads SET current_sequence = %s, sequence_started_at = NOW()
        WHERE id = %s
    """, (sequence_key, lead_id))


def run():
    print("[tcr worker] starting — checking every 10 minutes")
    while True:
        try:
            check_abandons()
        except Exception as e:
            print(f"[tcr worker] error: {e}")
        time.sleep(600)


if __name__ == "__main__":
    run()