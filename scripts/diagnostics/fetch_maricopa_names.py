# fetch_maricopa_names.py v3
# Search recording number -> click result -> extract name from popup

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time, re, csv
from pathlib import Path

INPUT_CSV  = Path('data/arizona/maricopa_liens_2026-06-01.csv')
OUTPUT_CSV = Path('data/arizona/maricopa_liens_with_names.csv')
SEARCH_URL = 'https://recorder.maricopa.gov/recording/document-search.html'

def get_driver():
    opts = uc.ChromeOptions()
    opts.add_argument('--window-size=1920,1080')
    driver = uc.Chrome(options=opts)
    return driver

with open(INPUT_CSV, encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
print(f'Total: {len(rows)}')

done = {}
if OUTPUT_CSV.exists():
    with open(OUTPUT_CSV, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            name = r.get('debtor_name','').strip()
            rec  = r.get('recording_number','').strip()
            # Only count as done if name is different from recording number
            if name and name != rec and not re.match(r'^\d{10,}$', name):
                done[rec] = name

print(f'Already have real names: {len(done)}')
todo = [r for r in rows if not done.get(r['recording_number'],'')]
print(f'To fetch: {len(todo)}')

if not todo:
    print('All done!')
    exit()

driver = get_driver()
fetched = errors = 0

def save_progress():
    all_rows = []
    for r in rows:
        r2 = dict(r)
        r2['debtor_name'] = done.get(r['recording_number'], '')
        all_rows.append(r2)
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['recording_number','filing_date','document_type','county','debtor_name'])
        w.writeheader()
        w.writerows(all_rows)

try:
    driver.get(SEARCH_URL)
    time.sleep(6)

    for i, row in enumerate(todo):
        rec_num = row['recording_number']
        try:
            # Fill recording number and search
            driver.execute_script("""
                var inputs = document.querySelectorAll('input[name="recordingNumber"]');
                if(inputs.length) {
                    inputs[0].value = arguments[0];
                    inputs[0].dispatchEvent(new Event('input',{bubbles:true}));
                }
            """, rec_num)
            time.sleep(0.5)

            # Click first visible submit button
            driver.execute_script("""
                var btns = document.querySelectorAll('button[type=submit]');
                for(var b of btns) {
                    if(b.offsetParent !== null) { b.click(); break; }
                }
            """)
            time.sleep(4)

            body = driver.find_element(By.TAG_NAME, 'body').text

            # Check for CAPTCHA
            if 'captcha' in body.lower():
                print(f'\nCAPTCHA - solve then press Enter')
                input()

            # Now find and click the result row to open popup
            clicked = driver.execute_script("""
                var cells = document.querySelectorAll('td');
                for(var td of cells) {
                    if(td.innerText.trim() === arguments[0]) {
                        td.click();
                        return 'clicked td';
                    }
                }
                var btns = document.querySelectorAll('button');
                for(var btn of btns) {
                    if(btn.innerText.includes(arguments[0])) {
                        btn.click();
                        return 'clicked btn';
                    }
                }
                return 'not found';
            """, rec_num)
            time.sleep(2)

            # Read popup/updated body
            body2 = driver.find_element(By.TAG_NAME, 'body').text

            # Extract debtor name - appears before INTERNAL REVENUE SERVICE
            name = ''
            m = re.search(
                r'NAME\(S\)\s*([\w\s,\.]+?)\s*INTERNAL REVENUE SERVICE',
                body2, re.IGNORECASE
            )
            if m:
                name = re.sub(r'\s+', ' ', m.group(1)).strip()

            # Validate - must not be a number or empty
            if name and not re.match(r'^\d+$', name) and len(name) > 3:
                done[rec_num] = name
                fetched += 1
            else:
                # Screenshot first failure for debugging
                if errors == 0:
                    driver.save_screenshot('data/arizona/name_debug.png')
                    with open('data/arizona/name_debug.txt', 'w') as dbg:
                        dbg.write(f'rec={rec_num}\nclicked={clicked}\nbody2=\n{body2[:2000]}')
                errors += 1

            # Close popup if open
            driver.execute_script("""
                var closes = document.querySelectorAll('[aria-label="Close"],[data-dismiss],button.close');
                if(closes.length) closes[0].click();
            """)
            time.sleep(0.5)

            # Go back to search
            driver.get(SEARCH_URL)
            time.sleep(3)

        except Exception as e:
            errors += 1
            driver.get(SEARCH_URL)
            time.sleep(3)

        if (i+1) % 10 == 0:
            real_names = [v for v in done.values() if not re.match(r'^\d+$', v)]
            sample = real_names[-1] if real_names else 'none yet'
            print(f'  [{i+1}/{len(todo)}] fetched={fetched} errors={errors} sample="{sample}"')
            save_progress()

finally:
    driver.quit()

save_progress()
named = sum(1 for r in rows if done.get(r['recording_number'],''))
print(f'\nDone: {named}/{len(rows)} real names fetched')
print(f'Check debug: data/arizona/name_debug.txt')