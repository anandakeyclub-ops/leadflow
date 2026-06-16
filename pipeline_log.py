"""
pipeline_log.py
===============
Unified pipeline logger for LeadFlow.
Used by weekly_scrape.py, send_email_sequence.py, and social_media_poster.py.

Every script appends to the same daily log file:
  logs/pipeline/YYYY-MM-DD.jsonl   — one JSON line per run
  logs/pipeline/latest.json        — always the most recent run

View logs:
  python pipeline_log.py --today
  python pipeline_log.py --history 7
  python pipeline_log.py --latest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# Log output contains status emoji (✅ ⚠️ ❌ ⏭). On a non-UTF-8 console
# (e.g. Windows cp1252) printing those raises UnicodeEncodeError and aborts
# finish(). Force UTF-8 with replacement so logging can never crash a run.
# No-op on already-UTF-8 streams (Render/Linux) and if the stream lacks
# reconfigure (it's wrapped by the server).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

BASE_DIR  = Path(os.getenv("BASE_DIR", Path(__file__).resolve().parent))
LOGS_DIR  = BASE_DIR / "logs" / "pipeline"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LATEST    = LOGS_DIR / "latest.json"


class PipelineLogger:
    """
    Drop-in logger for any LeadFlow script.

    Usage:
        logger = PipelineLogger("email_sequence")
        logger.start()
        logger.step_start("step1")
        logger.step_done("step1", ok=True, detail="60 sent")
        logger.finish({"total_sent": 60, "failed": 0})
    """

    def __init__(self, run_type: str):
        self.run_type  = run_type
        self.run_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.started   = None
        self.finished  = None
        self.steps     = []
        self._current  = {}
        self.errors    = []
        self._log_file = LOGS_DIR / f"{date.today().isoformat()}.jsonl"
        self._txt_file = LOGS_DIR / f"{date.today().isoformat()}.log"

    def start(self):
        self.started = datetime.now().isoformat()
        self._txt(f"\n{'='*60}")
        self._txt(f"  {self.run_type.upper()} — {self.started}")
        self._txt(f"{'='*60}")
        print(f"\n[PipelineLog] {self.run_type} started — {self.run_id}")

    def step_start(self, name: str):
        self._current[name] = {
            "name":    name,
            "started": datetime.now().isoformat(),
            "status":  "running",
            "detail":  "",
            "error":   "",
            "seconds": 0,
        }

    def step_done(self, name: str, ok: bool,
                  detail: str = "", error: str = ""):
        entry   = self._current.pop(name, {"name": name,
                                            "started": datetime.now().isoformat()})
        elapsed = (datetime.now() -
                   datetime.fromisoformat(entry["started"])).total_seconds()
        entry.update({
            "status":  "ok" if ok else "error",
            "detail":  detail[:300],
            "error":   error[:300],
            "seconds": round(elapsed, 1),
        })
        self.steps.append(entry)
        icon = "✅" if ok else "❌"
        self._txt(f"  {icon} {name} ({elapsed:.0f}s)"
                  f"{' — ' + detail[:80] if detail else ''}")
        if not ok and error:
            self._txt(f"     ERROR: {error[:120]}")
            self.errors.append({"step": name, "error": error[:300]})
        return entry

    def step_skip(self, name: str, reason: str = ""):
        self.steps.append({
            "name": name, "status": "skipped",
            "detail": reason, "error": "", "seconds": 0,
            "started": datetime.now().isoformat(),
        })
        self._txt(f"  ⏭  {name} — skipped"
                  f"{' (' + reason + ')' if reason else ''}")

    def finish(self, metrics: dict = None, status: str = None) -> dict:
        self.finished = datetime.now().isoformat()
        elapsed = (datetime.fromisoformat(self.finished) -
                   datetime.fromisoformat(self.started)).total_seconds()
        ok_ct   = sum(1 for s in self.steps if s["status"] == "ok")
        fail_ct = sum(1 for s in self.steps if s["status"] == "error")
        skip_ct = sum(1 for s in self.steps if s["status"] == "skipped")

        # `status` lets callers record an outcome that isn't a hard pass/fail
        # (e.g. "quality_rejected") so downstream reports can show it distinctly
        # instead of treating a clean early-return as a silent missing run.
        record = {
            "run_id":   self.run_id,
            "run_type": self.run_type,
            "date":     date.today().isoformat(),
            "started":  self.started,
            "finished": self.finished,
            "duration": round(elapsed, 1),
            "status":   status or ("ok" if fail_ct == 0 else "error"),
            "ok":       ok_ct,
            "failed":   fail_ct,
            "skipped":  skip_ct,
            "steps":    self.steps,
            "errors":   self.errors,
            "metrics":  metrics or {},
        }

        # Append to daily JSONL
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # Overwrite latest
        LATEST.write_text(json.dumps(record, indent=2))

        summary = (f"{'✅ OK' if fail_ct == 0 else f'⚠️ {fail_ct} FAILED'} "
                   f"| {ok_ct} ok  {fail_ct} failed  {skip_ct} skipped "
                   f"| {elapsed/60:.1f} min")
        self._txt(f"\n  {summary}")
        self._txt(f"{'='*60}\n")
        print(f"[PipelineLog] Done — {summary}")
        print(f"  Log: {self._log_file}")
        return record

    def _txt(self, line: str):
        with open(self._txt_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ── CLI viewer ────────────────────────────────────────────────────────────────

def print_today():
    log_file = LOGS_DIR / f"{date.today().isoformat()}.jsonl"
    txt_file = LOGS_DIR / f"{date.today().isoformat()}.log"
    if txt_file.exists():
        print(txt_file.read_text())
    elif log_file.exists():
        for line in log_file.read_text().strip().splitlines():
            _print_record(json.loads(line))
    else:
        print(f"No log for today ({date.today().isoformat()})")
        print(f"Expected: {log_file}")


def print_history(days: int = 7):
    print(f"\n{'─'*72}")
    print(f"  LeadFlow Pipeline — Last {days} Days")
    print(f"{'─'*72}")
    print(f"  {'Date':<12} {'Type':<20} {'Status':<8} {'OK':>4} "
          f"{'Fail':>5} {'Mins':>6}  Summary")
    print(f"  {'─'*12} {'─'*20} {'─'*8} {'─'*4} {'─'*5} {'─'*6}  {'─'*20}")

    for i in range(days - 1, -1, -1):
        d        = date.today() - timedelta(days=i)
        log_file = LOGS_DIR / f"{d.isoformat()}.jsonl"
        if not log_file.exists():
            print(f"  {d.isoformat():<12} {'—':<20} no log")
            continue
        lines = log_file.read_text().strip().splitlines()
        if not lines:
            continue
        for line in lines:
            rec    = json.loads(line)
            status = "✅ OK" if rec["status"] == "ok" else "❌ FAIL"
            mins   = rec["duration"] / 60
            m      = rec.get("metrics", {})
            # Build summary from metrics
            parts  = []
            if "total_sent"   in m: parts.append(f"{m['total_sent']} emails")
            if "step1_sent"   in m: parts.append(f"E1:{m['step1_sent']}")
            if "step2_sent"   in m: parts.append(f"E2:{m['step2_sent']}")
            if "step3_sent"   in m: parts.append(f"E3:{m['step3_sent']}")
            if "post_type"    in m: parts.append(m["post_type"])
            if "recipients"   in m: parts.append(f"sent:{m['recipients']}")
            if "blog_slug"    in m: parts.append(f"blog:{m['blog_slug'][:20]}")
            if "liens_scraped" in m: parts.append(f"{m['liens_scraped']} liens")
            summary = " | ".join(parts) if parts else ""
            print(f"  {d.isoformat():<12} {rec['run_type']:<20} {status:<8} "
                  f"{rec['ok']:>4} {rec['failed']:>5} {mins:>6.1f}  {summary}")
            for err in rec.get("errors", []):
                print(f"    ⚠  {err['step']}: {err['error'][:60]}")

    print(f"{'─'*72}\n")


def _print_record(rec: dict):
    print(f"\n  Run: {rec['run_id']}  ({rec['run_type']})")
    print(f"  {rec['started']} → {rec['finished']}  "
          f"({rec['duration']/60:.1f} min)")
    print(f"  Status: {'✅ OK' if rec['status'] == 'ok' else '❌ FAILED'}")
    if rec.get("metrics"):
        print(f"\n  Metrics:")
        for k, v in rec["metrics"].items():
            print(f"    {k:<30} {v}")
    print(f"\n  Steps:")
    for s in rec["steps"]:
        icon = "✅" if s["status"] == "ok" else \
               "❌" if s["status"] == "error" else "⏭"
        print(f"    {icon} {s['name']:<35} {s.get('seconds', 0):>6.0f}s  "
              f"{s.get('detail', '')[:60]}")
        if s.get("error"):
            print(f"       ↳ {s['error'][:80]}")


def main():
    parser = argparse.ArgumentParser(description="LeadFlow Pipeline Log Viewer")
    parser.add_argument("--today",   action="store_true")
    parser.add_argument("--latest",  action="store_true")
    parser.add_argument("--history", type=int, default=7, metavar="N")
    args = parser.parse_args()

    if args.today:
        print_today()
    elif args.latest:
        if LATEST.exists():
            _print_record(json.loads(LATEST.read_text()))
        else:
            print("No runs logged yet.")
    else:
        print_history(args.history)


if __name__ == "__main__":
    main()