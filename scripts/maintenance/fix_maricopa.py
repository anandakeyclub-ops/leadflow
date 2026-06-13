f = open('maricopa_lien_scraper.py', 'r', encoding='utf-8')
c = f.read()
f.close()

old = '''        # Set date range
        driver.find_element(By.NAME, 'beginDate').send_keys(date_from)
        driver.find_element(By.NAME, 'endDate').send_keys(date_to)
        time.sleep(1)

        # Click search button
        buttons = driver.find_elements(By.TAG_NAME, 'button')
        for btn in buttons:
            if 'search' in btn.text.lower() or btn.get_attribute('type') == 'submit':
                btn.click()
                print('  Clicked Search')
                break'''

new = '''        # Set date range via JavaScript
        driver.execute_script("""
            var begins = document.querySelectorAll('input[name=beginDate]');
            var ends = document.querySelectorAll('input[name=endDate]');
            begins.forEach(b => { b.value = arguments[0]; b.dispatchEvent(new Event('change',{bubbles:true})); });
            ends.forEach(e => { e.value = arguments[1]; e.dispatchEvent(new Event('change',{bubbles:true})); });
        """, date_from, date_to)
        print(f'  Set dates: {date_from} to {date_to}')
        time.sleep(2)

        # Click search via JavaScript
        driver.execute_script("""
            var buttons = document.querySelectorAll('button[type=submit], button.search-btn, input[type=submit]');
            if(buttons.length > 0) { buttons[0].click(); return; }
            var allBtns = document.querySelectorAll('button');
            for(var b of allBtns) {
                if(b.offsetParent !== null && b.innerText.toLowerCase().includes('search')) {
                    b.click(); return;
                }
            }
        """)
        print('  Clicked Search via JS')'''

if old in c:
    c = c.replace(old, new)
    print('Fixed')
else:
    print('Pattern not found')

f = open('maricopa_lien_scraper.py', 'w', encoding='utf-8')
f.write(c)
f.close()