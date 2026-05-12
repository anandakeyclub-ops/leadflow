-- Run this once in pgAdmin before the Broward permit scraper.

INSERT INTO counties (county_name, state, active)
VALUES ('Broward', 'FL', true)
ON CONFLICT (county_name) DO UPDATE SET active = true;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_permits_county_record
ON raw_permits (county_id, source_record_id)
WHERE source_record_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_normalized_permits_county_permit_number
ON normalized_permits (county_id, permit_number)
WHERE permit_number IS NOT NULL;

-- Optional but useful if your schema already accepts this column.
-- Leave commented if your normalized_permits table does not have valuation.
-- ALTER TABLE normalized_permits ADD COLUMN IF NOT EXISTS valuation NUMERIC(12,2);
