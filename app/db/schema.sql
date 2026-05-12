CREATE TABLE IF NOT EXISTS counties (
    id SERIAL PRIMARY KEY,
    county_name VARCHAR(100) NOT NULL UNIQUE,
    state VARCHAR(10) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_permits (
    id SERIAL PRIMARY KEY,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    source_file VARCHAR(255),
    source_record_id VARCHAR(255),
    raw_payload JSONB NOT NULL,
    issued_date DATE,
    scraped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_liens (
    id SERIAL PRIMARY KEY,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    source_file VARCHAR(255),
    source_record_id VARCHAR(255),
    raw_payload JSONB NOT NULL,
    filed_date DATE,
    scraped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS normalized_permits (
    id SERIAL PRIMARY KEY,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    raw_permit_id INTEGER REFERENCES raw_permits(id),
    owner_name VARCHAR(255),
    business_name VARCHAR(255),
    address_1 VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(20),
    zip VARCHAR(20),
    permit_number VARCHAR(100),
    permit_type VARCHAR(100),
    project_description TEXT,
    issued_date DATE,
    trade VARCHAR(100),
    normalized_hash VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS normalized_liens (
    id SERIAL PRIMARY KEY,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    raw_lien_id INTEGER REFERENCES raw_liens(id),
    debtor_name VARCHAR(255),
    business_name VARCHAR(255),
    address_1 VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(20),
    zip VARCHAR(20),
    filing_type VARCHAR(100),
    amount NUMERIC(12,2),
    filed_date DATE,
    normalized_hash VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS matched_leads (
    id SERIAL PRIMARY KEY,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    permit_id INTEGER REFERENCES normalized_permits(id),
    lien_id INTEGER REFERENCES normalized_liens(id),
    match_score NUMERIC(5,2),
    match_confidence VARCHAR(20),
    lead_score INTEGER,
    lead_status VARCHAR(50) NOT NULL DEFAULT 'new',
    enrichment_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_matched_leads UNIQUE (county_id, permit_id, lien_id)
);

CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES matched_leads(id),
    full_name VARCHAR(255),
    primary_phone VARCHAR(50),
    secondary_phone VARCHAR(50),
    email VARCHAR(255),
    mailing_address_1 VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(20),
    zip VARCHAR(20),
    enrichment_vendor VARCHAR(100),
    enrichment_score NUMERIC(5,2),
    enrichment_status VARCHAR(50),
    last_enriched_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_contacts_lead UNIQUE (lead_id)
);

CREATE TABLE IF NOT EXISTS outreach_events (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER REFERENCES matched_leads(id),
    channel VARCHAR(50) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    template_name VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    followup_due_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES matched_leads(id),
    booking_source VARCHAR(50) NOT NULL DEFAULT 'landing_page',
    external_booking_id VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'booked',
    scheduled_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS landing_submissions (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER REFERENCES matched_leads(id),
    email VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    phone VARCHAR(50),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_permits_county_id ON raw_permits(county_id);
CREATE INDEX IF NOT EXISTS idx_raw_liens_county_id ON raw_liens(county_id);
CREATE INDEX IF NOT EXISTS idx_norm_permits_county_id ON normalized_permits(county_id);
CREATE INDEX IF NOT EXISTS idx_norm_liens_county_id ON normalized_liens(county_id);
CREATE INDEX IF NOT EXISTS idx_matched_leads_county_id ON matched_leads(county_id);
CREATE INDEX IF NOT EXISTS idx_contacts_lead_id ON contacts(lead_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_outreach_events_lead_id ON outreach_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_bookings_lead_id ON bookings(lead_id);
CREATE INDEX IF NOT EXISTS idx_landing_submissions_lead_id ON landing_submissions(lead_id);
