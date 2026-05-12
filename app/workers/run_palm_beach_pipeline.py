from app.workers.import_palm_beach_weekly import main as import_palm_beach_weekly
from app.workers.scrape_palm_beach_liens import main as scrape_palm_beach_liens
from app.workers.match_and_score import main as match_and_score
from app.workers.enrich_palm_beach_from_dbpr import main as enrich_palm_beach_from_dbpr
from app.workers.generate_email_list import main as generate_email_list
from app.workers.send_email_campaign import main as send_email_campaign


def main():
    print("[1/6] Importing Palm Beach weekly permits...")
    import_palm_beach_weekly()

    print("[2/6] Scraping Palm Beach liens and downloading PDFs...")
    scrape_palm_beach_liens(days_back=90)

    print("[3/6] Matching and scoring leads...")
    match_and_score()

    print("[4/6] Enriching Palm Beach leads from DBPR...")
    enrich_palm_beach_from_dbpr()

    print("[5/6] Generating email list...")
    generate_email_list()

    print("[6/6] Sending email campaign...")
    send_email_campaign()

    print("Palm Beach pipeline complete.")


if __name__ == "__main__":
    main()