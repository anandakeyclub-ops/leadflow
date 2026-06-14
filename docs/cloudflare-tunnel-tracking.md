# Email Open/Click Tracking — Cloudflare Tunnel (PAUSED / alternative)

> **PAUSED 2026-06-14.** Not needed. The active solution is the ngrok **reserved
> static domain** (`deflator-rover-outtakes.ngrok-free.dev`), already configured and
> verified — see `email-tracking.md`. That fixes URL stability with zero DNS/Cloudflare
> changes. Keep this runbook only as a fallback if ngrok is ever dropped.

**Status: SETUP RUNBOOK — not cut over.** `.env` points at the ngrok static domain.

## Why this design

The email **worker** (`send_email_sequence.py`), `daily_summary`, and all open-rate
reporting read the **local Postgres at `localhost:5434`** (`.env` has no
`DATABASE_URL`, so `app/core/db.py` uses `DB_HOST/DB_PORT`).

The **Render** API (`leadflow-api-x7pf.onrender.com`) is connected to a **different**
(cloud) database. A unique-UUID test confirmed an open recorded via Render does
**not** appear in `localhost:5434`. So **tracking must NOT point at Render** — opens
would land in a DB the worker never reads and the open rate would read ~0.

Instead: a Cloudflare Tunnel gives a **stable public hostname** that forwards to the
**local** LeadFlow API, which writes opens to `localhost:5434` — the same DB the
worker reads. This fixes ngrok's fragility (rotating free URLs) without touching the
database split.

```
Email client → https://track.taxcasereview.org/t/o/{id}  (Cloudflare edge)
            → cloudflared tunnel (this PC)
            → http://localhost:8000  (LeadFlow API: uvicorn app.api.main:app)
            → localhost:5434  (email_opens / email_sends)   ✓ same DB as the worker
```

## Fixed identifiers

| Thing | Value |
|---|---|
| Tunnel name | `leadflow-tracking` |
| Public hostname | `track.taxcasereview.org` |
| Local target | `http://localhost:8000` (LeadFlow API — `uvicorn app.api.main:app`, "LeadFlow - API Server" task) |
| cloudflared binary | `C:\Program Files (x86)\cloudflared\cloudflared.exe` (v2026.5.2) |
| Pixel path | `/t/o/{tracking_id}` (also accepts `.gif`) → records open, returns 1×1 GIF |
| Click path | `/t/c/{tracking_id}?url=...` → records click, 302 redirect |

## Prerequisite — DNS (one-time, interactive)

`taxcasereview.org` is on **Google Cloud DNS**, not Cloudflare. A Cloudflare named
tunnel can only attach a hostname that is in a Cloudflare zone. Pick one:

- **Recommended — delegate just the subdomain** (keeps the website + email/MX on
  Google DNS, untouched):
  1. In the Cloudflare dashboard, **Add a site**: `track.taxcasereview.org`.
  2. Cloudflare assigns two nameservers (e.g. `xxx.ns.cloudflare.com`).
  3. In **Google Cloud DNS** for `taxcasereview.org`, add an **NS** record:
     host `track` → those two Cloudflare nameservers.
  4. Wait for Cloudflare to mark the zone **Active** (minutes–hours).
- Alternative — move the whole domain to Cloudflare (more disruptive: migrates all
  DNS incl. website + email MX). Only if you want everything on Cloudflare anyway.

## Setup runbook (run as the user; some steps need an elevated shell)

Use the full path or add `C:\Program Files (x86)\cloudflared` to PATH.

```powershell
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

# 1. Authenticate (opens a browser — authorize the track.taxcasereview.org zone)
& $cf tunnel login

# 2. Create the tunnel (writes creds to C:\Users\Dana\.cloudflared\<UUID>.json)
& $cf tunnel create leadflow-tracking
#    -> note the Tunnel UUID it prints

# 3. Attach the hostname (creates the CNAME in the Cloudflare zone)
& $cf tunnel route dns leadflow-tracking track.taxcasereview.org
```

Then create `C:\Users\Dana\.cloudflared\config.yml`:

```yaml
tunnel: leadflow-tracking
credentials-file: C:\Users\Dana\.cloudflared\<UUID>.json   # from step 2
ingress:
  - hostname: track.taxcasereview.org
    service: http://localhost:8000
  - service: http_status:404
```

Foreground test run:

```powershell
& $cf tunnel run leadflow-tracking
```

## Start on boot (so tracking can't silently die)

Install cloudflared as a **Windows service** (cleanest; auto-starts on boot,
restarts on crash). Run in an **elevated** PowerShell:

```powershell
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
& $cf service install        # installs "Cloudflared" service reading the config.yml above
Start-Service Cloudflared
Get-Service Cloudflared
```

(Alternative without a service — Task Scheduler ONSTART task:
`Register-ScheduledTask -TaskName "LeadFlow - Cloudflare Tunnel" -Action (New-ScheduledTaskAction -Execute "C:\Program Files (x86)\cloudflared\cloudflared.exe" -Argument "tunnel run leadflow-tracking") -Trigger (New-ScheduledTaskTrigger -AtStartup) -RunLevel Highest -Force`)

## Cutover + test (DO THIS BEFORE trusting it; keep ngrok running until it passes)

1. Edit `.env` (local only — **do not commit**):
   ```
   TRACKING_BASE_URL=https://track.taxcasereview.org
   ```
   `open_pixel_url()` / `tracked_link()` read `TRACKING_BASE_URL`, and `wrap_html()`
   uses them — so no code change is needed; new sends pick up the new base.
2. Generate a pixel and hit it, then confirm it landed in the **local** DB:
   ```powershell
   # from repo root, with the tunnel + API running
   python -X utf8 -c "import uuid,urllib.request; t=str(uuid.uuid4()); print(t); urllib.request.urlopen(f'https://track.taxcasereview.org/t/o/{t}.gif',timeout=30)"
   # then:
   python -X utf8 -c "from app.core.db import get_connection,release_connection as r; c=get_connection(); cur=c.cursor(); cur.execute(\"SELECT * FROM email_opens WHERE tracking_id=%s::uuid\", ('<paste-uuid>',)); print(cur.fetchone()); r(c)"
   ```
   Or: `python -m app.workers.send_email_sequence --auto --limit 1 --dry-run`, copy the
   pixel URL from the generated HTML, open it in a browser, and re-run the DB check.
3. Only once the open row appears in `localhost:5434`, the cutover is verified.
4. Leave ngrok running for a day or two as a fallback; then stop it.

## Known dependency / gotcha

- The **LeadFlow API must be running and healthy** ("LeadFlow - API Server" task,
  `uvicorn app.api.main:app` on :8000). It writes opens via a 5-connection pool;
  the open/click routes use `release_connection` (NOT `conn.close()`, which leaks
  pool slots and silently drops opens once exhausted — this was a live bug, fixed
  2026-06-14). If opens stop recording, restart that task to reset the pool.
