# Add to app/api/webhooks.py or create app/api/tcr_webhooks.py
@router.post("/tcr/webhooks/stripe")
async def tcr_stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, os.getenv("TCR_STRIPE_WEBHOOK_SECRET"))
    except Exception:
        raise HTTPException(400, "Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = (session.get("customer_email") or
                 session.get("customer_details", {}).get("email"))
        if email:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM tcr_leads WHERE email = %s", (email,))
                    row = cur.fetchone()
                    if row:
                        lead_id = row[0]
                        # Cancel all pending sequences
                        cur.execute("""
                            UPDATE tcr_scheduled_emails
                            SET status='cancelled', cancelled_at=NOW()
                            WHERE lead_id=%s AND status='pending'
                        """, (lead_id,))
                        # Update lead
                        cur.execute("""
                            UPDATE tcr_leads
                            SET status='CUSTOMER', paid_at=NOW(),
                                current_sequence=NULL, last_event_at=NOW()
                            WHERE id=%s
                        """, (lead_id,))
                        # Log event
                        cur.execute("""
                            INSERT INTO tcr_events
                                (lead_id, event_type, metadata, source)
                            VALUES (%s,'payment_success',%s,'stripe')
                        """, (lead_id, json.dumps({
                            "session_id": session["id"],
                            "amount": session.get("amount_total")
                        })))
                    conn.commit()
            finally:
                conn.close()
    return {"status": "ok"}