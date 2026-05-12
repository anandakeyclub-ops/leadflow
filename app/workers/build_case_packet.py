import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.db import get_connection

try:
    from pypdf import PdfReader, PdfWriter
except Exception:
    PdfReader = None
    PdfWriter = None


BASE_DIR = Path(__file__).resolve().parents[2]
EXPORT_DIR = BASE_DIR / "data" / "exports" / "case_packets"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def normalize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned[:80] or "case"


def pretty_json_lines(data: Any) -> list[str]:
    if not data:
        return ["No questionnaire answers captured."]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return [data]

    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            key = safe_str(k).replace("_", " ").strip().title()
            if isinstance(v, (dict, list)):
                val = json.dumps(v, ensure_ascii=False)
            else:
                val = safe_str(v)
            lines.append(f"{key}: {val}")
        return lines or ["No questionnaire answers captured."]

    if isinstance(data, list):
        return [safe_str(x) for x in data] or ["No questionnaire answers captured."]

    return [safe_str(data)]


def fetch_case_data(submission_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if submission_id:
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
            else:
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
                    ORDER BY
                        COALESCE(ls.booked_at, ls.created_at) DESC,
                        ps.created_at DESC NULLS LAST
                    LIMIT 1
                    """
                )

            row = cur.fetchone()
            if not row:
                return None

            cols = [desc[0] for desc in cur.description]
            data = dict(zip(cols, row))

            cur.execute(
                """
                SELECT event_type, template_name, notes, created_at
                FROM outreach_events
                WHERE lead_id = %s
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (data.get("lead_id"),),
            )
            data["recent_events"] = [
                {
                    "event_type": r[0],
                    "template_name": r[1],
                    "notes": r[2],
                    "created_at": safe_str(r[3]),
                }
                for r in cur.fetchall()
            ]

            return data
    finally:
        conn.close()


def build_table_data(case: Dict[str, Any]) -> list[list[str]]:
    name = case.get("full_name") or case.get("first_name") or ""
    phone = case.get("primary_phone") or case.get("phone") or ""
    email = case.get("contact_email") or case.get("email") or ""
    address = " ".join(
        x for x in [
            safe_str(case.get("mailing_address_1")),
            safe_str(case.get("city")),
            safe_str(case.get("state")),
            safe_str(case.get("zip")),
        ] if x.strip()
    )

    return [
        ["Submission ID", safe_str(case.get("submission_id"))],
        ["Lead ID", safe_str(case.get("lead_id"))],
        ["Client Name", name],
        ["Email", email],
        ["Phone", phone],
        ["Address", address],
        ["Submitted", safe_str(case.get("submitted_at"))],
        ["Booked At", safe_str(case.get("booked_at"))],
        ["Lead Score", safe_str(case.get("lead_score"))],
        ["Lead Status", safe_str(case.get("lead_status"))],
        ["Payment Status", safe_str(case.get("stripe_payment_status") or case.get("payment_status"))],
        ["Payment Amount", safe_str(case.get("stripe_amount") or case.get("payment_amount"))],
        ["Attribution", safe_str(case.get("attribution_source"))],
        ["Click Count", safe_str(case.get("click_count"))],
        ["Last Clicked", safe_str(case.get("last_clicked_at"))],
        ["Template", safe_str(case.get("template_name"))],
        ["Booking URL", safe_str(case.get("booking_url"))],
        ["Source Record", safe_str(case.get("source_document_path"))],
    ]


def write_case_brief_pdf(case: Dict[str, Any], out_pdf: Path) -> None:
    doc = SimpleDocTemplate(
        str(out_pdf),
        pagesize=LETTER,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = "Helvetica-Bold"
    title_style.fontSize = 16
    title_style.leading = 20

    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=colors.HexColor("#1f3b5b"),
        spaceAfter=6,
        spaceBefore=6,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        spaceAfter=2,
    )

    small_style = ParagraphStyle(
        "Small",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#444444"),
    )

    story = []

    client_name = case.get("full_name") or case.get("first_name") or "Case Brief"
    story.append(Paragraph(f"Tax Case Brief - {client_name}", title_style))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", small_style))
    story.append(Spacer(1, 10))

    info_table = Table(build_table_data(case), colWidths=[1.5 * inch, 5.9 * inch])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f4f7")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#111111")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Questionnaire Summary", section_style))
    for line in pretty_json_lines(case.get("quiz_answers"))[:18]:
        story.append(Paragraph(line, body_style))

    if case.get("booking_notes"):
        story.append(Spacer(1, 6))
        story.append(Paragraph("Booking Notes", section_style))
        story.append(Paragraph(safe_str(case.get("booking_notes")), body_style))

    if case.get("recent_events"):
        story.append(Spacer(1, 6))
        story.append(Paragraph("Recent Activity", section_style))
        for evt in case["recent_events"][:5]:
            line = f"{evt.get('created_at', '')} - {evt.get('event_type', '')} - {evt.get('template_name', '')}"
            story.append(Paragraph(line, body_style))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Call Objective", section_style))
    call_objective = (
        "Review the filing/judgment context, confirm burden and urgency, verify contact details, "
        "clarify questionnaire answers, and recommend the appropriate next step."
    )
    story.append(Paragraph(call_objective, body_style))

    doc.build(story)


def merge_with_source_pdf(brief_pdf: Path, source_path: Optional[str], merged_pdf: Path) -> bool:
    if not source_path or not PdfReader or not PdfWriter:
        return False

    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return False

    if source.suffix.lower() != ".pdf":
        return False

    try:
        if source.stat().st_size == 0:
            return False
    except OSError:
        return False

    try:
        writer = PdfWriter()

        brief_reader = PdfReader(str(brief_pdf))
        for page in brief_reader.pages:
            writer.add_page(page)

        source_reader = PdfReader(str(source))
        for page in source_reader.pages:
            writer.add_page(page)

        with merged_pdf.open("wb") as f:
            writer.write(f)

        return True
    except Exception:
        return False


def write_json_snapshot(case: Dict[str, Any], out_json: Path) -> None:
    serializable = {}
    for k, v in case.items():
        if isinstance(v, (dict, list, str, int, float, bool)) or v is None:
            serializable[k] = v
        else:
            serializable[k] = safe_str(v)

    out_json.write_text(json.dumps(serializable, indent=2, default=safe_str), encoding="utf-8")


def main() -> None:
    submission_id = None
    if len(sys.argv) > 1:
        try:
            submission_id = int(sys.argv[1])
        except ValueError:
            print("Usage: python -m app.workers.build_case_packet [submission_id]")
            sys.exit(1)

    case = fetch_case_data(submission_id=submission_id)
    if not case:
        print("No case data found.")
        sys.exit(1)

    name_part = normalize_filename(case.get("full_name") or case.get("first_name") or "case")
    sub_part = safe_str(case.get("submission_id") or "unknown")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    brief_pdf = EXPORT_DIR / f"case_brief_submission_{sub_part}_{name_part}_{ts}.pdf"
    merged_pdf = EXPORT_DIR / f"case_packet_submission_{sub_part}_{name_part}_{ts}.pdf"
    snapshot_json = EXPORT_DIR / f"case_packet_submission_{sub_part}_{name_part}_{ts}.json"

    write_case_brief_pdf(case, brief_pdf)
    write_json_snapshot(case, snapshot_json)

    merged = merge_with_source_pdf(brief_pdf, case.get("source_document_path"), merged_pdf)

    print(f"Case brief PDF: {brief_pdf}")
    if merged:
        print(f"Case packet PDF (brief + source record): {merged_pdf}")
    else:
        print("No source PDF appended. Either no source document path exists, file is missing, empty, invalid, or it is not a PDF.")
    print(f"JSON snapshot: {snapshot_json}")


if __name__ == "__main__":
    main()