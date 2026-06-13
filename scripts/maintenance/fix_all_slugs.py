import re

files = {
    'lib/seo-data/arizona-locations.ts': ['maricopa','pima','pinal','yavapai','mohave','coconino','yuma','cochise','navajo','apache'],
    'lib/seo-data/california-locations.ts': ['los-angeles','orange','san-diego','riverside','san-bernardino','sacramento','alameda','santa-clara','fresno','ventura','contra-costa','kern'],
    'lib/seo-data/texas-locations.ts': ['harris','dallas','tarrant','bexar','travis','collin','denton','fort-bend','hidalgo','el-paso','williamson','nueces','montgomery','cameron','galveston'],
    'lib/seo-data/newyork-locations.ts': ['kings','queens','new-york','suffolk','nassau','westchester','erie','monroe','bronx','richmond','onondaga','albany'],
    'lib/seo-data/northcarolina-locations.ts': ['mecklenburg','wake','guilford','forsyth','durham','buncombe','union','johnston','new-hanover','cabarrus','iredell','gaston'],
}

for path, counties in files.items():
    try:
        with open(path, 'r', encoding='utf-8') as f:
            c = f.read()
        count = 0
        for name in counties:
            old = f'slug: "{name}-county"'
            new = f'slug: "{name}"'
            if old in c:
                c = c.replace(old, new)
                count += 1
        with open(path, 'w', encoding='utf-8') as f:
            f.write(c)
        print(f"{path}: fixed {count} slugs")
    except FileNotFoundError:
        print(f"{path}: NOT FOUND")