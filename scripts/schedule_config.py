"""
schedule_config.py
===================
Canonical automation schedule — the single source of truth for *when* each
LeadFlow automation is supposed to run.

The keys here are also the PipelineLogger `run_type` values the workers log
under (see app/workers/daily_summary.py weekly calendar), so a schedule entry
and its log records always line up by key. If you add a new automation, add it
here AND make its worker log under the same key.

Day tokens are lowercase 3-letter weekday names ("mon".."sun"). An entry may
instead be a list of ints, which are treated as days-of-month (e.g. monthly
reports run on the 1st).
"""
from __future__ import annotations

from datetime import date


SCHEDULE = {
    "email_sends":        ["mon", "tue", "wed", "thu"],
    "social_post":        ["mon", "tue", "wed", "thu", "sat"],
    "reel_heygen":        ["thu", "sun"],
    "reel_remotion":      ["wed"],
    "blog_post":          ["mon", "tue", "wed", "thu", "fri", "sat"],
    "data_collection_fl": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "data_collection_tx": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "data_collection_ga": ["fri"],
    "data_collection_il": ["fri"],
    "data_collection_az": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "lead_scoring":       ["mon", "tue", "wed", "thu", "fri", "sat"],
    "collection_pages":   ["mon", "tue", "wed", "thu", "fri", "sat"],
    "email_enrichment":   ["mon", "tue", "wed", "thu", "fri", "sat"],
    "free_email_enrichment": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "daily_summary":      ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "weekly_intel":       ["sun"],
    "county_lien_intel":  ["sun"],
    "monthly_report":     [1],  # day of month
    "guest_post_outreach":["mon", "tue", "wed", "thu", "fri"],
    "weekly_scrape":       ["mon", "tue", "wed", "thu", "fri", "sat"],
    "multi_state_enrichment": ["mon", "wed", "fri"],
    "tx_contact_enrichment": ["tue", "thu"],
    "apollo_enrich_tx":    ["mon"],
}


def is_scheduled_on(task_key: str, d: date) -> bool:
    """Returns True if `task_key` is scheduled to run on the given date `d`.

    Handles both weekday-name entries (["mon", ...]) and day-of-month entries
    (lists of ints, e.g. [1] for the 1st of the month)."""
    entry = SCHEDULE.get(task_key, [])
    if not entry:
        return False
    if isinstance(entry[0], int):
        return d.day in entry
    return d.strftime("%a").lower() in entry


def is_scheduled_today(task_key: str) -> bool:
    """Returns True if the task is scheduled to run today."""
    from datetime import date
    entry = SCHEDULE.get(task_key, [])
    today = date.today()
    day_name = today.strftime("%a").lower()
    if isinstance(entry[0], int) if entry else False:
        return today.day in entry
    return day_name in entry


def get_todays_schedule() -> dict:
    """Returns dict of task_key -> bool for today."""
    return {k: is_scheduled_today(k) for k in SCHEDULE}

