# ESP Migration Plan — Amazon SES for cold outreach

**Status: PROPOSAL / NOT IMPLEMENTED.** Nothing in this doc has been applied.
No DNS records, AWS resources, or code changes will be made until reviewed and
approved.

## Why

`romy@taxcasereview.org` (Google Workspace) is **throttled down** to ~56/day —
it sustained 100–240/day in late May, then Google clamped it after the volume
spike + cold-outreach spam signals. Auth (SPF/DKIM/DMARC) is already correct, so
this is a **sending-reputation** ceiling, not a config bug. To reach 100/day
reliably we need dedicated infrastructure on a **separate sending identity** so
cold-send reputation never touches the mailbox used for replies/bookings.

SES is preferred: **$0.10 / 1,000 emails**, no monthly minimum, and it speaks
plain SMTP (so the existing `smtplib` worker barely changes).

---

## 1. Subdomain setup — `send.taxcasereview.org`

Use a dedicated subdomain so its reputation is isolated from the root domain
(which keeps serving Google Workspace mail for romy@/info@).

DNS records to add (at the domain registrar/DNS host — **review before adding**):

| Type | Host | Value | Purpose |
|---|---|---|---|
| MX (optional) | `send.taxcasereview.org` | `10 feedback-smtp.us-east-1.amazonses.com` | SES custom MAIL FROM (bounces) |
| TXT (SPF) | `send.taxcasereview.org` | `v=spf1 include:amazonses.com ~all` | authorize SES for the subdomain |
| CNAME ×3 (DKIM) | `<token1/2/3>._domainkey.send.taxcasereview.org` | `<token>.dkim.amazonses.com` | SES Easy DKIM (AWS gives the exact 3 tokens) |
| TXT (DMARC) | `_dmarc.send.taxcasereview.org` | `v=DMARC1; p=quarantine; rua=mailto:romy@taxcasereview.org; pct=100` | align with root policy, collect reports |

Notes:
- **Do NOT touch the root `taxcasereview.org` SPF/DKIM/DMARC** — those stay as-is
  for the Workspace mailbox. The subdomain gets its **own** SPF + DKIM.
- The 3 DKIM CNAMEs come from SES (Easy DKIM) when you verify the domain — paste
  them exactly.
- Custom MAIL FROM (`send.taxcasereview.org`) makes SPF/bounce handling align to
  our subdomain instead of `amazonses.com` — improves deliverability.

---

## 2. SES account setup (AWS console)

1. **Region:** pick one and stick to it (e.g. `us-east-1`). SMTP endpoint will be
   `email-smtp.us-east-1.amazonaws.com`.
2. **Verify the sending domain:** SES → *Verified identities* → *Create identity*
   → Domain → `send.taxcasereview.org` → enable **Easy DKIM** → AWS shows the 3
   DKIM CNAMEs + (optional) custom MAIL FROM records → add them to DNS (section 1)
   → wait for status **Verified** (minutes–hours).
3. **Request production access:** new SES accounts are in **sandbox** (can only
   send to *verified* addresses, 200/day cap). Open *Account dashboard* →
   *Request production access*: describe the use case (B2B tax-resolution
   outreach to public-record lien filers), confirm **list source, opt-out
   handling (every email has unsubscribe + honors it), and bounce/complaint
   handling**. Approval is usually < 24h.
4. **Set a sending identity / From:** e.g. `romy@send.taxcasereview.org` (display
   name "Romy — TaxCase Review"). Set **Reply-To: `romy@taxcasereview.org`** so
   replies land in the real Workspace mailbox.
5. **Create SMTP credentials:** SES → *SMTP settings* → *Create SMTP credentials*
   (this makes an IAM user with SES-send perms). Save the **SMTP username +
   password** — these are NOT your AWS keys.
6. **Configuration set + monitoring:** create a configuration set with
   **bounce/complaint event publishing** (to SNS or CloudWatch). SES auto-pauses
   accounts that exceed **bounce > ~5%** or **complaint > ~0.1%**, so we must
   watch these from day 1.
7. **Suppression list:** enable account-level suppression (auto-suppress hard
   bounces + complaints) so we never re-send to bad addresses.

---

## 3. Warmup schedule (0 → 100/day)

Our list is **cold, scraped public-record data** (higher bounce/complaint risk),
so warm **conservatively** and validate first. SES reputation is unforgiving —
one bad week of bounces pauses the account.

**Pre-step (do before any volume):** run the send list through an email
**verification pass** (e.g. ZeroBounce/NeverBounce or SES's own bounce feedback
on a tiny seed batch) and drop invalid/role/disposable addresses. Bounce rate is
the #1 thing that gets SES accounts suspended.

| Phase | Days | Daily volume | Watch |
|---|---|---|---|
| Seed | 1–2 | 5–10 (to engaged/known-good only) | 0 bounces, opens land in inbox not spam |
| Ramp 1 | 3–5 | 20 | bounce < 3%, complaint < 0.1% |
| Ramp 2 | 6–9 | 35 → 50 | same gates; pause/hold if exceeded |
| Ramp 3 | 10–14 | 60 → 80 | same |
| Target | 15–18 | **100/day** sustained | maintain gates |

Realistic timeline: **~2.5–3 weeks** to *reliably* sustain 100/day, assuming the
list is validated and bounce/complaint stay green. If bounces spike, hold volume
flat (don't advance) until they recover. Keep the `--delay 12` pacing.

---

## 4. Code changes (SMTP → SES, minimum diff)

**Use the SES SMTP endpoint, not boto3** — the worker already uses
`smtplib.SMTP_SSL`, so this is a ~10-line change vs. rewriting send logic for the
boto3 API.

Today (`app/workers/send_email_sequence.py`):
```python
SENDER_EMAIL = os.getenv("GMAIL_SENDER", "romy@taxcasereview.org")
APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

def get_gmail_service():
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
    server.login(SENDER_EMAIL, APP_PASSWORD)
    return server
```

Proposed (env-driven, provider-agnostic — Gmail stays the default until cutover):
```python
SMTP_HOST   = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER   = os.getenv("SMTP_USER", os.getenv("GMAIL_SENDER", "romy@taxcasereview.org"))
SMTP_PASS   = os.getenv("SMTP_PASS", os.getenv("GMAIL_APP_PASSWORD", "")).replace(" ", "")
FROM_EMAIL  = os.getenv("FROM_EMAIL", os.getenv("GMAIL_SENDER", "romy@taxcasereview.org"))
REPLY_TO    = os.getenv("REPLY_TO", "romy@taxcasereview.org")

def get_smtp_service():
    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx)
    server.login(SMTP_USER, SMTP_PASS)
    return server
```
- `send_message()` already sets `Reply-To` — point it at `REPLY_TO` (the
  Workspace mailbox) and `From` at `FROM_EMAIL` (the SES subdomain identity).
- `.env` at cutover:
  ```
  SMTP_HOST=email-smtp.us-east-1.amazonaws.com
  SMTP_PORT=465
  SMTP_USER=<SES SMTP username>
  SMTP_PASS=<SES SMTP password>
  FROM_EMAIL=romy@send.taxcasereview.org
  REPLY_TO=romy@taxcasereview.org
  ```
- **No change** to the queueing, scoring, tracking, unsubscribe, throttle-stop,
  or logging logic. Throttle detection (`is_gmail_throttle_error`) should be
  generalized to also catch SES throttling/limit responses.
- Everything stays env-driven, so rollback = revert the `.env` SMTP_* values.

---

## 5. What happens to `romy@taxcasereview.org` during migration

- **Stop all cold sends from it.** It is reputation-throttled; keep it off the
  outreach path entirely.
- **Keep it for replies + bookings only** — it remains the `Reply-To` on every
  SES email, so prospect replies and Calendly/booking mail still flow to the
  human inbox as today. The daily-summary sender (`info@taxcasereview.org`) and
  any transactional/booking mail also stay on Workspace.
- **Let it rest/recover.** Reduced volume + only warm, human replies will let its
  sender reputation recover over the coming weeks.
- The root domain's existing SPF/DKIM/DMARC are untouched; only the new
  `send.` subdomain carries cold-outreach reputation.

---

## 6. Cutover checklist (when approved)

1. Add DNS (section 1); confirm SES domain status **Verified**.
2. Get SES **production access** approved.
3. Validate + clean the send list (drop bad addresses).
4. Add `SMTP_*` / `FROM_EMAIL` / `REPLY_TO` to `.env`; deploy the env-driven
   worker change.
5. Run the warmup schedule (section 3), watching bounce/complaint daily.
6. Once 100/day is stable, raise `DAILY_EMAIL_LIMIT` from 50 → 100 and update the
   `--limit` in `setup_tasks.ps1`.
7. Keep Gmail config in `.env` (commented) for instant rollback.

## Cost
At 100/day = ~3,000/month → **~$0.30/month** in SES send fees (plus optional
list-verification one-time cost). Negligible vs. the deliverability gain.
