f = open('scripts/scrapers/selenium_tx_scraper.py', 'r', encoding='utf-8')
c = f.read()
f.close()

# Fix 1: Save file_number to filing_number column correctly
old = """INSERT INTO texas_liens (
                            filing_number, debtor_name, grantor_name, grantee_name,
                            filing_type, filing_date, county, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""

new = """INSERT INTO texas_liens (
                            filing_number, debtor_name, grantor_name, grantee_name,
                            filing_type, filing_date, county, source, instrument_type
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""

if old in c:
    c = c.replace(old, new)
    print('Fixed INSERT columns')
else:
    print('INSERT pattern not found')

with open('scripts/scrapers/selenium_tx_scraper.py', 'w', encoding='utf-8') as f:
    f.write(c)
print('Done')