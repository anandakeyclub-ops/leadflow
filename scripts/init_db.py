from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.db import get_connection


def load_schema():
    schema_path = PROJECT_ROOT / "app" / "db" / "schema.sql"
    return schema_path.read_text(encoding="utf-8")


def run_schema():
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(load_schema())
    cur.close()
    conn.close()
    print("Schema loaded.")


def seed_county():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO counties (county_name, state, active)
        VALUES (%s, %s, %s)
        ON CONFLICT (county_name) DO UPDATE SET state = EXCLUDED.state
        """,
        ("Palm Beach", "FL", True),
    )
    conn.commit()
    cur.close()
    conn.close()
    print("Palm Beach seeded.")


if __name__ == "__main__":
    run_schema()
    seed_county()
    print("Database initialized successfully.")
