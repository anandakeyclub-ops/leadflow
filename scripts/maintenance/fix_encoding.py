f = open('weekly_scrape.py', 'r', encoding='utf-8')
c = f.read()
f.close()

header = 'import os\nimport sys\nos.environ["PYTHONIOENCODING"] = "utf-8"\nsys.stdout.reconfigure(encoding="utf-8")\n\n'

if 'PYTHONIOENCODING' not in c:
    c = header + c
    f = open('weekly_scrape.py', 'w', encoding='utf-8')
    f.write(c)
    f.close()
    print('Fixed - UTF-8 encoding added')
else:
    print('Already has encoding')