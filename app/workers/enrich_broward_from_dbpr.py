"""Broward wrapper for the existing DBPR enrichment pattern."""

from app.workers.enrich_palm_beach_from_dbpr import load_dbpr_rows, choose_best_match, build_placeholder_email
from app.core.db import get_connection


def main():
    dbpr_rows = load_dbpr_rows()
    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ml.id AS lead_id,
                        np.business_name,
                        np.owner_name,
                        np.address_1,
                        ct.id AS contact_id,
                        ct.email AS existing_email
                    FROM matched_leads ml
                    JOIN normalized_permits np ON ml.permit_id = np.id
                    JOIN counties c ON ml.county_id = c.id
                    LEFT JOIN contacts ct ON ml.id = ct.lead_id
                    WHERE c.county_name = 'Broward'
                    ORDER BY ml.id
                    """
                )
                rows = cur.fetchall()
                upserted = 0

                for lead_id, business_name, owner_name, address_1, contact_id, existing_email in rows:
                    match = choose_best_match(
                        dbpr_rows=dbpr_rows,
                        business_name=business_name or "",
                        owner_name=owner_name or "",
                        address_1=address_1 or "",
                    )
                    if not match:
                        continue

                    full_name = business_name or owner_name or "Unknown"
                    email = match["email"] or existing_email or build_placeholder_email(full_name, lead_id)
                    phone = match["phone"] or None

                    cur.execute(
                        """
                        INSERT INTO contacts (
                            lead_id, full_name, primary_phone, secondary_phone, email,
                            mailing_address_1, city, state, zip,
                            enrichment_vendor, enrichment_score, enrichment_status, last_enriched_at
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (lead_id)
                        DO UPDATE SET
                            full_name = EXCLUDED.full_name,
                            primary_phone = COALESCE(EXCLUDED.primary_phone, contacts.primary_phone),
                            email = COALESCE(EXCLUDED.email, contacts.email),
                            mailing_address_1 = COALESCE(EXCLUDED.mailing_address_1, contacts.mailing_address_1),
                            city = COALESCE(EXCLUDED.city, contacts.city),
                            state = COALESCE(EXCLUDED.state, contacts.state),
                            zip = COALESCE(EXCLUDED.zip, contacts.zip),
                            enrichment_vendor = EXCLUDED.enrichment_vendor,
                            enrichment_score = EXCLUDED.enrichment_score,
                            enrichment_status = EXCLUDED.enrichment_status,
                            last_enriched_at = NOW()
                        """,
                        (
                            lead_id,
                            full_name,
                            phone,
                            None,
                            email,
                            match["mailing_address_1"] or address_1 or "",
                            match["city"] or "",
                            match["state"] or "FL",
                            match["zip"] or "",
                            "dbpr_csv",
                            85.0,
                            "matched_dbpr",
                        ),
                    )

                    cur.execute(
                        """
                        UPDATE matched_leads
                        SET enrichment_status = 'matched_dbpr', updated_at = NOW()
                        WHERE id = %s
                        """,
                        (lead_id,),
                    )
                    upserted += 1

        print(f"Broward leads enriched from DBPR: {upserted}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
