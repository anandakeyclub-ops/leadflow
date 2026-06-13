"""
Diagnose TDLR matched contacts - individual vs business breakdown
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.core.db import get_connection
import re

conn = get_connection()
cur = conn.cursor()

# Individual vs business breakdown among matched contacts
cur.execute("""
    SELECT business_name, license_type, business_city, business_county
    FROM texas_tdlr_contacts
    WHERE lien_match = TRUE
      AND (email IS NULL OR email = '')
    ORDER BY id
    LIMIT 200
""")
rows = cur.fetchall()

# Detect if name looks like a person (LASTNAME, FIRSTNAME pattern)
person_pattern = re.compile(r'^[A-Z]+,\s+[A-Z]', re.IGNORECASE)
business_keywords = {'LLC','INC','CORP','LTD','CO.','COMPANY','SERVICES',
                     'GROUP','SOLUTIONS','ENTERPRISES','HOLDINGS','CONSTRUCTION',
                     'ELECTRIC','HVAC','AIR','MECHANICAL','PLUMBING','ROOFING'}

individuals = []
businesses  = []

for biz_name, lic_type, city, county in rows:
    if not biz_name:
        individuals.append((biz_name, lic_type, city, county))
        continue
    name_up = biz_name.upper()
    is_person = (
        bool(person_pattern.match(biz_name)) or
        not any(kw in name_up for kw in business_keywords)
    )
    if bool(person_pattern.match(biz_name)):
        individuals.append((biz_name, lic_type, city, county))
    else:
        businesses.append((biz_name, lic_type, city, county))

print(f"=== Of first 200 matched contacts needing email ===")
print(f"  Individuals (LAST, FIRST pattern): {len(individuals)}")
print(f"  Businesses (company name):         {len(businesses)}")
print()
print("=== Sample BUSINESSES (searchable via SerpAPI) ===")
for b in businesses[:20]:
    print(f"  {b[0][:45]:<45} | {b[1]:<30} | {b[3]}")

print()
print("=== License type breakdown for businesses only ===")
from collections import Counter
biz_trades = Counter(b[1] for b in businesses)
for trade, cnt in biz_trades.most_common(10):
    print(f"  {(trade or 'Unknown'):<40} {cnt:>4}")

# Count all matched contacts that are businesses
cur.execute("""
    SELECT COUNT(id)
    FROM texas_tdlr_contacts
    WHERE lien_match = TRUE
      AND (email IS NULL OR email = '')
      AND business_name ~ '[A-Z]{3,}'
      AND business_name NOT SIMILAR TO '%[A-Z]+, [A-Z]%'
      AND (
          business_name ILIKE '%LLC%' OR
          business_name ILIKE '%INC%' OR
          business_name ILIKE '%CORP%' OR
          business_name ILIKE '%AIR%' OR
          business_name ILIKE '%ELECTRIC%' OR
          business_name ILIKE '%HVAC%' OR
          business_name ILIKE '%CONSTRUCTION%' OR
          business_name ILIKE '%MECHANICAL%' OR
          business_name ILIKE '%SERVICES%' OR
          business_name ILIKE '%PLUMBING%' OR
          business_name ILIKE '%ROOFING%' OR
          business_name ILIKE '%COMPANY%' OR
          business_name ILIKE '%SOLUTIONS%' OR
          business_name ILIKE '%ENTERPRISES%'
      )
""")
biz_count = cur.fetchone()[0]
print(f"\n=== Total matched contacts that are BUSINESSES: {biz_count} ===")
print("(These are the ones worth enriching with SerpAPI)")

conn.close()
