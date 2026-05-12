from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.db import get_connection


def check_db():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.close()
        conn.close()
        print("DB connection OK")
        return True
    except Exception as e:
        print(f"DB connection failed: {e}")
        return False


def check_tables():
    required = [
        "counties", "raw_permits", "raw_liens", "normalized_permits", "normalized_liens",
        "matched_leads", "contacts", "outreach_events", "bookings", "landing_submissions"
    ]
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        found = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()
        missing = [t for t in required if t not in found]
        if missing:
            print(f"Missing tables: {missing}")
            return False
        print("Required tables exist")
        return True
    except Exception as e:
        print(f"Table check failed: {e}")
        return False


def check_paths():
    required_paths = [
        PROJECT_ROOT / "data" / "raw" / "palm_beach" / "permits",
        PROJECT_ROOT / "data" / "raw" / "palm_beach" / "liens",
    ]
    ok = True
    for p in required_paths:
        if p.exists():
            print(f"Found: {p}")
        else:
            print(f"Missing: {p}")
            ok = False
    return ok


if __name__ == "__main__":
    db_ok = check_db()
    tables_ok = check_tables() if db_ok else False
    paths_ok = check_paths()
    print("Smoke test passed" if db_ok and tables_ok and paths_ok else "Smoke test failed")
