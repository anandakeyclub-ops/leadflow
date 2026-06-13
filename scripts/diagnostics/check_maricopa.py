from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time, json

opts = Options()
opts.add_argument('--window-size=1920,1080')
opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
driver = webdriver.Chrome(options=opts)

url = ('https://recorder.maricopa.gov/recording/document-search-results.html'
       '?documentTypeSelector=code&documentCode=FL&beginDate=2025-01-01&endDate=2026-06-01')
driver.get(url)
time.sleep(8)

# Get all clickable elements on results page
result = driver.execute_script("""
    var els = [];
    document.querySelectorAll('a, button, td, [onclick], [role=button]').forEach(el => {
        var txt = el.innerText || el.textContent || '';
        if(txt.match(/202[0-9]{8}/)) {
            els.push({
                tag: el.tagName,
                text: txt.trim().substring(0,30),
                class: el.className.substring(0,40),
                onclick: el.getAttribute('onclick') || '',
                href: el.getAttribute('href') || ''
            });
        }
    });
    return els.slice(0,10);
""")
print(f'Clickable recording number elements: {len(result)}')
for el in result:
    print(f'  {el}')

# Also check the full HTML of first result row
rows = driver.execute_script("""
    var rows = document.querySelectorAll('tr');
    var result = [];
    for(var r of rows) {
        if(r.innerText.match(/202[0-9]{8}/)) {
            result.push(r.innerHTML.substring(0,300));
            if(result.length >= 3) break;
        }
    }
    return result;
""")
print(f'\nFirst 3 result rows HTML:')
for r in rows:
    print(f'  {r}')

driver.save_screenshot('data/arizona/results_check.png')
driver.quit()