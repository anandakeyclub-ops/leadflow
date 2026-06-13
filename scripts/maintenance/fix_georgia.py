f = open('lib/seo-data/georgia-locations.ts', 'r', encoding='utf-8')
c = f.read()
f.close()
counties = ['fulton','gwinnett','cobb','dekalb','chatham','cherokee','forsyth','henry','hall','richmond','clayton']
for name in counties:
    c = c.replace(f'slug: "{name}-county"', f'slug: "{name}"')
f = open('lib/seo-data/georgia-locations.ts', 'w', encoding='utf-8')
f.write(c)
f.close()
print('done')