import psycopg2

# Part 1 - fix the enrichment script blocklist
f = open('scripts/enrichment/multi_state_email_enrichment.py', 'r', encoding='utf-8')
c = f.read()
f.close()

new_domains = [
    '"birdeye.com"', '"rocketreach.co"', '"bloomberg.com"',
    '"prolicensecheck.com"', '"bidbro.com"', '"blockrenovation.com"',
    '"faisalman.com"', '"bctonline.com"', '"realtor.com"',
    '"paci-inc.com"', '"me.com"',
]

insert_after = '"maps.google.com", "instagram.com", "twitter.com", "tiktok.com",'
addition = "\n    " + ", ".join(new_domains) + ","

if insert_after in c and "birdeye.com" not in c:
    c = c.replace(insert_after, insert_after + addition)
    print("Added to SKIP_DOMAINS")
else:
    print("SKIP_DOMAINS already updated or pattern not found")

insert_after2 = '"manta.com", "yellowpages.com", "whitepages.com",'
addition2 = '\n    "birdeye.com", "rocketreach.co", "bloomberg.com", "prolicensecheck.com", "blockrenovation.com", "realtor.com",'

if insert_after2 in c and "birdeye.com" not in c.split("SKIP_EMAIL_DOMAINS")[1][:200]:
    c = c.replace(insert_after2, insert_after2 + addition2)
    print("Added to SKIP_EMAIL_DOMAINS")

f = open('scripts/enrichment/multi_state_email_enrichment.py', 'w', encoding='utf-8')
f.write(c)
f.close()

# Part 2 - clear bad emails already saved to DB
bad_domains = [
    'birdeye.com', 'rocketreach.co', 'bloomberg.com', 'blockrenovation.com',
    'bidbro.com', 'faisalman.com', 'bctonline.com', 'realtor.com',
    'paci-inc.com', 'ks.gov', 'prolicensecheck.com', 'me.com',
]

conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()
total = 0
for domain in bad_domains:
    cur.execute("UPDATE texas_tdlr_contacts SET email=NULL WHERE email LIKE %s", (f'%@%{domain}%',))
    if cur.rowcount:
        print(f"  Cleared {cur.rowcount} bad emails from {domain}")
        total += cur.rowcount
conn.commit()

cur.execute("SELECT COUNT(*) FROM texas_tdlr_contacts WHERE lien_match=TRUE AND email IS NOT NULL")
print(f"\nClean TX emails remaining: {cur.fetchone()[0]}")
conn.close()
print(f"Total bad emails cleared: {total}")
print("Done")
