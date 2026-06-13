import os, re

files = {
    'arizona': 'lib/seo-data/arizona-locations.ts',
    'california': 'lib/seo-data/california-locations.ts',
    'texas': 'lib/seo-data/texas-locations.ts',
    'georgia': 'lib/seo-data/georgia-locations.ts',
    'newyork': 'lib/seo-data/newyork-locations.ts',
    'northcarolina': 'lib/seo-data/northcarolina-locations.ts',
    'florida': 'lib/seo-data/florida-locations.ts',
}

for state, path in files.items():
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            c = f.read()
        slugs = re.findall(r"slug:\s*['\"]([^'\"]+)['\"]", c)
        print(f"\n{state}:")
        print(f"  All slugs: {slugs[:10]}")
    else:
        print(f"\n{state}: FILE NOT FOUND at {path}")