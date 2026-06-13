-- ============================================================================
-- TaxCase Review — Centralized Data Engine schema
-- ----------------------------------------------------------------------------
-- normalized_contacts is the unified staging table for every state's license
-- holders + lien matches + enriched emails.
--
-- It is a NEW table. It NEVER replaces or alters the production tables that the
-- email sequence depends on (normalized_liens, lien_dbpr_contacts, email_sends,
-- email_opens, email_clicks). The data engine writes here, then
-- sync_to_email_pipeline() copies email-ready, lien-matched rows into
-- lien_dbpr_contacts so they flow through the existing 7-touch sequence.
-- ============================================================================

CREATE TABLE IF NOT EXISTS normalized_contacts (
    id                  SERIAL PRIMARY KEY,
    state               VARCHAR(2)   NOT NULL,
    state_name          VARCHAR(50),
    county              VARCHAR(100),
    license_number      VARCHAR(100),
    license_type        VARCHAR(100),
    license_status      VARCHAR(50),
    license_source      VARCHAR(50),
    owner_name          VARCHAR(200),
    business_name       VARCHAR(200),
    business_address    VARCHAR(300),
    business_city       VARCHAR(100),
    business_zip        VARCHAR(20),
    phone               VARCHAR(30),
    email               VARCHAR(200),
    has_lien_match      BOOLEAN      DEFAULT FALSE,
    lien_id             INTEGER      REFERENCES normalized_liens(id),
    lien_amount         NUMERIC,
    lien_filed_date     DATE,
    lien_county         VARCHAR(100),
    match_score         INTEGER,
    match_method        VARCHAR(50),
    email_confidence    VARCHAR(20)  DEFAULT 'low',
    email_source        VARCHAR(50),
    email_verified      BOOLEAN      DEFAULT FALSE,
    campaign_id         VARCHAR(100) DEFAULT 'lien_outreach_2026',
    email_step          INTEGER      DEFAULT 0,
    last_emailed_at     TIMESTAMP,
    replied             BOOLEAN      DEFAULT FALSE,
    unsubscribed        BOOLEAN      DEFAULT FALSE,
    is_spam_trap        BOOLEAN      DEFAULT FALSE,
    data_source         VARCHAR(100),
    created_at          TIMESTAMP    DEFAULT NOW(),
    updated_at          TIMESTAMP    DEFAULT NOW(),
    UNIQUE (state, license_number)
);

CREATE INDEX IF NOT EXISTS idx_nc_state      ON normalized_contacts(state);
CREATE INDEX IF NOT EXISTS idx_nc_county     ON normalized_contacts(county);
CREATE INDEX IF NOT EXISTS idx_nc_email      ON normalized_contacts(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_nc_lien_match ON normalized_contacts(has_lien_match) WHERE has_lien_match = TRUE;
CREATE INDEX IF NOT EXISTS idx_nc_pipeline   ON normalized_contacts(email_step, state, campaign_id);
CREATE INDEX IF NOT EXISTS idx_nc_lien_id    ON normalized_contacts(lien_id);
