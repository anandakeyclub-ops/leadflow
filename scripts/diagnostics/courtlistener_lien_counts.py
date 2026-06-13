"""
CourtListener PACER API — Federal tax lien count by state
Free API, no auth required for basic queries
Docs: https://www.courtlistener.com/help/api/
"""
import requests
import json
import time
from datetime import datetime

STATES = ['FL', 'TX', 'GA', 'AZ', 'CA', 'NY', 'NC', 'IL', 'OH', 'PA',
          'NV', 'CO', 'MI', 'WA', 'VA']

BASE_URL = "https://www.courtlistener.com/api/rest/v3/"

def get_lien_cases_by_state(state_code, year=None):
    """
    Query CourtListener for federal tax lien cases by state.
    Uses PACER/RECAP data for federal district courts.
    """
    params = {
        'type': 'r',  # RECAP/PACER
        'description': 'tax lien',
        'court__jurisdiction': 'FD',  # Federal District
        'court__in': f'{state_code.lower()}',
        'page_size': 1,  # Just need count
    }
    if year:
        params['date_filed__year'] = year
    
    try:
        resp = requests.get(
            f"{BASE_URL}dockets/",
            params=params,
            headers={'User-Agent': 'TaxCaseReview/1.0 research@taxcasereview.org'},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('count', 0)
    except Exception as e:
        print(f"  Error for {state_code}: {e}")
        return None

def main():
    results = {}
    print(f"CourtListener NFTL Query — {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 50)
    
    for state in STATES:
        print(f"Querying {state}...")
        count_total = get_lien_cases_by_state(state)
        count_2025 = get_lien_cases_by_state(state, 2025)
        count_2024 = get_lien_cases_by_state(state, 2024)
        
        results[state] = {
            'total': count_total,
            'fy2025': count_2025,
            'fy2024': count_2024
        }
        print(f"  {state}: total={count_total}, 2025={count_2025}, 2024={count_2024}")
        time.sleep(1)  # Rate limiting
    
    # Save results
    output_file = f"courtlistener_lien_counts_{datetime.now().strftime('%Y%m%d')}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {output_file}")
    
    # Print summary table
    print("\nState | FY2024 | FY2025 | Change")
    print("-" * 40)
    for state, data in results.items():
        y24 = data['fy2024'] or 0
        y25 = data['fy2025'] or 0
        chg = f"+{y25-y24}" if y25 > y24 else str(y25-y24)
        print(f"{state}   | {y24:6,} | {y25:6,} | {chg}")

if __name__ == '__main__':
    main()