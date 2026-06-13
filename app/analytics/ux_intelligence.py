"""Converts Clarity metrics into a simple UX health score and action item."""
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass
class UXIntelligence:
    health_score: int = 100
    primary_issue: str = "No major UX issue detected"
    action_item: str = "Keep collecting behavior data."
    notes: list[str] = None
    status: str = "ok"
    error: str = ""
    def __post_init__(self):
        if self.notes is None: self.notes = []

def analyze_ux(clarity: dict) -> dict:
    try:
        notes = []
        score = 100
        sessions = int(clarity.get("sessions", 0) or 0)
        bot_sessions = int(clarity.get("bot_sessions", 0) or 0)
        rage = int(clarity.get("rage_clicks", 0) or 0)
        dead = int(clarity.get("dead_clicks", 0) or 0)
        quick = int(clarity.get("quick_backs", 0) or 0)
        scroll = float(clarity.get("average_scroll_depth", 0) or 0)
        errors = int(clarity.get("script_errors", 0) or 0)
        if sessions == 0:
            return asdict(UXIntelligence(0, "No Clarity sessions yet", "Wait for live traffic or verify Clarity script is installed.", ["No behavior data available."]))
        if bot_sessions >= sessions:
            notes.append("Current Clarity traffic appears to be mostly bots/testing."); score -= 10
        if rage > 0:
            notes.append(f"{rage} rage-click event(s) detected."); score -= min(30, rage * 10)
        if dead > 0:
            notes.append(f"{dead} dead-click event(s) detected."); score -= min(25, dead * 8)
        if quick > 0:
            notes.append(f"{quick} quick-back event(s) detected."); score -= min(20, quick * 8)
        if errors > 0:
            notes.append(f"{errors} script error(s) detected."); score -= min(25, errors * 10)
        if scroll and scroll < 25:
            notes.append(f"Low average scroll depth: {scroll}%."); score -= 15
        score = max(0, min(100, score))
        if rage > 0 or dead > 0:
            primary = "Users may be clicking confusing/non-interactive elements."
            action = "Review Clarity recordings and simplify the affected area."
        elif scroll and scroll < 25:
            primary = "Users are not scrolling deeply enough."
            action = "Strengthen above-the-fold clarity and move primary CTA higher."
        elif bot_sessions >= sessions:
            primary = "Behavior sample is not real user traffic yet."
            action = "Collect real sessions before making UX decisions."
        else:
            primary = "No major UX issue detected yet."
            action = "Continue monitoring as traffic grows."
        return asdict(UXIntelligence(score, primary, action, notes or ["No major friction signals detected."]))
    except Exception as e:
        return asdict(UXIntelligence(0, "UX analysis failed", "Check Clarity payload and integration.", [], "error", str(e)))
