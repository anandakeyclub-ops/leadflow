f = open('scripts/scrapers/selenium_tx_scraper.py', 'r', encoding='utf-8')
c = f.read()
f.close()

# Fix build_url to use offset/limit pagination
old = '''    if page > 1:
        url += f"&page={page}"
    return url'''

new = '''    limit = 250
    offset = (page - 1) * limit
    url += f"&limit={limit}&offset={offset}"
    return url'''

c = c.replace(old, new)
f = open('scripts/scrapers/selenium_tx_scraper.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('done')