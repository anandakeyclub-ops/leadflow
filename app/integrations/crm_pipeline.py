"""
crm_pipeline.py — TaxCase Review CRM Integration
==================================================
Calls 3 Supabase RPC functions built by Romy to create leads, cases,
and calendar appointments when a Stripe payment is confirmed.

Uses the PUBLIC anon key only — no service role key needed.
Functions: leadflow_upsert_lead, leadflow_create_case, leadflow_book_appointment

Calendar table: calevents
Date format:    YYYY-MM-DD
Time format:    HH:MM (24hr)
"""

import os
import logging
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# .strip()/.rstrip("/") guard against trailing newlines or whitespace that env
# dashboards (e.g. Render) commonly append when pasting — an unstripped key has
# a "\n" that requests rejects as an invalid header value.
SUPABASE_URL  = os.getenv("SUPABASE_URL", "https://mpxgxfqdbquzkrvvejkh.supabase.co").strip().rstrip("/")
SUPABASE_ANON = os.getenv("SUPABASE_ANON_KEY", "").strip()

RPC_URL = f"{SUPABASE_URL}/rest/v1/rpc"

HEADERS = {
    "apikey":       SUPABASE_ANON,
    "Content-Type": "application/json",
}

# issueType mapping from quiz result_type → CRM caseType
ISSUE_TYPE_MAP = {
    "oic":               "OIC",
    "installment":       "Installment Agreement",
    "cnc":               "Currently Not Collectible",
    "penalty_abatement": "Penalty Abatement",
    "lien_withdrawal":   "Lien Withdrawal",
    "tfrp":              "TFRP",
    "needs_review":      "OIC",   # fallback
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _rpc(function_name: str, params: dict) -> dict:
    """Call a Supabase RPC function. Returns {"ok": True/False, "data": ...}"""
    try:
        r = requests.post(
            f"{RPC_URL}/{function_name}",
            headers=HEADERS,
            json=params,
            timeout=15,
        )
        if r.status_code in (200, 201):
            return {"ok": True, "data": r.json()}
        log.error(f"RPC {function_name} failed {r.status_code}: {r.text[:200]}")
        return {"ok": False, "error": f"{r.status_code}: {r.text[:200]}"}
    except Exception as e:
        log.error(f"RPC {function_name} exception: {e}")
        return {"ok": False, "error": str(e)}


def _parse_calendly_datetime(event_start: str):
    """
    Parse Calendly event start time (ISO 8601) into date and time strings.
    Returns (date_str, time_str, end_time_str) e.g. ("2026-06-15", "10:00", "10:30")
    """
    try:
        # Calendly sends ISO 8601: "2026-06-15T14:00:00.000000Z"
        dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
        end = dt + timedelta(minutes=30)
        return (
            dt.strftime("%Y-%m-%d"),
            dt.strftime("%H:%M"),
            end.strftime("%H:%M"),
        )
    except Exception as e:
        log.warning(f"Could not parse event start time '{event_start}': {e}")
        return (
            datetime.now().strftime("%Y-%m-%d"),
            "09:00",
            "09:30",
        )


def _map_issue_type(raw: str) -> str:
    """Map quiz result_type to CRM issueType string."""
    return ISSUE_TYPE_MAP.get(raw.lower().strip(), raw)


# ── Step 1: Create or update lead ────────────────────────────────────────────
def create_crm_lead(booking_data: dict, payment_data: dict) -> str | None:
    """
    Upsert a lead in the CRM. Returns lead UUID string or None on failure.
    If email already exists, updates status to Tax Inv Fee Paid and adds Stripe IDs.
    """
    name   = booking_data.get("name", "").strip()
    email  = booking_data.get("email", "").strip()
    phone  = "".join(filter(str.isdigit, booking_data.get("phone", "")))
    state  = booking_data.get("state", "")
    county = booking_data.get("county", "")

    raw_issue = booking_data.get("issueType") or booking_data.get("result_type", "")
    issue_type  = _map_issue_type(raw_issue) if raw_issue else None
    irs_balance = str(booking_data.get("irsBalance", "")).replace("$", "").replace(",", "") or None

    notes = (
        f"Quiz result: {raw_issue}. "
        f"Booked via Calendly. "
        f"Paid ${payment_data.get('amount', 399)} via Stripe."
    )

    params = {
        "p_name":                     name,
        "p_email":                    email,
        "p_phone":                    phone or None,
        "p_irs_balance":              irs_balance,
        "p_issue_type":               issue_type,
        "p_state":                    state or None,
        "p_county":                   county or None,
        "p_notes":                    notes,
        "p_stripe_payment_id":        payment_data.get("stripe_payment_id"),
        "p_stripe_customer_id":       payment_data.get("stripe_customer_id"),
        "p_investigation_fee_amount": float(payment_data.get("amount", 399)),
        "p_assigned_to":              "Dana Richard",
    }

    result = _rpc("leadflow_upsert_lead", params)
    if result["ok"]:
        lead_id = result["data"]
        log.info(f"CRM lead upserted: {lead_id} — {name} ({email})")
        return str(lead_id)

    log.error(f"CRM lead creation failed: {result.get('error')}")
    return None


# ── Step 2: Create case ───────────────────────────────────────────────────────
def create_crm_case(lead_id: str, booking_data: dict) -> str | None:
    """
    Create a case linked to the lead by UUID. Returns case UUID or None on failure.
    """
    name = booking_data.get("name", "").strip()
    raw_issue   = booking_data.get("issueType") or booking_data.get("result_type", "OIC")
    case_type   = _map_issue_type(raw_issue)
    irs_balance = str(booking_data.get("irsBalance", "")).replace("$", "").replace(",", "") or None
    tax_years   = booking_data.get("taxYears") or booking_data.get("tax_years")

    params = {
        "p_lead_id":     lead_id,
        "p_client_name": name,
        "p_case_type":   case_type,
        "p_irs_balance": irs_balance,
        "p_tax_years":   str(tax_years) if tax_years else None,
        "p_assigned_to": "Dana Richard",
        "p_notes":       "Investigation fee paid. Case opened automatically via payment system.",
    }

    result = _rpc("leadflow_create_case", params)
    if result["ok"]:
        case_id = result["data"]
        log.info(f"CRM case created: {case_id} — {name}")
        return str(case_id)

    log.error(f"CRM case creation failed: {result.get('error')}")
    return None


# ── Step 3: Book appointment ──────────────────────────────────────────────────
def book_crm_appointment(booking_data: dict) -> str | None:
    """
    Book a calendar appointment in the CRM calevents table.
    Returns appointment UUID or None on failure.
    """
    name        = booking_data.get("name", "").strip()
    event_start = (
        booking_data.get("calendly_event_start_time")
        or booking_data.get("event_start_time")
        or booking_data.get("start_time")
        or ""
    )

    date_str, time_str, end_time_str = _parse_calendly_datetime(event_start)

    notes = (
        f"Booked online via TaxCase Review. "
        f"Stripe payment confirmed. "
        f"Phone: {booking_data.get('phone', 'not provided')}"
    )

    params = {
        "p_client_name": name,
        "p_date":        date_str,
        "p_time":        time_str,
        "p_end_time":    end_time_str,
        "p_event_type":  "Consultation Call",
        "p_assigned_to": "Dana Richard",
        "p_notes":       notes,
    }

    result = _rpc("leadflow_book_appointment", params)
    if result["ok"]:
        appt_id = result["data"]
        log.info(f"CRM appointment booked: {appt_id} — {name} on {date_str} at {time_str}")
        return str(appt_id)

    log.error(f"CRM appointment booking failed: {result.get('error')}")
    return None


# ── Master pipeline ───────────────────────────────────────────────────────────
def run_crm_pipeline(booking_data: dict, payment_data: dict) -> dict:
    """
    Run all 3 CRM steps in order. Each step fails gracefully.
    Never raises an exception — always returns a result dict.

    Returns:
    {
        "success": bool,
        "lead_id": str or None,
        "case_id": str or None,
        "appointment_id": str or None,
        "errors": [list of error strings]
    }
    """
    result = {
        "success":        False,
        "lead_id":        None,
        "case_id":        None,
        "appointment_id": None,
        "errors":         [],
    }

    # Pipeline run logging — same pattern as the email workers. pipeline_log
    # lives at the repo root, so import it lazily (matches send_email_sequence).
    # Writes to the daily JSONL log so CRM runs can be monitored alongside email
    # sends. Logging must never affect the pipeline result, so it's guarded.
    logger = None
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger("crm_pipeline")
        logger.start()
    except Exception as e:
        log.warning(f"PipelineLogger unavailable, continuing without it: {e}")

    try:
        if not SUPABASE_ANON:
            result["errors"].append("SUPABASE_ANON_KEY not set in .env")
            log.error("CRM pipeline aborted: SUPABASE_ANON_KEY missing")
            return result

        name  = booking_data.get("name", "unknown")
        email = booking_data.get("email", "unknown")
        log.info(f"CRM pipeline starting: {name} ({email})")

        # Step 1 — Lead
        try:
            lead_id = create_crm_lead(booking_data, payment_data)
            if lead_id:
                result["lead_id"] = lead_id
            else:
                result["errors"].append("Lead creation failed — see logs")
        except Exception as e:
            result["errors"].append(f"Lead exception: {e}")
            log.error(f"CRM lead step exception: {e}")

        # Step 2 — Case (only if lead succeeded)
        if result["lead_id"]:
            try:
                case_id = create_crm_case(result["lead_id"], booking_data)
                if case_id:
                    result["case_id"] = case_id
                else:
                    result["errors"].append("Case creation failed — see logs")
            except Exception as e:
                result["errors"].append(f"Case exception: {e}")
                log.error(f"CRM case step exception: {e}")

        # Step 3 — Appointment (always attempt, even if case failed)
        try:
            appt_id = book_crm_appointment(booking_data)
            if appt_id:
                result["appointment_id"] = appt_id
            else:
                result["errors"].append("Appointment booking failed — see logs")
        except Exception as e:
            result["errors"].append(f"Appointment exception: {e}")
            log.error(f"CRM appointment step exception: {e}")

        result["success"] = result["lead_id"] is not None
        log.info(
            f"CRM pipeline complete: lead={result['lead_id']} "
            f"case={result['case_id']} appt={result['appointment_id']} "
            f"errors={result['errors']}"
        )
        return result
    finally:
        # Fires on every return path (including the early abort above) so the
        # run is always recorded. Guarded so a logging failure can't break it.
        if logger:
            try:
                logger.finish({
                    "lead_id":            result["lead_id"],
                    "case_id":            result["case_id"],
                    "appointment_id":     result["appointment_id"],
                    "name":               booking_data.get("name"),
                    "email":              booking_data.get("email"),
                    "stripe_payment_id":  payment_data.get("stripe_payment_id"),
                    "amount":             payment_data.get("amount"),
                    "errors":             result["errors"],
                    "lead_created":       result["lead_id"] is not None,
                    "case_created":       result["case_id"] is not None,
                    "appointment_booked": result["appointment_id"] is not None,
                })
            except Exception as e:
                log.warning(f"PipelineLogger finish failed: {e}")
