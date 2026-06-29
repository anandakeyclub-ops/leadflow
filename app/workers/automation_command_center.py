"""
automation_command_center.py
=========================================================
Builds the Automation Command Center sections (A–H) appended to the daily
summary email. Self-contained and defensive: every reader/query is wrapped so
this module can NEVER raise into build_html (existing sections must not break).

Data sources:
  - logs/task_audit/scheduled_tasks_latest.json   (Windows task export)
  - logs/pipeline/<date>.jsonl                     (PipelineLogger output)
  - scripts/automation_registry.py                 (expected task catalog)
  - safe DB queries (passed in)                    (data engine panel)

Entry point:
  build_automation_sections(ctx) -> html_str
where ctx is a dict providing helpers/colors/metrics from daily_summary so we
match its styling without a circular import.
"""

from __future__ import annotations

import json
from datetime import date, datetime


# ── tolerant readers ─────────────────────────────────────────────────────────

def _load_task_audit(base_dir) -> dict | None:
    f = base_dir / "logs" / "task_audit" / "scheduled_tasks_latest.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {t["TaskName"]: t for t in data.get("tasks", [])}
    except Exception:
        return None


def _load_registry():
    try:
        from scripts.automation_registry import TASKS, NAMING_ISSUES
        return TASKS, NAMING_ISSUES
    except Exception:
        try:
            import importlib.util, pathlib
            p = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "automation_registry.py"
            spec = importlib.util.spec_from_file_location("automation_registry", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.TASKS, mod.NAMING_ISSUES
        except Exception:
            return [], {}


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s).split(".")[0], fmt)
        except Exception:
            continue
    return None


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _expected_today(sched: str, today: date) -> bool:
    s = (sched or "").lower()
    if "continuous" in s or "hourly" in s or "every" in s or "daily" in s:
        return True
    if "monthly" in s:
        return today.day == 1
    if "weekly" in s or any(d in s for d in _WEEKDAYS):
        wd = _WEEKDAYS[today.weekday()]
        if any(d in s for d in _WEEKDAYS):
            return wd in s
        return False  # "weekly" with no named day — don't hard-flag a miss
    return False


def _ran_today(win, today: date) -> bool:
    dt = _parse_dt((win or {}).get("LastRunTime"))
    return bool(dt and dt.date() == today)


def _win_result_label(code):
    if code is None:
        return "—"
    table = {0: "Success", 267009: "Running", 267011: "Never run",
             267010: "Last ran (state)", 1: "Error 1", 2: "File not found (2)",
             10: "Error 10", 267014: "Terminated"}
    return table.get(code, f"Code {code}")


def _pipeline_match(runs, run_type):
    if not run_type:
        return None
    for r in runs:
        if r.get("run_type") == run_type:
            return r
    return None


def _status_for(task, win, runs, today):
    """Return (emoji, label, diagnosis)."""
    state = (win or {}).get("State", "")
    code = (win or {}).get("LastTaskResult")
    run = _pipeline_match(runs, task.get("expected_pipeline_run_type"))
    expected = _expected_today(task.get("expected_schedule", ""), today)
    ran = _ran_today(win, today)

    if win is None:
        return "⚪", "Unknown", "Not in Windows export (run export_scheduled_tasks.ps1 / task may be unscheduled)"
    if str(state).lower() == "disabled":
        return "⚫", "Disabled", "Task is disabled in Windows Task Scheduler"
    if code == 267009 or str(state).lower() == "running":
        return "🟡", "Running", "Currently executing"
    if code not in (0, None) and code != 267011:
        return "🔴", "Failed", f"Windows result {_win_result_label(code)} — {task['manual_run_command']}"
    if expected and not ran:
        return "❌", "Missed", f"Expected today ({task['expected_schedule']}) but no run recorded"
    if ran and code == 0:
        if task.get("expected_pipeline_run_type") and run is None:
            return "🟠", "No output", "Windows succeeded but no matching pipeline log entry today"
        if run is not None and run.get("status") != "ok":
            return "🟠", "No output", "Pipeline run logged an error"
        return "🟢", "OK", "Ran and succeeded"
    if code == 0 and not expected:
        return "🟢", "OK", "Last run succeeded"
    return "🟠", "No output", "No run recorded today"


def _today_minutes(sched: str):
    """Best-effort HH:MM extraction for the timeline ordering/label."""
    import re
    m = re.search(r"(\d{1,2}):(\d{2})", sched or "")
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if "continuous" in (sched or "").lower():
        return "00:00"
    return "—"


# ── main builder ─────────────────────────────────────────────────────────────

def build_automation_sections(ctx) -> str:
    """Build sections A–H. Returns HTML. Never raises."""
    try:
        return _build(ctx)
    except Exception as e:  # absolute backstop
        sec2 = ctx.get("sec2")
        if sec2:
            return sec2("⚙️", "Automation Command Center",
                        f"<p style='color:#b91c1c'>Command Center unavailable: {e}</p>")
        return ""


def _build(ctx) -> str:
    sec2 = ctx["sec2"]; h = ctx["h"]; tbl = ctx["tbl"]
    C = ctx["colors"]
    base_dir = ctx["base_dir"]
    runs = ctx.get("pipeline_runs") or []
    safe_query = ctx.get("safe_query")
    today = date.today()
    today_str = ctx.get("today_str", today.isoformat())

    registry, naming_issues = _load_registry()
    audit = _load_task_audit(base_dir)

    out = []

    # ── Section A — Command Center header / health score ──────────────────────
    if audit is None:
        banner = (f"<div style='background:{C['BG_AMBER']};border:1px solid {C['C_AMBER']};"
                  f"border-radius:8px;padding:12px 16px;color:{C['C_AMBER']};font-size:13px;font-weight:700'>"
                  f"⚠️ Windows Task export missing — run "
                  f"<code>scripts/maintenance/export_scheduled_tasks.ps1</code> to populate live task status.</div>")
    else:
        banner = ""

    # compute statuses
    rows = []
    p0p1_fail = []
    counts = {"expected": 0, "succeeded": 0, "failed": 0, "missed": 0, "running": 0}
    for t in registry:
        win = (audit or {}).get(t["task_name"])
        emoji, label, diag = _status_for(t, win, runs, today)
        ran = _ran_today(win, today)
        expected = _expected_today(t.get("expected_schedule", ""), today)
        if expected:
            counts["expected"] += 1
        if label == "OK" and ran:
            counts["succeeded"] += 1
        elif label == "Failed":
            counts["failed"] += 1
        elif label == "Missed":
            counts["missed"] += 1
        elif label == "Running":
            counts["running"] += 1
        if t["priority"] in ("P0", "P1") and label in ("Failed", "Missed", "No output"):
            p0p1_fail.append((t, label, diag))
        rows.append((t, win, emoji, label, diag, ran, expected))

    # health score = % of P0+P1 tasks that are OK
    p0p1 = [r for r in rows if r[0]["priority"] in ("P0", "P1")]
    p0p1_ok = sum(1 for r in p0p1 if r[3] == "OK")
    health = round(100 * p0p1_ok / len(p0p1)) if p0p1 else 0
    hcolor = C["C_GREEN"] if health >= 90 else (C["C_AMBER"] if health >= 70 else C["C_RED"])

    header_body = (
        banner +
        f"<table style='width:100%;border-collapse:collapse;margin-top:12px'><tr>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:30px;font-weight:900;color:{hcolor}'>{health}%</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>P0/P1 Health</div></td>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:24px;font-weight:800'>{counts['expected']}</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>Expected today</div></td>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:24px;font-weight:800;color:{C['C_GREEN']}'>{counts['succeeded']}</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>Succeeded</div></td>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:24px;font-weight:800;color:{C['C_RED']}'>{counts['failed']}</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>Failed</div></td>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:24px;font-weight:800;color:{C['C_AMBER']}'>{counts['missed']}</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>Missed</div></td>"
        f"<td style='text-align:center;padding:10px'><div style='font-size:24px;font-weight:800;color:{C['C_BLUE']}'>{counts['running']}</div>"
        f"<div style='font-size:11px;color:{C['C_SLATE']}'>Running</div></td>"
        f"</tr></table>"
    )
    if p0p1_fail:
        items = "".join(f"<li style='color:{C['C_RED']};font-size:13px;margin-bottom:4px'>"
                        f"<b>{h(t['task_name'])}</b> [{t['priority']}] — {label}: {h(diag)}</li>"
                        for t, label, diag in p0p1_fail)
        header_body += f"<div style='margin-top:12px'><b style='color:{C['C_RED']}'>P0/P1 Failures</b><ul style='margin:6px 0'>{items}</ul></div>"
    else:
        header_body += f"<p style='color:{C['C_GREEN']};font-size:13px;margin-top:10px'>✅ No P0/P1 failures detected.</p>"
    out.append(sec2("⚙️", "Automation Command Center", header_body,
                    "Health = % of P0/P1 (critical) tasks that succeeded today."))

    # ── Section B — Today at a Glance timeline ────────────────────────────────
    timeline = sorted(
        [(t, emoji, label, ran, expected) for (t, win, emoji, label, diag, ran, expected) in rows
         if expected or ran or label in ("Failed", "Running")],
        key=lambda x: _today_minutes(x[0].get("expected_schedule", ""))
    )
    if timeline:
        tl_rows = [[_today_minutes(t.get("expected_schedule", "")),
                    h(t["task_name"].replace("LeadFlow", "").lstrip(" -")),
                    t["priority"], f"{emoji} {label}"]
                   for (t, emoji, label, ran, expected) in timeline]
        body_b = tbl(["Time", "Task", "Pri", "Status"], tl_rows, center_cols={2, 3})
    else:
        body_b = f"<p style='color:{C['C_SLATE']};font-size:13px'>No tasks expected today.</p>"
    out.append(sec2("🕒", "Today at a Glance", body_b,
                    "Tasks expected to run today, ordered by scheduled time."))

    # ── Section C — Full task status table ────────────────────────────────────
    full_rows = []
    for (t, win, emoji, label, diag, ran, expected) in sorted(rows, key=lambda r: (r[0]["priority"], r[0]["task_name"])):
        win = win or {}
        last_run = _parse_dt(win.get("LastRunTime"))
        last_run_s = last_run.strftime("%m/%d %H:%M") if last_run else "—"
        run = _pipeline_match(runs, t.get("expected_pipeline_run_type"))
        pmatch = ("✅ " + run.get("run_type", "")) if run else ("—" if not t.get("expected_pipeline_run_type") else "❌ none")
        output = ""
        if run:
            steps = run.get("steps", [])
            output = (steps[-1].get("detail", "") if steps else "")[:40]
        full_rows.append([
            _today_minutes(t.get("expected_schedule", "")),
            h(t["task_name"].replace("LeadFlow", "").lstrip(" -")),
            t["priority"],
            "yes" if expected else "—",
            last_run_s,
            h(_win_result_label(win.get("LastTaskResult"))),
            h(pmatch),
            h(output or "—"),
            emoji,
            h(diag[:60]),
        ])
    body_c = tbl(["Time", "Task", "Pri", "Exp", "Win LastRun", "Win Result",
                  "Pipeline", "Output", "St", "Diagnosis"], full_rows, center_cols={2, 3, 8})
    out.append(sec2("📋", "Full Task Status", body_c,
                    f"All {len(rows)} registered tasks · 🟢 OK 🔴 Failed 🟠 No output 🟡 Running ⚫ Disabled ❌ Missed ⚪ Unknown"))

    # ── Section D — P0/P1 critical panel ──────────────────────────────────────
    crit_keys = ["api_server", "ngrok_tunnel", "ngrok_watchdog", "daily_summary", "daily_email",
                 "data_engine", "email_enrichment", "bridge_to_email_pool",
                 "sms_morning", "sms_midday", "sms_afternoon", "abandoned_booking_retargeting"]
    crit_rows = []
    for (t, win, emoji, label, diag, ran, expected) in rows:
        if t["task_key"] not in crit_keys:
            continue
        win = win or {}
        fix = t["manual_run_command"] if label in ("Failed", "Missed", "No output") else "—"
        crit_rows.append([
            h(t["task_name"].replace("LeadFlow - ", "")),
            f"{emoji} {label}",
            h(_win_result_label(win.get("LastTaskResult"))),
            (_parse_dt(win.get("LastRunTime")).strftime("%m/%d %H:%M") if _parse_dt(win.get("LastRunTime")) else "—"),
            h(fix[:70]),
        ])
    body_d = tbl(["Critical Task", "Status", "Win Result", "Last Run", "Manual Fix (if down)"],
                 crit_rows, center_cols={1})
    out.append(sec2("🚨", "P0/P1 Critical Infrastructure", body_d,
                    "The pipeline cannot earn revenue if any of these are down. Run the manual fix command immediately."))

    # ── Section E — Content engine panel ──────────────────────────────────────
    def _find_run(rt):
        return _pipeline_match(runs, rt)

    blog = _find_run("blog_post")
    social = _find_run("social_post")
    reel = _find_run("reel_heygen") or _find_run("reel_remotion")

    def _yn(x):
        return "✅ YES" if x else "❌ NO"

    def _detail(run):
        if not run:
            return "—"
        steps = run.get("steps", [])
        return (steps[-1].get("detail", "") if steps else "")[:60] or "ran"

    content_rows = [
        ["Blog Draft", _yn(blog), _detail(blog),
         "published" if blog and blog.get("status") == "ok" else "not today"],
        ["Social Media", _yn(social), _detail(social),
         "posted" if social and social.get("status") == "ok" else "not today"],
        ["Reel (today)", _yn(reel), _detail(reel),
         (reel.get("run_type") if reel else "—")],
    ]
    body_e = tbl(["Engine", "Ran Today", "Detail", "Output"], content_rows, center_cols={1})
    out.append(sec2("🎬", "Content Engine", body_e,
                    "Blog / social / reel runs detected from today's pipeline log."))

    # ── Section F — Data engine panel (defensive DB) ──────────────────────────
    data_body = _build_data_panel(safe_query, C, tbl, h)
    out.append(sec2("🗄️", "Data Engine", data_body,
                    "New liens, raw contacts, and matched leads created today."))

    # ── Section G — Infrastructure panel ──────────────────────────────────────
    infra_body = _build_infra_panel(base_dir, audit, C, h)
    out.append(sec2("🛰️", "Infrastructure", infra_body,
                    "API server, ngrok tunnel, and DB pool health."))

    # ── Section H — AI export snapshot ────────────────────────────────────────
    out.append(_build_ai_snapshot(ctx, counts, health, p0p1_fail, blog, social, reel,
                                  registry, audit, naming_issues, today_str, sec2, h, C))

    return "\n".join(out)


def _build_data_panel(safe_query, C, tbl, h):
    if not safe_query:
        return f"<p style='color:{C['C_SLATE']};font-size:13px'>DB unavailable.</p>"

    def q(cur, sql):
        cur.execute(sql)
        return cur.fetchall()

    rows = []

    def try_metric(label, sql, fmt=lambda r: f"{(r[0][0] if r and r[0] and r[0][0] is not None else 0):,}"):
        try:
            res = safe_query(lambda cur: q(cur, sql), None)
            rows.append([label, fmt(res) if res is not None else "n/a"])
        except Exception:
            rows.append([label, "n/a"])

    try_metric("New liens today", "SELECT COUNT(*) FROM normalized_liens WHERE created_at::date = CURRENT_DATE")
    # state breakdown (best-effort)
    try:
        res = safe_query(lambda cur: q(cur,
            "SELECT state, COUNT(*) FROM normalized_liens "
            "WHERE created_at::date = CURRENT_DATE AND state IS NOT NULL "
            "GROUP BY state ORDER BY 2 DESC LIMIT 12"), None)
        if res:
            brk = " · ".join(f"{s}:{c}" for s, c in res)
            rows.append(["  by state", h(brk)])
    except Exception:
        pass
    try_metric("New matched contacts today",
               "SELECT COUNT(*) FROM lien_dbpr_contacts WHERE created_at::date = CURRENT_DATE")
    try_metric("New email-ready today",
               "SELECT COUNT(*) FROM lien_dbpr_contacts WHERE created_at::date = CURRENT_DATE AND email IS NOT NULL")
    # raw contact sources (real table names)
    for label, table in [("Google Places contacts today", "google_places_contacts"),
                         ("Arizona ROC contacts today", "arizona_roc_contacts")]:
        try:
            res = safe_query(lambda cur, tt=table: q(cur,
                f"SELECT COUNT(*) FROM {tt} WHERE created_at::date = CURRENT_DATE"), None)
            if res is not None:
                rows.append([label, f"{(res[0][0] if res and res[0] else 0):,}"])
        except Exception:
            pass

    if not rows:
        return f"<p style='color:{C['C_SLATE']};font-size:13px'>No data-engine metrics available.</p>"
    return tbl(["Metric", "Today"], rows)


def _build_infra_panel(base_dir, audit, C, h):
    rows = []
    # API server health
    api = "❓ unknown"
    try:
        import requests
        r = requests.get("http://localhost:8000/health", timeout=2)
        api = f"🟢 responding ({r.status_code})" if r.status_code < 500 else f"🟠 {r.status_code}"
    except Exception:
        try:
            import requests
            r = requests.get("http://localhost:8000/", timeout=2)
            api = f"🟢 up ({r.status_code})"
        except Exception:
            api = "🔴 not responding on :8000"
    rows.append(["API Server (:8000)", h(api)])

    # ngrok — from task audit last run
    ng = "❓ unknown"
    if audit:
        nt = audit.get("LeadFlow - ngrok Tunnel") or audit.get("LeadFlow - ngrok Watchdog")
        if nt:
            lr = _parse_dt(nt.get("LastRunTime"))
            ng = f"state={nt.get('State','?')}, last run {lr.strftime('%m/%d %H:%M') if lr else '—'}"
    rows.append(["ngrok Tunnel", h(ng)])

    # DB pool
    db = "❓ unknown"
    try:
        from app.core.db import get_connection, release_connection
        c = get_connection()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            db = "🟢 pool healthy"
        finally:
            try:
                release_connection(c)
            except Exception:
                pass
    except Exception as e:
        db = f"🔴 {str(e)[:50]}"
    rows.append(["DB connection pool", h(db)])

    body = "".join(f"<tr><td style='padding:6px 10px;font-weight:600'>{lbl}</td>"
                   f"<td style='padding:6px 10px'>{val}</td></tr>" for lbl, val in rows)
    return f"<table style='width:100%;border-collapse:collapse;font-size:13px'>{body}</table>"


def _build_ai_snapshot(ctx, counts, health, p0p1_fail, blog, social, reel,
                       registry, audit, naming_issues, today_str, sec2, h, C):
    seq = ctx.get("seq", {}) or {}
    sms = ctx.get("sms", {}) or {}
    lead = ctx.get("lead", {}) or {}

    fails = ", ".join(f"{t['task_name']} ({label})" for t, label, diag in p0p1_fail) or "NONE"

    # auto bottleneck + recommendation
    if p0p1_fail:
        bottleneck = f"{p0p1_fail[0][0]['task_name']} — {p0p1_fail[0][1]}"
        rec = f"Run: {p0p1_fail[0][0]['manual_run_command']}"
    elif counts["missed"] > 0:
        bottleneck = f"{counts['missed']} expected task(s) missed today"
        rec = "Check Windows Task Scheduler History; re-run missed P2/P3 jobs."
    elif audit is None:
        bottleneck = "No Windows task export"
        rec = "Run scripts/maintenance/export_scheduled_tasks.ps1 before the daily summary."
    else:
        bottleneck = "None — automation healthy"
        rec = "Focus on conversion: warm up email sends and follow up replies."

    lines = [
        "=== AUTOMATION SNAPSHOT ===",
        f"Date: {today_str}",
        f"Tasks expected: {counts['expected']} | Succeeded: {counts['succeeded']} | "
        f"Failed: {counts['failed']} | Missed: {counts['missed']} | Running: {counts['running']}",
        f"P0/P1 health: {health}%",
        f"P0/P1 failures: {fails}",
        f"Email sends today: {seq.get('sent_24h',0)} | Open rate: {seq.get('open_rate',0)}% | Click rate: {seq.get('click_rate',0)}%",
        f"SMS sends today: {sms.get('sent_today', sms.get('sent_total',0))} | Delivery: {sms.get('delivery_rate',0)}%",
        f"Blog published today: {'YES' if blog and blog.get('status')=='ok' else 'NO'}",
        f"Social posted today: {'YES' if social and social.get('status')=='ok' else 'NO'}",
        f"Reel rendered today: {'YES — ' + reel.get('run_type','') if reel else 'NO'}",
        f"New liens today: {lead.get('liens_24h', 'n/a')}",
        f"Email-ready leads (total): {lead.get('email_ready', 'n/a')}",
        f"Registered tasks: {len(registry)} | Windows tasks exported: {len(audit) if audit else 0}",
        f"Naming issues: {', '.join(naming_issues.keys()) if naming_issues else 'none'}",
        f"Top bottleneck: {bottleneck}",
        f"Recommended action: {rec}",
        "===========================",
    ]
    text = "\n".join(str(x) for x in lines)
    body = (f"<div style='background:#0f1b2d;border-radius:8px;padding:16px;overflow-x:auto'>"
            f"<pre style='color:#e2e8f0;font-size:11px;font-family:monospace;margin:0;"
            f"white-space:pre-wrap;line-height:1.6'>{h(text)}</pre></div>")
    return sec2("🤖", "Automation Snapshot — Copy to Claude", body,
                "Plain-text automation status. Paste into Claude with: \"What's my top automation risk today?\"")
