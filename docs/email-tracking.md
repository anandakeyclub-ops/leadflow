# Email Open/Click Tracking — Active Setup

**Active solution: ngrok reserved (static) domain → local API → local DB.**
Verified end-to-end 2026-06-14 (open recorded in `localhost:5434` via the public URL).

## How it works

```
Email client → https://deflator-rover-outtakes.ngrok-free.dev/t/o/{id}   (ngrok edge, FIXED domain)
            → ngrok tunnel (this PC)
            → http://localhost:8000   (LeadFlow API: uvicorn app.api.main:app)
            → localhost:5434          (email_opens / email_sends)  ← same DB the worker reads
```

- **Public domain:** `https://deflator-rover-outtakes.ngrok-free.dev` — this is ngrok's
  **free reserved static domain**. It does **not** rotate; it is pinned with `--domain=`.
- **Routes:** `/t/o/{tracking_id}` (open pixel → records open, returns 1×1 GIF, never 404s;
  also accepts `.gif`) · `/t/c/{tracking_id}?url=...` (click → records click, 302 redirect).
- **`.env`:** `TRACKING_BASE_URL=https://deflator-rover-outtakes.ngrok-free.dev`
  (`.env` is gitignored / local only). `open_pixel_url()` and `tracked_link()` read it,
  and `wrap_html()` uses them, so changing this one value re-points all tracking.

## Scheduled tasks (Windows Task Scheduler)

| Task | Purpose |
|---|---|
| `LeadFlow - API Server` | `uvicorn app.api.main:app --host 0.0.0.0 --port 8000` (serves the tracking routes) |
| `LeadFlow - ngrok Tunnel` | `ngrok http --domain=deflator-rover-outtakes.ngrok-free.dev 8000` (pins the static domain) |
| `LeadFlow - ngrok Watchdog` | `watchdog_ngrok.ps1` — restarts the tunnel if it dies |

## ⚠️ Do NOT point tracking at Render

`leadflow-api-x7pf.onrender.com` runs the same code but is connected to a **different
(cloud) database**. A unique-UUID test confirmed an open recorded via Render does **not**
appear in `localhost:5434`. Pointing `TRACKING_BASE_URL` at Render would silently send
opens to a DB the worker never reads → open rate reads ~0. Keep tracking on the
ngrok-static-domain → local-API path.

## Gotcha — connection pool (caused a silent outage, fixed 2026-06-14)

The open/click routes (`app/api/routes/tracking.py`, `click_tracking.py`) use a 5-connection
pool and **must** call `release_connection()`, NOT `conn.close()`. With `conn.close()` the
pool slot leaks; after ~5 pixel hits the pool is exhausted and every subsequent open insert
fails silently (still returns the 200 GIF) — so opens stop recording with no error.
This happened and was fixed. **If opens stop recording, restart `LeadFlow - API Server`**
to reset the pool, and confirm the routes still use `release_connection`.

## Verify (anytime)

```powershell
# hit the public pixel with a random id, then confirm it landed locally
python -X utf8 -c "import uuid,urllib.request; t=str(uuid.uuid4()); print(t); urllib.request.urlopen(f'https://deflator-rover-outtakes.ngrok-free.dev/t/o/{t}.gif',timeout=30)"
python -X utf8 -c "from app.core.db import get_connection,release_connection as r; c=get_connection(); cur=c.cursor(); cur.execute(\"SELECT opened_at FROM email_opens WHERE tracking_id=%s::uuid\", ('<paste-id>',)); print(cur.fetchone()); r(c)"
```

## Alternative (paused)

A Cloudflare Tunnel runbook exists in `cloudflare-tunnel-tracking.md`. It was paused:
the ngrok reserved static domain already provides a stable URL with zero DNS/Cloudflare
changes, so the Cloudflare path is unnecessary unless ngrok is dropped later.
