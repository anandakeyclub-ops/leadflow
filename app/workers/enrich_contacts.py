import os
import re
from pathlib import Path

from dotenv import load_dotenv

from app.core.db import get_connection


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

ALLOW_PLACEHOLDER_EMAILS = os.getenv("ALLOW_PLACEHOLDER_EMAILS", "false").lower() == "true"
PLACEHOLDER_EMAIL_DOMAIN = os.getenv("PLACEHOLDER_EMAIL_DOMAIN", "example.com").strip()


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", ".", value)
    value = re.sub(r"\.+", ".", value).strip(".")
    return value or "lead"


def build_placeholder_email(name: str, lead_id: int, domain: str) -> str:
    base = slugify(name)
    return f"{base}.{lead_id}@{domain}"


def main() -> None:
    conn = get_connection()
    upserted = 0

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ml.id AS lead_id,
                        COALESCE(np.owner_name, nl.debtor_name, 'Unknown Owner') AS full_name,
                        COALESCE(np.business_name, nl.business_name, 'Unknown Business') AS business_name,
                        COALESCE(np.address_1, nl.address_1, '') AS mailing_address_1,
                        COALESCE(np.city, nl.city, '') AS city,
                        COALESCE(np.state, nl.state, '') AS state,
                        COALESCE(np.zip, nl.zip, '') AS zip,
                        ct.id AS contact_id,
                        ct.email AS existing_email
                    FROM matched_leads ml
                    LEFT JOIN normalized_permits np
                        ON ml.permit_id = np.id
                    LEFT JOIN normalized_liens nl
                        ON ml.lien_id = nl.id
                    LEFT JOIN contacts ct
                        ON ml.id = ct.lead_id
                    WHERE
                        ct.id IS NULL
                        OR ct.email IS NULL
                        OR ct.email = ''
                        OR ct.email LIKE '%@example.com'
                    ORDER BY ml.id
                    """
                )

                rows = cur.fetchall()

                for row in rows:
                    lead_id = row[0]
                    full_name = row[1]
                    business_name = row[2]
                    mailing_address_1 = row[3]
                    city = row[4]
                    state = row[5]
                    zip_code = row[6]
                    existing_email = row[8]

                    email = existing_email
                    enrichment_status = "unresolved"
                    enrichment_vendor = "manual_placeholder"
                    enrichment_score = 10.0 if ALLOW_PLACEHOLDER_EMAILS else 0.0

                    if ALLOW_PLACEHOLDER_EMAILS:
                        email_name = business_name if business_name and business_name != "Unknown Business" else full_name
                        email = build_placeholder_email(email_name, lead_id, PLACEHOLDER_EMAIL_DOMAIN)
                        enrichment_status = "placeholder"

                    cur.execute(
                        """
                        INSERT INTO contacts (
                            lead_id,
                            full_name,
                            primary_phone,
                            secondary_phone,
                            email,
                            mailing_address_1,
                            city,
                            state,
                            zip,
                            enrichment_vendor,
                            enrichment_score,
                            enrichment_status,
                            last_enriched_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (lead_id)
                        DO UPDATE SET
                            full_name = EXCLUDED.full_name,
                            email = EXCLUDED.email,
                            mailing_address_1 = EXCLUDED.mailing_address_1,
                            city = EXCLUDED.city,
                            state = EXCLUDED.state,
                            zip = EXCLUDED.zip,
                            enrichment_vendor = EXCLUDED.enrichment_vendor,
                            enrichment_score = EXCLUDED.enrichment_score,
                            enrichment_status = EXCLUDED.enrichment_status,
                            last_enriched_at = NOW()
                        """,
                        (
                            lead_id,
                            full_name,
                            None,
                            None,
                            email,
                            mailing_address_1,
                            city,
                            state,
                            zip_code,
                            enrichment_vendor,
                            enrichment_score,
                            enrichment_status,
                        ),
                    )

                    cur.execute(
                        """
                        UPDATE matched_leads
                        SET
                            enrichment_status = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (enrichment_status, lead_id),
                    )

                    upserted += 1

        print(f"Contacts upserted: {upserted}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()