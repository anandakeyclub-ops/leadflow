-- ============================================================
-- Email tracking tables for Leadflow
-- Run once: psql -d leadflow -f migrations/add_email_tracking.sql
-- ============================================================

-- Track every email sent
CREATE TABLE IF NOT EXISTS email_sends (
    id                  SERIAL PRIMARY KEY,
    lead_id             INTEGER REFERENCES matched_leads(id),
    contact_id          INTEGER,
    campaign_id         TEXT    NOT NULL DEFAULT 'default',
    to_email            TEXT    NOT NULL,
    to_name             TEXT,
    subject             TEXT,
    tracking_id         UUID    NOT NULL DEFAULT gen_random_uuid(),
    sent_at             TIMESTAMPTZ,
    status              TEXT    NOT NULL DEFAULT 'queued',  -- queued, sent, failed, bounced
    error_message       TEXT,
    county_name         TEXT,
    lien_type           TEXT,
    lien_amount         NUMERIC,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sends_tracking ON email_sends(tracking_id);
CREATE INDEX IF NOT EXISTS idx_email_sends_lead ON email_sends(lead_id);
CREATE INDEX IF NOT EXISTS idx_email_sends_sent ON email_sends(sent_at);
CREATE INDEX IF NOT EXISTS idx_email_sends_status ON email_sends(status);

-- Track opens (via 1x1 pixel)
CREATE TABLE IF NOT EXISTS email_opens (
    id              SERIAL PRIMARY KEY,
    tracking_id     UUID    NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address      TEXT,
    user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_opens_tracking ON email_opens(tracking_id);

-- Track clicks (via redirect links)
CREATE TABLE IF NOT EXISTS email_clicks (
    id              SERIAL PRIMARY KEY,
    tracking_id     UUID    NOT NULL,
    clicked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    url             TEXT,
    ip_address      TEXT,
    user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_clicks_tracking ON email_clicks(tracking_id);

-- Track replies (manual entry or Gmail webhook)
CREATE TABLE IF NOT EXISTS email_replies (
    id              SERIAL PRIMARY KEY,
    tracking_id     UUID,
    lead_id         INTEGER,
    replied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reply_snippet   TEXT,
    outcome         TEXT   -- interested, not_interested, wrong_person, converted
);

-- Conversions — when a lien review is purchased
CREATE TABLE IF NOT EXISTS conversions (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER REFERENCES matched_leads(id),
    tracking_id     UUID,
    converted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revenue         NUMERIC NOT NULL DEFAULT 399.00,
    notes           TEXT
);

-- Daily pipeline snapshot (for trend charts in summary email)
CREATE TABLE IF NOT EXISTS daily_snapshots (
    id              SERIAL PRIMARY KEY,
    snapshot_date   DATE    NOT NULL DEFAULT CURRENT_DATE,
    county_name     TEXT    NOT NULL,
    total_permits   INTEGER DEFAULT 0,
    total_liens     INTEGER DEFAULT 0,
    total_leads     INTEGER DEFAULT 0,
    new_leads_24h   INTEGER DEFAULT 0,
    emails_sent     INTEGER DEFAULT 0,
    emails_opened   INTEGER DEFAULT 0,
    emails_clicked  INTEGER DEFAULT 0,
    emails_bounced  INTEGER DEFAULT 0,
    conversions     INTEGER DEFAULT 0,
    revenue         NUMERIC DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(snapshot_date, county_name)
);

-- Convenience view for campaign metrics
CREATE OR REPLACE VIEW campaign_metrics AS
SELECT
    es.campaign_id,
    es.county_name,
    COUNT(DISTINCT es.id)                                           AS emails_sent,
    COUNT(DISTINCT eo.tracking_id)                                  AS unique_opens,
    COUNT(DISTINCT ec.tracking_id)                                  AS unique_clicks,
    COUNT(DISTINCT CASE WHEN es.status = 'bounced' THEN es.id END)  AS bounces,
    COUNT(DISTINCT c.id)                                            AS conversions,
    COALESCE(SUM(c.revenue), 0)                                     AS revenue,
    ROUND(
        100.0 * COUNT(DISTINCT eo.tracking_id) / NULLIF(COUNT(DISTINCT es.id), 0), 1
    )                                                               AS open_rate_pct,
    ROUND(
        100.0 * COUNT(DISTINCT ec.tracking_id) / NULLIF(COUNT(DISTINCT es.id), 0), 1
    )                                                               AS click_rate_pct
FROM email_sends es
LEFT JOIN email_opens  eo ON eo.tracking_id = es.tracking_id
LEFT JOIN email_clicks ec ON ec.tracking_id = es.tracking_id
LEFT JOIN conversions   c ON c.tracking_id  = es.tracking_id
WHERE es.status IN ('sent', 'bounced')
GROUP BY es.campaign_id, es.county_name;

COMMENT ON TABLE email_sends   IS 'One row per email sent to a lead';
COMMENT ON TABLE email_opens   IS 'Pixel-based open tracking events';
COMMENT ON TABLE email_clicks  IS 'Link click tracking events';
COMMENT ON TABLE conversions   IS 'Paid tax review conversions at $399';
COMMENT ON TABLE daily_snapshots IS 'Morning pipeline snapshot for trend reporting';
