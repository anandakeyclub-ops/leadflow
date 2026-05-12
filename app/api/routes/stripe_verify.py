import os
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USERNAME)

TAX_PRO_EMAIL = os.getenv("TAX_PRO_EMAIL", "").strip()
TAX_PRO_NAME = os.getenv("TAX_PRO_NAME", "Tax Professional").strip()

CASE_PACKET_DIR = BASE_DIR / "data" / "exports" / "case_packets"


def safe_str(value) -> str:
    return "" if value is None else str(value)


def fetch_submission_summary(submission_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ls.id AS submission_id,
                    ls.first_name,
                    ls.email,
                    ls.phone,
                    ls.quiz_answers,
                    ls.click_token,
                    ls.lead_id,
                    ls.outreach_event_id,
                    ls.attribution_source,
                    ls.payment_status,
                    ls.payment_amount,
                    ls.created_at AS submitted_at,
                    ls.booked_at,
                    ls.booking_url,
                    ls.booking_notes,

                    ml.lead_score,
                    ml.lead_status,
                    ml.source_document_path,

                    c.full_name,
                    c.email AS contact_email,
                    c.primary_phone,
                    c.secondary_phone,
                    c.mailing_address_1,
                    c.city,
                    c.state,
                    c.zip,

                    ps.session_id,
                    ps.payment_status AS stripe_payment_status,
                    ps.amount AS stripe_amount,
                    ps.created_at AS payment_created_at,

                    ect.template_name,
                    ect.recipient_email,
                    ect.click_count,
                    ect.first_clicked_at,
                    ect.last_clicked_at

                FROM landing_submissions ls
                LEFT JOIN matched_leads ml
                    ON ls.lead_id = ml.id
                LEFT JOIN contacts c
                    ON ls.lead_id = c.lead_id
                LEFT JOIN payment_sessions ps
                    ON ps.submission_id = ls.id
                LEFT JOIN email_click_tracking ect
                    ON ect.tracking_token = ls.click_token
                WHERE ls.id = %s
                ORDER BY ps.created_at DESC NULLS LAST
                LIMIT 1
                """,
                (submission_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
    finally:
        conn.close()


def find_latest_case_files(submission_id: int) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
    if not CASE_PACKET_DIR.exists():
        return None, None, None

    brief_matches = sorted(
        CASE_PACKET_DIR.glob(f"case_brief_submission_{submission_id}_*.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    packet_matches = sorted(
        CASE_PACKET_DIR.glob(f"case_packet_submission_{submission_id}_*.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    json_matches = sorted(
        CASE_PACKET_DIR.glob(f"case_packet_submission_{submission_id}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    brief_pdf = brief_matches[0] if brief_matches else None
    packet_pdf = packet_matches[0] if packet_matches else None
    packet_json = json_matches[0] if json_matches else None

    return brief_pdf, packet_pdf, packet_json


def build_internal_email_body(summary: dict) -> str:
    client_name = summary.get("full_name") or summary.get("first_name") or "Unknown"
    client_email = summary.get("contact_email") or summary.get("email") or ""
    client_phone = summary.get("primary_phone") or summary.get("phone") or ""
    address = " ".join(
        x for x in [
            safe_str(summary.get("mailing_address_1")),
            safe_str(summary.get("city")),
            safe_str(summary.get("state")),
            safe_str(summary.get("zip")),
        ] if x.strip()
    )

    lines = [
        f"Hi {TAX_PRO_NAME},",
        "",
        "A paid tax case review has been completed and the case packet is attached.",
        "",
        "Case Summary",
        "------------",
        f"Submission ID: {safe_str(summary.get('submission_id'))}",
        f"Lead ID: {safe_str(summary.get('lead_id'))}",
        f"Client Name: {client_name}",
        f"Email: {client_email}",
        f"Phone: {client_phone}",
        f"Address: {address}",
        f"Submitted At: {safe_str(summary.get('submitted_at'))}",
        f"Booked At: {safe_str(summary.get('booked_at'))}",
        f"Lead Score: {safe_str(summary.get('lead_score'))}",
        f"Lead Status: {safe_str(summary.get('lead_status'))}",
        f"Payment Status: {safe_str(summary.get('stripe_payment_status') or summary.get('payment_status'))}",
        f"Payment Amount: {safe_str(summary.get('stripe_amount') or summary.get('payment_amount'))}",
        f"Attribution Source: {safe_str(summary.get('attribution_source'))}",
        f"Email Template: {safe_str(summary.get('template_name'))}",
        f"Click Count: {safe_str(summary.get('click_count'))}",
        f"Source Record Path: {safe_str(summary.get('source_document_path'))}",
        "",
        "Questionnaire Answers",
        "---------------------",
        safe_str(summary.get("quiz_answers")),
        "",
        "Booking Notes",
        "-------------",
        safe_str(summary.get("booking_notes")) or "None",
        "",
        "Please review the attached packet before the call.",
        "",
        "Dana",
    ]
    return "\n".join(lines)


def attach_file(msg: EmailMessage, path: Optional[Path]) -> None:
    if not path or not path.exists() or not path.is_file():
        return

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        maintype, subtype = "application", "pdf"
    elif suffix == ".json":
        maintype, subtype = "application", "json"
    else:
        maintype, subtype = "application", "octet-stream"

    with path.open("rb") as f:
        msg.add_attachment(
            f.read(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )


def send_case_packet_email(summary: dict, brief_pdf: Optional[Path], packet_pdf: Optional[Path], packet_json: Optional[Path]) -> None:
    if not TAX_PRO_EMAIL:
        raise ValueError("TAX_PRO_EMAIL is missing in .env")

    client_name = summary.get("full_name") or summary.get("first_name") or "Client"
    submission_id = safe_str(summary.get("submission_id"))

    subject = f"New Paid Tax Case Review - {client_name} - Submission {submission_id}"
    body = build_internal_email_body(summary)

    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = TAX_PRO_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)

    if packet_pdf and packet_pdf.exists():
        attach_file(msg, packet_pdf)
    else:
        attach_file(msg, brief_pdf)

    attach_file(msg, packet_json)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)


def generate_case_packet(submission_id: int) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
    subprocess.run(
        ["python", "-m", "app.workers.build_case_packet", str(submission_id)],
        cwd=str(BASE_DIR),
        check=False,
    )
    return find_latest_case_files(submission_id)


@router.get("/verify-session")
def verify_session(session_id: str):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")

    session = stripe.checkout.Session.retrieve(session_id)
    payment_status = session.payment_status

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE payment_sessions
                    SET payment_status = %s, updated_at = NOW()
                    WHERE session_id = %s
                    RETURNING submission_id, click_token, lead_id
                    """,
                    (payment_status, session_id),
                )
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Session not found")

                submission_id, click_token, lead_id = row

                if payment_status == "paid":
                    cur.execute(
                        """
                        UPDATE landing_submissions
                        SET payment_status = 'paid',
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (submission_id,),
                    )

        brief_pdf = None
        packet_pdf = None
        packet_json = None
        emailed_tax_pro = False
        email_error = None

        if payment_status == "paid":
            brief_pdf, packet_pdf, packet_json = generate_case_packet(submission_id)
            summary = fetch_submission_summary(submission_id)

            if summary:
                try:
                    send_case_packet_email(summary, brief_pdf, packet_pdf, packet_json)
                    emailed_tax_pro = True
                except Exception as e:
                    email_error = str(e)

        return {
            "status": "success",
            "payment_status": payment_status,
            "submission_id": submission_id,
            "click_token": click_token,
            "lead_id": lead_id,
            "case_brief_pdf": str(brief_pdf) if brief_pdf else None,
            "case_packet_pdf": str(packet_pdf) if packet_pdf else None,
            "case_packet_json": str(packet_json) if packet_json else None,
            "emailed_tax_pro": emailed_tax_pro,
            "email_error": email_error,
        }

    finally:
        conn.close()