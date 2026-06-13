import json, csv
from pathlib import Path

# Parse the r=8 response
with open('data/arizona/responses/aura_r8_115479.json', encoding='utf-8') as f:
    data = json.load(f)

records = []
for action in data.get('actions', []):
    rv = action.get('returnValue', [])
    if not isinstance(rv, list):
        continue
    for item in rv:
        if not isinstance(item, dict) or 'accountName' not in item:
            continue
        addr = item.get('address', '')
        parts = [p.strip() for p in addr.split(',')]
        city  = parts[0] if parts else ''
        state = parts[1] if len(parts) > 1 else 'AZ'
        zipcode = parts[2] if len(parts) > 2 else ''
        owner = ''
        for contact in item.get('accountContactData', []):
            name = contact.get('contactName', '')
            if 'Qualifying Party' in name or 'Member' in name:
                owner = name.split('(')[0].strip()
                break
        for lic in item.get('licenseData', []):
            records.append({
                'license_number': lic.get('licenseNo', '').replace('ROC ', '').strip(),
                'license_class':  lic.get('subType', ''),
                'status':         lic.get('status', 'Active'),
                'business_name':  item.get('accountName', ''),
                'owner_name':     owner,
                'business_city':  city,
                'business_state': 'AZ',
                'business_zip':   zipcode,
                'phone':          item.get('phone', ''),
                'county':         'Maricopa',
                'email':          '',
            })

print(f'Parsed {len(records)} records')
for r in records[:5]:
    print(f"  {r['license_number']} | {r['business_name']} | {r['business_city']} | {r['phone']}")

# Save to CSV
out = Path('data/arizona/az_roc_phoenix_parsed.csv')
fields = ['license_number','license_class','status','business_name',
          'owner_name','business_city','business_state','business_zip',
          'phone','county','email']
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    w.writerows(records)
print(f'Saved: {out}')