from fastapi import APIRouter

from app.core.db import get_connection

router = APIRouter()


@router.get("/summary")
def broward_summary():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM counties WHERE county_name = %s", ("Broward",))
            row = cur.fetchone()
            if not row:
                return {
                    "county": "Broward",
                    "raw_liens": 0,
                    "normalized_liens": 0,
                    "permits": 0,
                    "matched_leads": 0,
                    "enriched_contacts": 0,
                    "email_ready_leads": 0,
                }

            county_id = row[0]

            cur.execute("SELECT COUNT(*) FROM raw_liens WHERE county_id = %s", (county_id,))
            raw_liens = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM normalized_liens WHERE county_id = %s", (county_id,))
            normalized_liens = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM normalized_permits WHERE county_id = %s", (county_id,))
            permits = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM matched_leads WHERE county_id = %s", (county_id,))
            matched_leads = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM matched_leads ml
                JOIN contacts ct ON ct.lead_id = ml.id
                WHERE ml.county_id = %s
                  AND ct.email IS NOT NULL
                  AND ct.email <> ''
                """,
                (county_id,),
            )
            enriched_contacts = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM matched_leads ml
                JOIN contacts ct ON ct.lead_id = ml.id
                WHERE ml.county_id = %s
                  AND ct.email IS NOT NULL
                  AND ct.email <> ''
                  AND ml.lead_score >= 60
                  AND ml.lead_status NOT IN ('replied', 'booked', 'closed', 'do_not_contact')
                """,
                (county_id,),
            )
            email_ready_leads = cur.fetchone()[0]

            return {
                "county": "Broward",
                "raw_liens": raw_liens,
                "normalized_liens": normalized_liens,
                "permits": permits,
                "matched_leads": matched_leads,
                "enriched_contacts": enriched_contacts,
                "email_ready_leads": email_ready_leads,
            }
    finally:
        conn.close()


@router.get("/recent-leads")
def broward_recent_leads(limit: int = 25):
    limit = max(1, min(limit, 100))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ml.id,
                    ml.lead_score,
                    ml.match_confidence,
                    ml.lead_status,
                    COALESCE(ct.full_name, np.owner_name, nl.debtor_name, '') AS name,
                    COALESCE(ct.email, '') AS email,
                    COALESCE(np.address_1, nl.address_1, '') AS address,
                    COALESCE(np.project_description, np.permit_type, '') AS permit_context,
                    COALESCE(nl.filing_type, '') AS filing_type,
                    COALESCE(nl.amount, 0) AS lien_amount,
                    COALESCE(nl.filed_date::text, '') AS lien_date,
                    COALESCE(np.issued_date::text, '') AS permit_date
                FROM matched_leads ml
                JOIN counties c ON c.id = ml.county_id
                LEFT JOIN contacts ct ON ct.lead_id = ml.id
                LEFT JOIN normalized_permits np ON np.id = ml.permit_id
                LEFT JOIN normalized_liens nl ON nl.id = ml.lien_id
                WHERE c.county_name = 'Broward'
                ORDER BY ml.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

            return {
                "county": "Broward",
                "count": len(rows),
                "leads": [
                    {
                        "lead_id": r[0],
                        "lead_score": r[1],
                        "match_confidence": r[2],
                        "lead_status": r[3],
                        "name": r[4],
                        "email": r[5],
                        "address": r[6],
                        "permit_context": r[7],
                        "filing_type": r[8],
                        "lien_amount": float(r[9] or 0),
                        "lien_date": r[10],
                        "permit_date": r[11],
                    }
                    for r in rows
                ],
            }
    finally:
        conn.close()
