f = open('fetch_maricopa_names.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '''from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time, re, csv
from pathlib import Path'''

new = '''import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time, re, csv
from pathlib import Path'''

c = c.replace(old, new)

old2 = '''def get_driver():
    opts = Options()
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    return webdriver.Chrome(options=opts)'''

new2 = '''def get_driver():
    opts = uc.ChromeOptions()
    opts.add_argument('--window-size=1920,1080')
    driver = uc.Chrome(options=opts)
    return driver'''

c = c.replace(old2, new2)

f = open('fetch_maricopa_names.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('Done')