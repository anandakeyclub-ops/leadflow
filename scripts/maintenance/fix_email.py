f = open('scripts/reports/monthly_state_report.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '''        "landing":       "/north-carolina",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
}'''

new = '''        "landing":       "/north-carolina",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "illinois": {
        "name":          "Illinois",
        "abbreviation":  "IL",
        "has_db_data":   False,
        "top_counties":  ["Cook (Chicago)", "DuPage", "Lake",
                          "Will (Joliet)", "Kane", "Winnebago (Rockford)",
                          "Peoria", "Champaign"],
        "key_industries": ["construction contractors",
                           "manufacturing workers",
                           "trucking and logistics operators",
                           "restaurant and hospitality owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/illinois",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "ohio": {
        "name":          "Ohio",
        "abbreviation":  "OH",
        "has_db_data":   False,
        "top_counties":  ["Cuyahoga (Cleveland)", "Franklin (Columbus)",
                          "Hamilton (Cincinnati)", "Summit (Akron)",
                          "Montgomery (Dayton)", "Lucas (Toledo)",
                          "Stark (Canton)", "Lorain"],
        "key_industries": ["manufacturing and auto industry workers",
                           "construction contractors",
                           "trucking operators",
                           "small business owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/ohio",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
    "pennsylvania": {
        "name":          "Pennsylvania",
        "abbreviation":  "PA",
        "has_db_data":   False,
        "top_counties":  ["Philadelphia", "Allegheny (Pittsburgh)",
                          "Montgomery", "Bucks", "Delaware",
                          "Lancaster", "York", "Lehigh (Allentown)"],
        "key_industries": ["construction contractors",
                           "trucking and logistics operators",
                           "manufacturing workers",
                           "restaurant and hospitality owners"],
        "notice_focus":  "CP14, CP503, CP504",
        "landing":       "/pennsylvania",
        "data_note":     "Based on national IRS enforcement trends and public data.",
    },
}'''

if old in c:
    c = c.replace(old, new)
    print('Fixed: Added IL OH PA to STATES dict')
else:
    print('Pattern not found')

# Update help text
c = c.replace('7 states', '10 states')
c = c.replace('Generate all 7 states', 'Generate all 10 states')

f = open('scripts/reports/monthly_state_report.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('Done')