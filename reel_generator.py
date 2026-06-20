"""
reel_generator.py
=================
Generates Facebook/Instagram Reels for TaxCase Review.

Flow:
  1. Claude writes a tight 15-30 second script for Romy
  2. HeyGen API renders Romy speaking the script (avatar video)
  3. Script + caption saved locally
  4. Video URL sent to Make.com webhook for Facebook/Instagram posting

Post types:
  weekly-stats    — "X new liens in [county] this week" — 15 seconds
  educational     — one IRS insight explained simply — 30 seconds
  notice          — CP14/CP503/CP504 explainer — 30 seconds
  urgency         — emotional angle, call to action — 20 seconds
  success-story   — anonymized client win — 30 seconds

Task Scheduler (adds to existing rotation):
  Wednesday 9:00 AM → python reel_generator.py --auto
  Sunday    9:00 AM → python reel_generator.py --auto

Usage:
  python reel_generator.py --auto
  python reel_generator.py --post weekly-stats
  python reel_generator.py --post educational
  python reel_generator.py --dry-run         # generate script only, no render
  python reel_generator.py --status          # check pending HeyGen jobs

.env required:
  ANTHROPIC_API_KEY=sk-ant-...
  HEYGEN_API_KEY=sk_V2_...
  HEYGEN_AVATAR_ID=458747b5df084c9cadbab9ebec070ea2
  MAKE_WEBHOOK_URL=https://hook.us2.make.com/...
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HEYGEN_API_KEY    = os.getenv("HEYGEN_API_KEY", "")
HEYGEN_AVATAR_ID  = os.getenv("HEYGEN_AVATAR_ID", "")
MAKE_WEBHOOK_URL  = os.getenv("MAKE_WEBHOOK_URL", "")
SITE_URL          = "https://taxcasereview.org"
PHONE             = "(561) 247-0678"

REELS_DIR         = Path("reels")
REEL_LOG_FILE     = Path("reel_log.json")

# HeyGen voice ID for Romy — Latino male, professional
# Options: use HeyGen's voice list endpoint to find best match
# Default: "en-US-GuyNeural" style — can be updated after testing
HEYGEN_VOICE_ID   = os.getenv("HEYGEN_VOICE_ID", "2d5b0e6cf36f460aa7fc47e3eee4ba54")

FLORIDA_COUNTIES  = [
    "Miami-Dade", "Palm Beach", "Broward", "Orange", "Hillsborough",
    "Pinellas", "Duval", "Sarasota", "Martin", "St. Lucie"
]

NOTICE_ROTATION   = ["CP14", "CP503", "CP504"]

try:
    from app.core.db import get_connection
    HAS_DB = True
except ImportError:
    HAS_DB = False


# ── Day-aware reel type ───────────────────────────────────────────────────────

def get_reel_type_for_today() -> str:
    """
    Wednesday → weekly_stats
    Sunday    → educational or notice (alternates)
    """
    day      = datetime.now().weekday()
    week_num = date.today().isocalendar()[1]
    if day == 2:    return "weekly_stats"        # Wednesday
    elif day == 6:  return "notice" if week_num % 2 == 0 else "educational"
    else:           return "educational"


def get_notice_for_this_week() -> str:
    return NOTICE_ROTATION[date.today().isocalendar()[1] % 3]


# ── DB: lien stats ────────────────────────────────────────────────────────────

def get_weekly_lien_stats() -> dict:
    if not HAS_DB:
        county = random.choice(FLORIDA_COUNTIES)
        return {
            "county": county,
            "count":  random.randint(8, 55),
        }
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.county_name, COUNT(*) AS cnt
                FROM normalized_liens nl
                JOIN counties c ON c.id = nl.county_id
                WHERE nl.created_at >= NOW() - INTERVAL '7 days'
                GROUP BY c.county_name
                ORDER BY cnt DESC
                LIMIT 1
            """)
            r = cur.fetchone()
            return {"county": r[0], "count": r[1]} if r \
                   else {"county": "Miami-Dade", "count": 42}
    finally:
        conn.close()


# ── Claude: script generation ─────────────────────────────────────────────────

def call_claude(prompt: str, max_tokens: int = 400) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":          ANTHROPIC_API_KEY,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        },
        json={
            "model":      "claude-sonnet-4-5",
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def generate_reel_script(reel_type: str, context: dict) -> dict:
    """
    Generate a Reel script + caption using Claude.
    Returns: {script, caption, hashtags, duration_seconds, hook}
    """
    week_of = date.today().strftime("%B %d, %Y")
    county  = context.get("county", random.choice(FLORIDA_COUNTIES))
    count   = context.get("count",  random.randint(10, 50))
    notice  = context.get("notice", get_notice_for_this_week())

    persona = """You are Romy, a 46-year-old Cuban-American IRS resolution specialist in South Florida.
You speak directly to camera in a warm, confident, no-nonsense way.
You sound like the smartest person in the room who also genuinely wants to help.
You never sound scripted, corporate, or salesy.
Speak conversationally — the way you'd explain this to a friend over coffee."""

    duration_map = {
        "weekly_stats":  15,
        "educational":   30,
        "notice":        30,
        "urgency":       20,
        "success_story": 30,
    }
    duration = duration_map.get(reel_type, 30)
    # Approximate words: 2.5 words per second for natural speech
    word_count = int(duration * 2.5)

    prompts = {

        "weekly_stats": f"""{persona}

Write a {duration}-second Reel script ({word_count} words max) about IRS tax liens in {county} County.

Facts: {count} new federal tax liens filed this week of {week_of}.

Script rules:
- Open with a HOOK that stops the scroll in the first 3 seconds
  Example hooks: "Did you know the IRS just filed {count} liens in {county} County?"
  or "{count} people in {county} County got hit by the IRS this week alone."
- Speak directly to someone who might have received a notice
- Mention 1-2 resolution options naturally
- End with ONE clear CTA: visit {SITE_URL} or call {PHONE}
- No hashtags in script — those go in caption
- Natural speech rhythm — add pauses with "..." where Romy should pause

Then write:
CAPTION: (60-80 word Facebook/Instagram caption for this Reel)
HASHTAGS: (8-10 relevant hashtags)

Format your response exactly like:
SCRIPT:
[script here]

CAPTION:
[caption here]

HASHTAGS:
[hashtags here]""",

        "educational": f"""{persona}

Write a {duration}-second Reel script ({word_count} words max) teaching ONE thing about IRS tax debt.

Week of {week_of}. Website: {SITE_URL} | Phone: {PHONE}

Pick ONE angle that feels like insider knowledge:
- How the IRS Fresh Start program actually works
- The difference between a lien and a levy (most people don't know)
- What "currently not collectible" status means and who qualifies
- Why most Offer in Compromise applications get rejected
- What CDP hearing rights are and why the deadline matters
- How penalty abatement works for first-time offenders

Script rules:
- HOOK in first 3 seconds — make them stop scrolling
- One clear insight explained simply
- Brief CTA at end: {SITE_URL} or {PHONE}
- Natural conversational rhythm with "..." for pauses

Then write:
CAPTION: (60-80 word caption)
HASHTAGS: (8-10 hashtags)

Format:
SCRIPT:
[script]

CAPTION:
[caption]

HASHTAGS:
[hashtags]""",

        "notice": f"""{persona}

Write a {duration}-second Reel script ({word_count} words max) explaining IRS notice {notice}.

Week of {week_of}. Website: {SITE_URL} | Phone: {PHONE}

Script rules:
- HOOK: address someone who just got this notice — they're scared right now
- Explain what {notice} means in plain language (no jargon)
- What happens if they ignore it
- What to do RIGHT NOW
- CTA: {SITE_URL} or {PHONE}
- Calm but urgent tone — reassure them, then motivate action
- Natural pauses with "..."

Then write:
CAPTION: (60-80 word caption)
HASHTAGS: (8-10 hashtags)

Format:
SCRIPT:
[script]

CAPTION:
[caption]

HASHTAGS:
[hashtags]""",

        "urgency": f"""{persona}

Write a {duration}-second Reel script ({word_count} words max) about the EMOTIONAL cost of ignoring IRS debt.

Week of {week_of}. Website: {SITE_URL} | Phone: {PHONE}

Emotional angle: the frozen feeling of knowing you need to act but not knowing where to start.

Script rules:
- HOOK: start with the feeling, not the facts
- Acknowledge the stress — make them feel understood
- Pivot: most situations are more fixable than people think
- ONE action they can take today
- CTA: {SITE_URL} or {PHONE}
- Do NOT use "the longer you wait"
- Warm, human, direct

Then write:
CAPTION: (60-80 word caption)
HASHTAGS: (8-10 hashtags)

Format:
SCRIPT:
[script]

CAPTION:
[caption]

HASHTAGS:
[hashtags]""",

        "success_story": f"""{persona}

Write a {duration}-second Reel script ({word_count} words max) sharing an anonymized client success story.

Week of {week_of}. Website: {SITE_URL} | Phone: {PHONE}

Use anonymous descriptor: "A contractor in Broward County" or "A small business owner in Miami-Dade"
IRS debt: pick $18k-$95k (specific number)
Resolution: OIC / installment agreement / penalty abatement / lien withdrawal
Outcome: specific savings or resolution amount

Script rules:
- HOOK: lead with the outcome or the problem
- Tell the story in 2-3 sentences
- Make them feel it could be them
- Include: "Results vary. Every case is unique."
- CTA: {SITE_URL} or {PHONE}

Then write:
CAPTION: (60-80 word caption)
HASHTAGS: (8-10 hashtags)

Format:
SCRIPT:
[script]

CAPTION:
[caption]

HASHTAGS:
[hashtags]""",
    }

    raw = call_claude(prompts.get(reel_type, prompts["educational"]))

    # Parse sections
    script   = _extract_section(raw, "SCRIPT")
    caption  = _extract_section(raw, "CAPTION")
    hashtags = _extract_section(raw, "HASHTAGS")
    hook     = script.split(".")[0].strip() if script else ""

    return {
        "script":           script,
        "caption":          caption,
        "hashtags":         hashtags,
        "hook":             hook,
        "duration_seconds": duration,
        "reel_type":        reel_type,
        "county":           county,
        "week_of":          week_of,
    }


def _extract_section(text: str, section: str) -> str:
    """Extract a labeled section from Claude's response."""
    lines  = text.splitlines()
    result = []
    inside = False
    for line in lines:
        if line.strip().startswith(f"{section}:"):
            inside = True
            # Content might be on same line
            rest = line.split(":", 1)[1].strip()
            if rest:
                result.append(rest)
            continue
        if inside:
            # Stop at next section header
            if any(line.strip().startswith(f"{s}:")
                   for s in ["SCRIPT", "CAPTION", "HASHTAGS"]):
                break
            result.append(line)
    return "\n".join(result).strip()


# ── HeyGen API ────────────────────────────────────────────────────────────────

def submit_heygen_video(script: dict) -> dict:
    """
    Submit a video generation job to HeyGen.
    Returns: {video_id, status, estimated_seconds}
    """
    if not HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY not set in .env")
    if not HEYGEN_AVATAR_ID:
        raise RuntimeError("HEYGEN_AVATAR_ID not set in .env")

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type":      "avatar",
                    "avatar_id": HEYGEN_AVATAR_ID,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type":     "text",
                    "input_text": script["script"],
                    "voice_id": HEYGEN_VOICE_ID,
                    "speed":    1.0,
                },
                "background": {
                    "type":  "color",
                    "value": "#0f1b2d",   # TaxCase navy
                },
            }
        ],
        "dimension": {
            "width":  1080,
            "height": 1920,   # Portrait — optimized for Reels
        },
        "aspect_ratio": "9:16",
        "caption":       True,   # Auto-captions for accessibility
    }

    r = requests.post(
        "https://api.heygen.com/v2/video/generate",
        headers={
            "X-Api-Key":    HEYGEN_API_KEY,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if r.status_code != 200:
        raise RuntimeError(f"HeyGen API error: {r.status_code} — {r.text[:200]}")

    data     = r.json()
    video_id = data.get("data", {}).get("video_id", "")
    print(f"  🎬 HeyGen job submitted: {video_id}")
    print(f"  ⏱  Estimated render time: 2-5 minutes")
    return {"video_id": video_id, "status": "processing"}


def check_heygen_status(video_id: str) -> dict:
    """Check status of a HeyGen render job."""
    r = requests.get(
        f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
        headers={"X-Api-Key": HEYGEN_API_KEY},
        timeout=15,
    )
    data   = r.json().get("data", {})
    status = data.get("status", "unknown")
    url    = data.get("video_url", "")
    return {"status": status, "video_url": url, "video_id": video_id}


def wait_for_heygen(video_id: str, max_minutes: int = 10) -> str:
    """
    Poll HeyGen until video is ready or timeout.
    Returns video URL or empty string on failure.
    """
    print(f"  ⏳ Waiting for HeyGen render (up to {max_minutes} min)...")
    for attempt in range(max_minutes * 4):   # check every 15 seconds
        time.sleep(15)
        result = check_heygen_status(video_id)
        status = result["status"]
        print(f"  [{attempt*15}s] Status: {status}")

        if status == "completed":
            print(f"  ✅ Video ready: {result['video_url']}")
            return result["video_url"]
        elif status in ("failed", "error"):
            print(f"  ❌ HeyGen render failed")
            return ""

    print(f"  ⏰ Timeout after {max_minutes} minutes")
    return ""


# ── Reel log ──────────────────────────────────────────────────────────────────

def load_reel_log() -> list[dict]:
    if REEL_LOG_FILE.exists():
        try:
            return json.loads(REEL_LOG_FILE.read_text())
        except Exception:
            return []
    return []

def save_reel(entry: dict):
    log = load_reel_log()
    log.append(entry)
    REEL_LOG_FILE.write_text(json.dumps(log[-100:], indent=2))

def check_pending_reels():
    """Check status of all pending HeyGen jobs."""
    log     = load_reel_log()
    pending = [r for r in log if r.get("status") == "processing"]
    if not pending:
        print("No pending HeyGen jobs.")
        return
    print(f"\n{len(pending)} pending jobs:\n")
    for entry in pending:
        result = check_heygen_status(entry["video_id"])
        print(f"  {entry['reel_type']} | {entry['date']} | "
              f"Status: {result['status']}")
        if result["video_url"]:
            print(f"  URL: {result['video_url']}")
            # Update log entry
            entry["status"]    = "completed"
            entry["video_url"] = result["video_url"]
    REEL_LOG_FILE.write_text(json.dumps(log, indent=2))


# ── Make.com: post Reel ───────────────────────────────────────────────────────

def post_reel_via_make(script: dict, video_url: str) -> dict:
    """Send Reel to Make.com for Facebook/Instagram posting."""
    if not MAKE_WEBHOOK_URL:
        print("  ⚠️  MAKE_WEBHOOK_URL not set")
        return {"error": "no webhook"}

    caption_with_tags = f"{script['caption']}\n\n{script['hashtags']}"

    payload = {
        "message":   caption_with_tags,
        "video_url": video_url,
        "reel":      True,         # tells Make.com to post as Reel
        "link":      SITE_URL,
    }
    r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
    return {"status": r.status_code, "response": r.text}


# ── Save script locally ───────────────────────────────────────────────────────

def save_script_locally(script: dict, video_id: str = ""):
    REELS_DIR.mkdir(exist_ok=True)
    slug     = script["reel_type"].replace("_", "-")
    filename = f"{date.today().isoformat()}-reel-{slug}.txt"
    out      = REELS_DIR / filename

    content = f"""REEL SCRIPT — {script['reel_type'].upper()}
Date: {script['week_of']}
Duration: {script['duration_seconds']}s
County: {script.get('county', '—')}
HeyGen Video ID: {video_id or 'not submitted'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOOK (first 3 seconds):
{script['hook']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FULL SCRIPT (Romy speaks this):
{script['script']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAPTION:
{script['caption']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HASHTAGS:
{script['hashtags']}
"""
    out.write_text(content, encoding="utf-8")
    print(f"  💾 Script saved: {out}")
    return str(out)


# ── Pipeline logger ───────────────────────────────────────────────────────────

def get_logger(run_type: str):
    try:
        from pipeline_log import PipelineLogger
        logger = PipelineLogger(run_type)
        logger.start()
        return logger
    except ImportError:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TaxCase Review Reel Generator")
    parser.add_argument("--auto",    action="store_true",
                        help="Auto-detect day and generate correct reel type")
    parser.add_argument("--post",    choices=["weekly-stats","educational",
                                               "notice","urgency","success-story"])
    parser.add_argument("--notice",  default=None, choices=["CP14","CP503","CP504"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate script only — do not submit to HeyGen")
    parser.add_argument("--status",  action="store_true",
                        help="Check status of pending HeyGen jobs")
    args = parser.parse_args()

    if args.status:
        check_pending_reels()
        return

    if not args.auto and not args.post:
        parser.print_help()
        return

    print(f"\n{'='*55}")
    print(f"  TaxCase Review Reel Generator")
    print(f"  {datetime.now().strftime('%A %B %d, %Y %I:%M %p')}")
    print(f"  Avatar  : Romy ({HEYGEN_AVATAR_ID[:8]}...)")
    print(f"  {'DRY RUN — script only' if args.dry_run else 'LIVE — will render video'}")
    print(f"{'='*55}\n")

    logger = get_logger("reel_generator")

    # ── Determine reel type ───────────────────────────────────────────────────
    if args.auto:
        reel_type = get_reel_type_for_today()
        print(f"Auto mode → {reel_type}\n")
    else:
        type_map  = {
            "weekly-stats":  "weekly_stats",
            "educational":   "educational",
            "notice":        "notice",
            "urgency":       "urgency",
            "success-story": "success_story",
        }
        reel_type = type_map[args.post]

    # ── Build context ─────────────────────────────────────────────────────────
    stats   = get_weekly_lien_stats()
    context = {
        "county": stats.get("county", random.choice(FLORIDA_COUNTIES)),
        "count":  stats.get("count",  random.randint(10, 40)),
        "notice": args.notice or get_notice_for_this_week(),
    }

    # ── Generate script ───────────────────────────────────────────────────────
    if logger: logger.step_start("generate_script")
    print(f"Generating {reel_type} script with Claude...\n")
    script = generate_reel_script(reel_type, context)

    print(f"{'─'*55}")
    print(f"HOOK: {script['hook']}\n")
    print(f"SCRIPT ({script['duration_seconds']}s):")
    print(script["script"])
    print(f"\nCAPTION:")
    print(script["caption"])
    print(f"\nHASHTAGS: {script['hashtags']}")
    print(f"{'─'*55}\n")

    if logger:
        logger.step_done("generate_script", ok=True,
                         detail=f"{reel_type} | {script['duration_seconds']}s | {context['county']}")

    # ── Dry run stops here ────────────────────────────────────────────────────
    if args.dry_run:
        save_script_locally(script)
        print("Dry run — script saved, not submitted to HeyGen.\n")
        if logger:
            logger.step_skip("heygen_render", "dry-run")
            logger.finish({
                "reel_type":        reel_type,
                "duration_seconds": script["duration_seconds"],
                "county":           context["county"],
                "dry_run":          True,
                "video_rendered":   False,
            })
        return

    # ── Submit to HeyGen ──────────────────────────────────────────────────────
    if logger: logger.step_start("heygen_render")
    print("Submitting to HeyGen for rendering...\n")
    try:
        job       = submit_heygen_video(script)
        video_id  = job["video_id"]
        script_path = save_script_locally(script, video_id)

        # Save to log as processing
        log_entry = {
            "date":             date.today().isoformat(),
            "reel_type":        reel_type,
            "county":           context["county"],
            "duration_seconds": script["duration_seconds"],
            "video_id":         video_id,
            "status":           "processing",
            "video_url":        "",
            "script_path":      script_path,
            "hook":             script["hook"][:80],
        }
        save_reel(log_entry)

        if logger:
            logger.step_done("heygen_render", ok=True,
                             detail=f"video_id: {video_id}")
            logger.step_start("wait_for_video")

        # ── Wait for render ───────────────────────────────────────────────────
        video_url = wait_for_heygen(video_id, max_minutes=10)

        if video_url:
            # Update log
            log = load_reel_log()
            for entry in log:
                if entry.get("video_id") == video_id:
                    entry["status"]    = "completed"
                    entry["video_url"] = video_url
            REEL_LOG_FILE.write_text(json.dumps(log, indent=2))

            if logger:
                logger.step_done("wait_for_video", ok=True,
                                 detail=video_url[:80])
                logger.step_start("post_to_make")

            # ── Post via Make.com ─────────────────────────────────────────────
            print(f"\nPosting Reel via Make.com...")
            result   = post_reel_via_make(script, video_url)
            make_ok  = result.get("status") == 200
            print(f"Make.com: {result}")

            if make_ok:
                print("✅ Reel posted successfully!\n")
            else:
                print("❌ Make.com post failed — check scenario logs\n")

            if logger:
                logger.step_done("post_to_make", ok=make_ok)
                logger.finish({
                    "reel_type":        reel_type,
                    "duration_seconds": script["duration_seconds"],
                    "county":           context["county"],
                    "video_id":         video_id,
                    "video_url":        video_url,
                    "posted":           make_ok,
                })
        else:
            print("❌ HeyGen render timed out or failed.")
            print(f"   Check status later: python reel_generator.py --status")
            print(f"   Video ID: {video_id}\n")
            if logger:
                logger.step_done("wait_for_video", ok=False,
                                 error="timeout or render failed")
                logger.finish({
                    "reel_type": reel_type,
                    "video_id":  video_id,
                    "posted":    False,
                })

    except Exception as e:
        print(f"❌ Error: {e}")
        if logger:
            logger.step_done("heygen_render", ok=False, error=str(e))
            logger.finish({"reel_type": reel_type, "error": str(e)})
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
