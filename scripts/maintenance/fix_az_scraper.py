# Final update to arizona_roc_scraper.py
# Replaces scrape_county with city-based scraping using proven Aura API capture

NEW_CITIES = {
    "Maricopa": ["Phoenix", "Scottsdale", "Mesa", "Tempe", "Chandler",
                 "Gilbert", "Glendale", "Peoria", "Surprise", "Avondale",
                 "Goodyear", "Buckeye", "Anthem", "Sun City", "Tolleson"],
    "Pima":     ["Tucson", "Marana", "Sahuarita", "Oro Valley", "South Tucson"],
    "Pinal":    ["Casa Grande", "Apache Junction", "Maricopa", "Coolidge", "Florence"],
    "Yavapai":  ["Prescott", "Cottonwood", "Sedona", "Prescott Valley", "Chino Valley"],
    "Mohave":   ["Kingman", "Bullhead City", "Lake Havasu City", "Fort Mohave"],
    "Yuma":     ["Yuma", "San Luis", "Somerton", "Wellton"],
    "Cochise":  ["Sierra Vista", "Douglas", "Bisbee", "Willcox"],
    "Navajo":   ["Show Low", "Winslow", "Holbrook", "Pinetop"],
}

print("Cities to scrape:")
total = 0
for county, cities in NEW_CITIES.items():
    print(f"  {county}: {len(cities)} cities")
    total += len(cities)
print(f"Total: {total} city searches")
print()
print("Update arizona_roc_scraper.py with this city list")
print("Each city returns ~250-300 records")
print(f"Estimated total records: {total * 275:,}")