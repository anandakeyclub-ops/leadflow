f = open('scripts/scrapers/arizona_roc_scraper.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '    "Maricopa": [\n        "Phoenix", "Scottsdale", "Mesa", "Tempe", "Chandler",\n        "Gilbert", "Glendale", "Peoria", "Surprise", "Avondale",\n        "Goodyear", "Buckeye", "Anthem", "Sun City", "Tolleson",\n    ],'

new = '    "Maricopa": [\n        "Phoenix", "Scottsdale", "Mesa", "Tempe", "Chandler",\n        "Gilbert", "Glendale", "Peoria", "Surprise", "Avondale",\n        "Goodyear", "Buckeye", "Anthem", "Sun City West", "Tolleson",\n        "Queen Creek", "Laveen", "Litchfield Park", "El Mirage", "Cave Creek",\n    ],'

if old in c:
    c = c.replace(old, new)
    print("Updated Maricopa cities")
else:
    print("Pattern not found")

f = open('scripts/scrapers/arizona_roc_scraper.py', 'w', encoding='utf-8')
f.write(c)
f.close()
