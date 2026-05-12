from pathlib import Path
import re

path = Path("app/workers/scrape_broward_permits.py")
text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.pagination_bak")
backup.write_text(text, encoding="utf-8")
print(f"Backup saved: {backup}")

new_func = r'''
def scrape_source(source: Dict, start: date, end: date, visible: bool, limit: int, pages: int, debug_pages: bool = False) -> List[PermitRecord]:
    source_name = source["name"]
    jurisdiction = source.get("jurisdiction") or "Broward"
    driver = make_driver(visible=visible)
    records: List[PermitRecord] = []

    seen_detail_urls = set()
    seen_page_signatures = set()

    try:
        print(f"[source] {source_name}: {start} to {end}")
        open_general_search(driver, source["base_url"])

        if debug_pages:
            save_debug(driver, source_name, "01_search_form_loaded")
            dump_page_inventory(driver, source_name, "01_search_form_loaded")

        set_search_dates(driver, start, end, source_name=source_name)
        maybe_select_record_type(driver, source.get("record_type_contains"))

        if debug_pages:
            save_debug(driver, source_name, "02_search_form_filled")

        click_search(driver)

        if debug_pages:
            save_debug(driver, source_name, "03_after_search_click")
            dump_page_inventory(driver, source_name, "03_after_search_click")

        for page_num in range(1, pages + 1):
            if debug_pages:
                save_debug(driver, source_name, f"results_page_{page_num:03d}")

            links = extract_result_links(driver)
            page_signature = tuple(sorted([url for _, url, _ in links]))

            if page_signature in seen_page_signatures:
                print(f"  page {page_num}: repeated result page detected; stopping pagination")
                break

            seen_page_signatures.add(page_signature)

            new_links = []
            for record_number, detail_url, row_data in links:
                if detail_url not in seen_detail_urls:
                    seen_detail_urls.add(detail_url)
                    new_links.append((record_number, detail_url, row_data))

            print(f"  page {page_num}: {len(links)} link(s), {len(new_links)} new unique link(s)")

            if debug_pages:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                audit_path = DEBUG_DIR / f"{source_name}_result_links_page_{page_num:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                audit_path.write_text(
                    "\n".join([f"{rn}\t{url}" for rn, url, _ in links]),
                    encoding="utf-8",
                    errors="ignore",
                )
                print(f"  [debug] result links: {audit_path}")

            if not links:
                save_debug(driver, source_name, "no_results")
                break

            if not new_links:
                print(f"  page {page_num}: no new links; stopping pagination")
                break

            for record_number, detail_url, row_data in new_links:
                if limit and len(records) >= limit:
                    return records

                row_guess = guess_from_row(record_number, row_data)
                detail_payload = dict(row_data)
                detail_payload["detail_url"] = detail_url

                try:
                    driver.get(detail_url)
                    time.sleep(3)

                    if debug_pages and len(records) < 10:
                        save_debug(driver, source_name, f"detail_{record_number}", include_screenshot=False)

                    detail_payload.update(extract_text_map(driver.page_source))

                except Exception as exc:
                    detail_payload["detail_error"] = str(exc)

                def first_value(*keys):
                    for key in keys:
                        for actual, val in detail_payload.items():
                            if key.lower() in actual.lower() and val:
                                return clean_text(str(val))
                    return None

                rec = PermitRecord(
                    source_name=source_name,
                    jurisdiction=jurisdiction,
                    permit_number=record_number,
                    permit_type=pick_accela_permit_type(detail_payload, row_guess),
                    project_description=pick_accela_description(detail_payload, row_guess),
                    address_1=first_value("address", "Address") or row_guess.get("address_1"),
                    owner_name=first_value("owner", "Owner", "Applicant"),
                    business_name=first_value("contractor", "Licensed Professional", "Contractor", "Business"),
                    issued_date=parse_date(first_value("issue_date", "Issue Date", "Issued Date", "opened_date") or row_guess.get("issued_date")),
                    status=first_value("status", "Status") or row_guess.get("status"),
                    raw_url=detail_url,
                    raw_payload=detail_payload,
                )

                rec = normalize_record(rec)
                if rec:
                    records.append(rec)

                try:
                    driver.back()
                    time.sleep(2)
                except Exception:
                    pass

            if limit and len(records) >= limit:
                break

            if not next_results_page(driver):
                break

    except Exception as exc:
        print(f"  [error] {source_name}: {exc}")
        try:
            save_debug(driver, source_name, "error")
        except Exception:
            pass

    finally:
        driver.quit()

    print(f"  {source_name} unique records collected before import: {len(records)}")
    return records
'''

text = re.sub(
    r"def scrape_source\(source: Dict, start: date, end: date, visible: bool, limit: int, pages: int, debug_pages: bool = False\) -> List\[PermitRecord\]:\n.*?\n\n\ndef get_county_id",
    new_func + "\n\n\ndef get_county_id",
    text,
    flags=re.S
)

path.write_text(text, encoding="utf-8")
print("Patched pagination/link audit logic successfully.")
