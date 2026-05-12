import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

from app.core.db import get_connection


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

EXPORT_DIR = BASE_DIR / "data" / "exports" / "email_lists"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_RESEND_WINDOW_HOURS = int(os.getenv("EMAIL_RESEND_WINDOW_HOURS", "120"))
MIN_LEAD_SCORE = int(os.getenv("MIN_LEAD_SCORE", "60"))

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def clean_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def extract_email(*values: Optional[str]) -> str:
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        match = EMAIL_RE.search(text)
        if match:
            return match.group(0).lower()
    return ""


def extract_name_before_email(text: Optional[str], fallback: str = "there") -> str:
    text = clean_text(text)
    if not text:
        return fallback

    match = EMAIL_RE.search(text)
    if match:
        before = clean_text(text[: match.start()])
        # Accela often stores: PERSON EMAIL COMPANY ADDRESS
        # Keep the likely person name before the email.
        if before:
            parts = before.split()
            if len(parts) >= 2:
                return " ".join(parts[:3]).title()
            return before.title()

    return fallback


def has_column(cur, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return cur.fetchone() is not None


def get_rows(cur) -> List[Tuple]:
    # contacts may not exist for contractor permit outreach. Use LEFT JOIN and fall back
    # to contractor email embedded in normalized_permits.business_name.
    cur.execute(
        """
        SELECT
            ml.id AS lead_id,
            COALESCE(ct.full_name, '') AS contact_full_name,
            COALESCE(ct.email, '') AS contact_email,
            COALESCE(ct.mailing_address_1, '') AS mailing_address_1,
            COALESCE(ct.city, '') AS city,
            COALESCE(ct.state, '') AS state,
            COALESCE(ct.zip, '') AS zip,
            COALESCE(c.county_name, '') AS county_name,
            COALESCE(ml.lead_score, 0) AS lead_score,
            COALESCE(ml.lead_status, '') AS lead_status,
            COALESCE(np.business_name, nl.business_name, '') AS business_name,
            COALESCE(np.address_1, nl.address_1, '') AS property_address,
            COALESCE(np.project_description, np.permit_type, '') AS permit_context,
            COALESCE(np.issued_date::text, '') AS permit_date,
            COALESCE(nl.amount, 0) AS lien_amount,
            COALESCE(nl.filed_date::text, '') AS lien_date,
            COALESCE(np.permit_number, '') AS permit_number,
            COALESCE(np.owner_name, '') AS owner_name,
            COALESCE(nl.debtor_name, '') AS debtor_name
        FROM matched_leads ml
        LEFT JOIN contacts ct
            ON ml.id = ct.lead_id
        LEFT JOIN counties c
            ON ml.county_id = c.id
        LEFT JOIN normalized_permits np
            ON ml.permit_id = np.id
        LEFT JOIN normalized_liens nl
            ON ml.lien_id = nl.id
        WHERE COALESCE(ml.lead_score, 0) >= %s
          AND COALESCE(ml.lead_status, 'new') NOT IN ('replied', 'booked', 'closed', 'do_not_contact')
          AND ct.email IS NOT NULL
          AND ct.email NOT LIKE '%leadflow.invalid'
          AND ct.email NOT LIKE '%noemail%'
          AND ct.email LIKE '%@%'
          AND ct.enrichment_status LIKE 'matched_dbpr%'
          AND NOT EXISTS (
                SELECT 1
                FROM outreach_events oe
                WHERE oe.lead_id = ml.id
                  AND oe.channel = 'email'
                  AND oe.event_type = 'email_sent'
                  AND oe.created_at >= NOW() - (%s || ' hours')::interval
          )
        ORDER BY
            ml.lead_score DESC,
            ml.created_at DESC
        """,
        (MIN_LEAD_SCORE, str(EMAIL_RESEND_WINDOW_HOURS)),
    )
    return cur.fetchall()


def build_export_rows(db_rows: List[Tuple]) -> List[List]:
    exported: List[List] = []
    seen_emails: Dict[str, int] = {}

    for row in db_rows:
        (
            lead_id,
            contact_full_name,
            contact_email,
            mailing_address_1,
            city,
            state,
            zip_code,
            county_name,
            lead_score,
            lead_status,
            business_name,
            property_address,
            permit_context,
            permit_date,
            lien_amount,
            lien_date,
            permit_number,
            owner_name,
            debtor_name,
        ) = row

        email = extract_email(contact_email, business_name)
        if not email:
            continue

        # De-dupe by recipient. Keep the highest-scoring/first row from SQL ordering.
        if email in seen_emails:
            continue
        seen_emails[email] = int(lead_id)

        full_name = clean_text(contact_full_name)
        if not full_name:
            full_name = extract_name_before_email(business_name, fallback="there")

        exported.append(
            [
                lead_id,
                full_name,
                email,
                mailing_address_1,
                city,
                state,
                zip_code,
                county_name,
                lead_score,
                lead_status or "new",
                business_name,
                property_address,
                permit_context,
                permit_date,
                lien_amount,
                lien_date,
                permit_number,
                owner_name,
                debtor_name,
                "contact_email" if clean_text(contact_email) else "permit_business_name_email",
            ]
        )

    return exported


def main() -> None:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            db_rows = get_rows(cur)
            rows = build_export_rows(db_rows)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = EXPORT_DIR / f"email_campaign_list_{timestamp}.csv"

        headers = [
            "lead_id",
            "full_name",
            "email",
            "mailing_address_1",
            "city",
            "state",
            "zip",
            "county_name",
            "lead_score",
            "lead_status",
            "business_name",
            "property_address",
            "permit_context",
            "permit_date",
            "lien_amount",
            "lien_date",
            "permit_number",
            "owner_name",
            "debtor_name",
            "email_source",
        ]

        with export_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        print(f"Email list exported: {export_path}")
        print(f"Rows exported: {len(rows)}")
        print(f"Rows skipped without email: {len(db_rows) - len(rows)}")
        print(f"Min lead score: {MIN_LEAD_SCORE}")
        print(f"Resend window hours: {EMAIL_RESEND_WINDOW_HOURS}")

    finally:
        conn.close()


# compatibility alias for run_palm_beach_pipeline.py or other imports
generate_email_list = main


if __name__ == "__main__":
    main()