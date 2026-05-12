import base64
import os
import re
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from app.core.db import get_connection
from app.services.gmail_client import ensure_label, get_gmail_service

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

GMAIL_REPLY_LABEL = os.getenv("GMAIL_REPLY_LABEL", "leadflow-processed")
GMAIL_REPLY_QUERY = os.getenv("GMAIL_REPLY_QUERY", "in:anywhere newer_than:30d")


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


def matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_reply(subject: str, body: str):
    text = f"{subject or ''}\n{body or ''}".lower().strip()

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


def extract_headers(payload):
    headers = {}
    for h in payload.get("headers", []):
        headers[h["name"].lower()] = h["value"]
    return headers


def decode_body(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="ignore")


def extract_plain_text(payload) -> str:
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain" and payload.get("body", {}).get("data"):
        return decode_body(payload["body"]["data"])

    for part in payload.get("parts", []) or []:
        result = extract_plain_text(part)
        if result:
            return result

    if payload.get("body", {}).get("data"):
        return decode_body(payload["body"]["data"])

    return ""


def normalize_email(raw_from: str) -> str:
    _, addr = parseaddr(raw_from or "")
    return (addr or "").strip().lower()


def already_processed(cur, gmail_message_id: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM outreach_events
        WHERE channel = 'email'
          AND event_type IN (
              'reply_positive',
              'reply_qualified',
              'reply_negative',
              'unsubscribe',
              'reply_review',
              'reply_ignored',
              'reply_unmatched'
          )
          AND notes ILIKE %s
        LIMIT 1
        """,
        (f"%gmail_message_id={gmail_message_id}%",),
    )
    return cur.fetchone() is not None


def find_lead_by_email(cur, from_email: str) -> Optional[int]:
    cur.execute(
        """
        SELECT ct.lead_id
        FROM contacts ct
        WHERE LOWER(ct.email) = LOWER(%s)
        LIMIT 1
        """,
        (from_email,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def find_lead_by_thread_sent_event(cur, thread_id: str) -> Optional[int]:
    cur.execute(
        """
        SELECT lead_id
        FROM outreach_events
        WHERE channel = 'email'
          AND event_type = 'email_sent'
          AND notes ILIKE %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (f"%thread_id={thread_id}%",),
    )
    row = cur.fetchone()
    return row[0] if row else None


def find_lead_from_thread_headers(service, thread_id: str) -> Optional[int]:
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    for msg in thread.get("messages", []):
        payload = msg.get("payload", {})
        headers = extract_headers(payload)
        lead_id = headers.get("x-leadflow-lead-id")
        if lead_id and str(lead_id).isdigit():
            return int(lead_id)
    return None


def log_event(cur, lead_id, event_type, notes):
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
        VALUES (%s, 'email', %s, 'gmail_reply_sync', %s, NOW())
        """,
        (lead_id, event_type, notes),
    )


def update_status(cur, lead_id, status):
    cur.execute(
        """
        UPDATE matched_leads
        SET lead_status = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (status, lead_id),
    )


def main():
    service = get_gmail_service()
    processed_label_id = ensure_label(service, GMAIL_REPLY_LABEL)

    resp = service.users().messages().list(
        userId="me",
        q=GMAIL_REPLY_QUERY,
        maxResults=100,
    ).execute()

    messages = resp.get("messages", [])
    if not messages:
        print("No Gmail replies found.")
        return

    conn = get_connection()
    processed = 0
    matched = 0
    unmatched = 0

    try:
        with conn:
            with conn.cursor() as cur:
                for m in messages:
                    gmail_id = m["id"]

                    full = service.users().messages().get(
                        userId="me",
                        id=gmail_id,
                        format="full",
                    ).execute()

                    payload = full.get("payload", {})
                    headers = extract_headers(payload)
                    subject = headers.get("subject", "")
                    from_email = normalize_email(headers.get("from", ""))
                    thread_id = full.get("threadId", "")
                    body = extract_plain_text(payload)

                    if already_processed(cur, gmail_id):
                        service.users().messages().modify(
                            userId="me",
                            id=gmail_id,
                            body={"addLabelIds": [processed_label_id]},
                        ).execute()
                        continue

                    lead_id = (
                        find_lead_from_thread_headers(service, thread_id)
                        or find_lead_by_email(cur, from_email)
                        or find_lead_by_thread_sent_event(cur, thread_id)
                    )

                    notes = (
                        f"gmail_message_id={gmail_id}\n"
                        f"thread_id={thread_id}\n"
                        f"from_email={from_email}\n"
                        f"subject={subject[:250]}\n"
                        f"body={body[:1000]}"
                    )

                    if not lead_id:
                        log_event(cur, None, "reply_unmatched", notes)
                        service.users().messages().modify(
                            userId="me",
                            id=gmail_id,
                            body={"addLabelIds": [processed_label_id]},
                        ).execute()
                        processed += 1
                        unmatched += 1
                        continue

                    classification, reason = classify_reply(subject, body)
                    notes = notes + f"\nreason={reason}"

                    if classification == "ignore":
                        log_event(cur, lead_id, "reply_ignored", notes)

                    elif classification == "unsubscribe":
                        update_status(cur, lead_id, "do_not_contact")
                        log_event(cur, lead_id, "unsubscribe", notes)

                    elif classification == "negative":
                        update_status(cur, lead_id, "not_interested")
                        log_event(cur, lead_id, "reply_negative", notes)

                    elif classification == "qualified":
                        update_status(cur, lead_id, "qualified")
                        log_event(cur, lead_id, "reply_qualified", notes)

                    elif classification == "positive":
                        update_status(cur, lead_id, "replied")
                        log_event(cur, lead_id, "reply_positive", notes)

                    else:
                        update_status(cur, lead_id, "review")
                        log_event(cur, lead_id, "reply_review", notes)

                    service.users().messages().modify(
                        userId="me",
                        id=gmail_id,
                        body={"addLabelIds": [processed_label_id]},
                    ).execute()
                    processed += 1
                    matched += 1

        print(f"Gmail replies processed: {processed}")
        print(f"Matched: {matched}")
        print(f"Unmatched: {unmatched}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()