import csv
import re
from pathlib import Path
from typing import Optional, Tuple

from app.core.db import get_connection


BASE_DIR = Path(__file__).resolve().parents[2]
REPLIES_FILE = BASE_DIR / "data" / "exports" / "manual_replies.csv"


IGNORE_PATTERNS = [
    r"\bout of office\b",
    r"\bautomatic reply\b",
    r"\bauto(?:matic)?\s?response\b",
    r"\bautoreply\b",
    r"\bvacation responder\b",
    r"\bdelivery status notification\b",
    r"\bmail delivery\b",
    r"\bundeliverable\b",
]

UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bremove\b.*\blist\b",
    r"\bopt out\b",
    r"\bstop emailing\b",
    r"\bdo not contact\b",
    r"\bdon't contact me\b",
]

STRONG_POSITIVE_PATTERNS = [
    r"\bcall me\b",
    r"\blet'?s talk\b",
    r"\bwhen can we talk\b",
    r"\bbook\b",
    r"\bschedule\b",
    r"\bset up a call\b",
    r"\bcan you help\b",
    r"\bi need help\b",
    r"\bwhat are my options\b",
    r"\binterested\b",
    r"\byes\b.*\b(call|talk|help|review|options)\b",
]

POSITIVE_PATTERNS = [
    r"\byes\b",
    r"\bmaybe\b",
    r"\bpossibly\b",
    r"\bcurious\b",
    r"\bopen to\b",
    r"\bmore information\b",
    r"\binfo\b",
    r"\bdetails\b",
    r"\bhow does this work\b",
    r"\bwhat does this cost\b",
    r"\bcan you explain\b",
    r"\blooking into\b",
    r"\bneed to resolve\b",
    r"\bowe\b",
    r"\bpayment plan\b",
    r"\binstallment agreement\b",
    r"\boffer in compromise\b",
]

NEGATIVE_PATTERNS = [
    r"\bnot interested\b",
    r"\bno thanks\b",
    r"\bno thank you\b",
    r"\balready handled\b",
    r"\balready resolved\b",
    r"\balready working with someone\b",
    r"\bwrong person\b",
    r"\bwrong email\b",
]


def normalize_text(subject: str, body: str) -> str:
    text = f"{subject or ''}\n{body or ''}".lower()
    text = text.replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_reply(subject: str, body: str) -> Tuple[str, str]:
    text = normalize_text(subject, body)

    if not text:
        return "review", "empty_message"

    if matches_any(text, IGNORE_PATTERNS):
        return "ignore", "auto_reply"

    if matches_any(text, UNSUBSCRIBE_PATTERNS):
        return "unsubscribe", "unsubscribe_request"

    if matches_any(text, NEGATIVE_PATTERNS):
        return "negative", "negative_reply"

    if matches_any(text, STRONG_POSITIVE_PATTERNS):
        return "qualified", "strong_positive_reply"

    if matches_any(text, POSITIVE_PATTERNS):
        return "positive", "positive_reply"

    return "review", "manual_review"


def safe_excerpt(text: str, limit: int = 1000) -> str:
    text = (text or "").strip()
    return text[:limit]


def parse_received_at(raw_value: Optional[str]) -> Optional[str]:
    value = (raw_value or "").strip()
    return value if value else None


def upsert_outreach_event(cur, lead_id: int, event_type: str, notes: str, created_at: Optional[str] = None) -> None:
    if created_at:
        cur.execute(
            """
            INSERT INTO outreach_events (
                lead_id,
                channel,
                event_type,
                template_name,
                notes,
                created_at
            )
            VALUES (%s, 'email', %s, 'manual_reply_import', %s, %s::timestamp)
            """,
            (lead_id, event_type, notes, created_at),
        )
    else:
        cur.execute(
            """
            INSERT INTO outreach_events (
                lead_id,
                channel,
                event_type,
                template_name,
                notes
            )
            VALUES (%s, 'email', %s, 'manual_reply_import', %s)
            """,
            (lead_id, event_type, notes),
        )


def update_lead_status(cur, lead_id: int, new_status: str) -> None:
    cur.execute(
        """
        UPDATE matched_leads
        SET
            lead_status = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (new_status, lead_id),
    )


def already_processed(cur, lead_id: int, body: str) -> bool:
    snippet = safe_excerpt(body, 250)
    cur.execute(
        """
        SELECT 1
        FROM outreach_events
        WHERE lead_id = %s
          AND channel = 'email'
          AND event_type IN (
              'reply_positive',
              'reply_qualified',
              'reply_negative',
              'unsubscribe',
              'reply_review',
              'reply_ignored'
          )
          AND notes ILIKE %s
        LIMIT 1
        """,
        (lead_id, f"%{snippet}%"),
    )
    return cur.fetchone() is not None


def main() -> None:
    if not REPLIES_FILE.exists():
        print(f"No reply file found at {REPLIES_FILE}")
        print("Expected CSV columns: lead_id, from_email, subject, body, received_at")
        return

    conn = get_connection()

    processed = 0
    ignored = 0
    unsubscribed = 0
    positive = 0
    qualified = 0
    negative = 0
    review = 0
    duplicates = 0

    try:
        with conn:
            with conn.cursor() as cur:
                with REPLIES_FILE.open(newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)

                    for row in reader:
                        raw_lead_id = (row.get("lead_id") or "").strip()
                        subject = row.get("subject", "")
                        body = row.get("body", "")
                        from_email = (row.get("from_email") or "").strip()
                        received_at = parse_received_at(row.get("received_at"))

                        if not raw_lead_id:
                            continue

                        lead_id = int(raw_lead_id)

                        if already_processed(cur, lead_id, body):
                            duplicates += 1
                            continue

                        classification, reason = classify_reply(subject, body)
                        notes = (
                            f"from_email={from_email}\n"
                            f"reason={reason}\n"
                            f"subject={safe_excerpt(subject, 250)}\n"
                            f"body={safe_excerpt(body, 1000)}"
                        )

                        if classification == "ignore":
                            upsert_outreach_event(cur, lead_id, "reply_ignored", notes, received_at)
                            ignored += 1

                        elif classification == "unsubscribe":
                            update_lead_status(cur, lead_id, "do_not_contact")
                            upsert_outreach_event(cur, lead_id, "unsubscribe", notes, received_at)
                            unsubscribed += 1
                            processed += 1

                        elif classification == "negative":
                            update_lead_status(cur, lead_id, "not_interested")
                            upsert_outreach_event(cur, lead_id, "reply_negative", notes, received_at)
                            negative += 1
                            processed += 1

                        elif classification == "qualified":
                            update_lead_status(cur, lead_id, "qualified")
                            upsert_outreach_event(cur, lead_id, "reply_qualified", notes, received_at)

                            cur.execute(
                                """
                                UPDATE outreach_events
                                SET followup_due_at = NOW()
                                WHERE lead_id = %s
                                  AND channel = 'email'
                                  AND event_type = 'email_sent'
                                  AND followup_due_at IS NOT NULL
                                """,
                                (lead_id,),
                            )

                            qualified += 1
                            processed += 1

                        elif classification == "positive":
                            update_lead_status(cur, lead_id, "replied")
                            upsert_outreach_event(cur, lead_id, "reply_positive", notes, received_at)
                            positive += 1
                            processed += 1

                        else:
                            update_lead_status(cur, lead_id, "review")
                            upsert_outreach_event(cur, lead_id, "reply_review", notes, received_at)
                            review += 1
                            processed += 1

        print(f"Replies processed: {processed}")
        print(f"Qualified: {qualified}")
        print(f"Positive: {positive}")
        print(f"Negative: {negative}")
        print(f"Unsubscribed: {unsubscribed}")
        print(f"Ignored: {ignored}")
        print(f"Review: {review}")
        print(f"Duplicates skipped: {duplicates}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()