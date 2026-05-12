"""
scrape_miami_dade_permits.py
============================
Miami-Dade County building permits via their Open Data ArcGIS REST API.
No browser, no CAPTCHA, no auth required — pure REST.

Source: Miami-Dade Open Data Hub — Building Permit dataset
  https://gis-mdc.opendata.arcgis.com/datasets/MDC::building-permit/about
  Last 3 years of county permits, updated regularly.

API endpoint:
  https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/
  BuildingPermit_public_gdb/FeatureServer/0/query

Usage:
  python -m app.workers.scrape_miami_dade_permits --days-back 90
  python -m app.workers.scrape_miami_dade_permits --days-back 30
  python -m app.workers.scrape_miami_dade_permits --no-db (test)
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from app.core.db import get_connection
except ImportError:
    get_connection = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COUNTY_NAME = "Miami-Dade"
SOURCE_NAME = "miami_dade_arcgis"

# Miami-Dade Open Data ArcGIS REST endpoint
# Try multiple known endpoint patterns
# Confirmed working endpoints (verified 2026-04-28)
# Primary: MD_LandInformation layer 1 = County Building Permits
# Fields: ADDRESS, CONTRNAME, TYPE, DESC1-10, ISSUDATE (epoch ms), PROCNUM, FOLIO, BPSTATUS
ARCGIS_ENDPOINTS = [
    "https://gisweb.miamidade.gov/arcgis/rest/services/MD_LandInformation/MapServer/1/query",
]

# Secondary: WASD Permits (unincorporated only, layer 1)
# Fields: BLDPRMNO, BLDPRMTYP, BLDPRMIDT (epoch ms), PROJDESC, PROJZIP, BLDPRMSTAT
WASD_ENDPOINT = "https://gisweb.miamidade.gov/arcgis/rest/services/Wasd/Permits_4_v1/MapServer/1/query"


BASE_DIR  = Path(__file__).resolve().parents[2]
RAW_DIR   = BASE_DIR / "data" / "raw" / "miami_dade" / "permits"
RAW_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (leadflow pipeline)"})
_retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
SESSION.mount("https://", HTTPAdapter(max_retries=_retry))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PermitRecord:
    permit_number:       str
    owner_name:          Optional[str]  = None
    business_name:       Optional[str]  = None
    address_1:           Optional[str]  = None
    city:                Optional[str]  = None
    state:               str            = "FL"
    zip:                 Optional[str]  = None
    permit_type:         Optional[str]  = None
    project_description: Optional[str]  = None
    issued_date:         Optional[date] = None
    status:              Optional[str]  = None
    project_value:       Optional[float] = None
    raw_payload:         Dict            = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()

def parse_epoch_ms(v: Any) -> Optional[date]:
    """Convert ArcGIS epoch milliseconds to date."""
    if not v:
        return None
    try:
        return datetime.fromtimestamp(int(v) / 1000).date()
    except Exception:
        return None

def parse_date(v: Any) -> Optional[date]:
    s = clean(v)
    # Try epoch ms first
    if re.match(r"^\d{10,13}$", s):
        return parse_epoch_ms(int(s))
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def parse_money(v: Any) -> Optional[float]:
    s = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# API query — discover working endpoint and field names
# ---------------------------------------------------------------------------

def probe_endpoint(url: str) -> Optional[dict]:
    """Check if an ArcGIS endpoint is alive."""
    try:
        resp = SESSION.get(url, params={"f": "json", "where": "1=1", "resultRecordCount": 1, "outFields": "*"}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if "features" in data:
                return data
    except Exception:
        pass
    return None


def query_permits(url: str, where: str, offset: int = 0, count: int = 1000) -> list:
    """Query ArcGIS FeatureServer for permit records."""
    params = {
        "where":             where,
        "outFields":         "*",
        "f":                 "json",
        "resultOffset":      offset,
        "resultRecordCount": count,
        "orderByFields":     "OBJECTID DESC",
    }
    try:
        resp = SESSION.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("features", [])
    except Exception as e:
        print(f"  [API] Query error at offset {offset}: {e}")
        return []


def attrs_to_permit(attrs: dict, source: str = "mdi") -> Optional[PermitRecord]:
    """
    Map ArcGIS feature attributes to PermitRecord.
    
    MD_LandInformation/1 fields (confirmed):
      PROCNUM, ADDRESS, TYPE, DESC1-DESC10, ISSUDATE (epoch ms),
      CONTRNAME, BPSTATUS, FOLIO, RESCOMM, FFRMLINE
    
    WASD/Permits_4_v1/1 fields (confirmed):
      BLDPRMNO, BLDPRMTYP, BLDPRMIDT (epoch ms), PROJDESC, PROJZIP, BLDPRMSTAT
    """
    if source == "wasd":
        permit_num = clean(str(attrs.get("BLDPRMNO") or ""))
        if not permit_num:
            return None
        desc = clean(str(attrs.get("PROJDESC") or ""))
        ptype = clean(str(attrs.get("BLDPRMTYP") or ""))
        issued_raw = attrs.get("BLDPRMIDT")
        issued_date = parse_epoch_ms(issued_raw) if issued_raw else None
        return PermitRecord(
            permit_number       = permit_num,
            address_1           = None,
            city                = "Miami-Dade Unincorporated",
            state               = "FL",
            zip                 = clean(str(attrs.get("PROJZIP") or "")),
            permit_type         = ptype,
            project_description = desc,
            issued_date         = issued_date,
            status              = clean(str(attrs.get("BLDPRMSTAT") or "")),
            raw_payload         = {k: v for k, v in attrs.items() if v is not None},
        )
    else:
        # MD_LandInformation layer 1 — County Building Permits
        permit_num = clean(str(attrs.get("PROCNUM") or attrs.get("ID") or ""))
        if not permit_num:
            return None

        # Combine DESC fields for full description
        descs = [clean(str(attrs.get(f"DESC{i}") or "")) for i in range(1, 11)]
        description = " | ".join(d for d in descs if d) or clean(str(attrs.get("FFRMLINE") or ""))

        # ISSUDATE is epoch ms
        issued_raw = attrs.get("ISSUDATE")
        issued_date = parse_epoch_ms(issued_raw) if issued_raw else None

        return PermitRecord(
            permit_number       = permit_num,
            owner_name          = None,  # not in this layer
            business_name       = clean(str(attrs.get("CONTRNAME") or "")),
            address_1           = clean(str(attrs.get("ADDRESS") or "")),
            city                = "Miami-Dade",
            state               = "FL",
            zip                 = None,
            permit_type         = clean(str(attrs.get("TYPE") or "")),
            project_description = description,
            issued_date         = issued_date,
            status              = clean(str(attrs.get("BPSTATUS") or "")),
            raw_payload         = {k: v for k, v in attrs.items() if v is not None},
        )


# ---------------------------------------------------------------------------
# Main scrape function
# ---------------------------------------------------------------------------

def fetch_layer(url: str, where: str, source: str = "mdi") -> List[PermitRecord]:
    """Paginate through an ArcGIS layer and return PermitRecord list."""
    records = []
    seen: set = set()
    offset = 0
    batch = 1000
    while True:
        features = query_permits(url, where, offset=offset, count=batch)
        if not features:
            break
        for feat in features:
            attrs = feat.get("attributes", {})
            rec = attrs_to_permit(attrs, source=source)
            if rec and rec.permit_number not in seen:
                seen.add(rec.permit_number)
                records.append(rec)
        print(f"  offset={offset} fetched={len(features)} total={len(records)}")
        if len(features) < batch:
            break
        offset += batch
        time.sleep(0.3)
    return records


def scrape_miami_dade(start: date, end: date) -> List[PermitRecord]:
    """Fetch Miami-Dade permits from confirmed ArcGIS endpoints."""
    print(f"\n[Miami-Dade] Scraping {start} → {end}")
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")
    all_records: List[PermitRecord] = []

    # Source 1: MD_LandInformation — County Building Permits
    # Date field: ISSUDATE (epoch ms) — use DATE filter
    print(f"\n  [MDI] County Building Permits (MD_LandInformation)...")
    mdi_url   = ARCGIS_ENDPOINTS[0]
    mdi_where = f"ISSUDATE >= timestamp '{start_str} 00:00:00' AND ISSUDATE <= timestamp '{end_str} 23:59:59'"
    mdi_recs  = fetch_layer(mdi_url, mdi_where, source="mdi")
    print(f"  [MDI] {len(mdi_recs)} permits")
    all_records.extend(mdi_recs)

    # Source 2: WASD Unincorporated permits
    # Date field: BLDPRMIDT (epoch ms)
    print(f"\n  [WASD] Unincorporated permits...")
    wasd_where = f"BLDPRMIDT >= timestamp '{start_str} 00:00:00' AND BLDPRMIDT <= timestamp '{end_str} 23:59:59'"
    wasd_recs  = fetch_layer(WASD_ENDPOINT, wasd_where, source="wasd")
    print(f"  [WASD] {len(wasd_recs)} permits")
    all_records.extend(wasd_recs)

    # Deduplicate across sources
    seen: set = set()
    records: List[PermitRecord] = []
    for r in all_records:
        if r.permit_number not in seen:
            seen.add(r.permit_number)
            records.append(r)

    print(f"\n  [Miami-Dade] Total unique permits: {len(records)}")
    return records


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

def get_county_id(cur) -> int:
    cur.execute("SELECT id FROM counties WHERE county_name = %s", (COUNTY_NAME,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO counties (county_name, state, active, created_at) "
        "VALUES (%s, 'FL', true, NOW()) RETURNING id", (COUNTY_NAME,)
    )
    return cur.fetchone()[0]


def import_records(records: List[PermitRecord]) -> Dict[str, int]:
    if not records or not get_connection:
        return {"raw": 0, "normalized": 0, "skipped": 0}

    conn = get_connection()
    conn.autocommit = False
    stats = {"raw": 0, "normalized": 0, "skipped": 0}

    try:
        with conn.cursor() as cur:
            county_id = get_county_id(cur)
            for rec in records:
                if not rec.permit_number:
                    stats["skipped"] += 1
                    continue

                source_record_id = f"{SOURCE_NAME}::{rec.permit_number}"
                payload = json.dumps(asdict(rec), default=str)

                cur.execute("""
                    INSERT INTO raw_permits
                        (county_id, source_file, source_record_id, raw_payload, issued_date)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_id, source_record_id) DO UPDATE SET
                        raw_payload = EXCLUDED.raw_payload,
                        issued_date = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (county_id, SOURCE_NAME, source_record_id, payload, rec.issued_date))
                rp = cur.fetchone()
                raw_id = rp[0]
                if rp[1]:
                    stats["raw"] += 1

                cur.execute("""
                    INSERT INTO normalized_permits (
                        county_id, raw_permit_id, owner_name, business_name,
                        address_1, city, state, zip,
                        permit_number, permit_type, project_description,
                        issued_date, trade, normalized_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (county_id, permit_number) DO UPDATE SET
                        owner_name          = EXCLUDED.owner_name,
                        address_1           = EXCLUDED.address_1,
                        permit_type         = EXCLUDED.permit_type,
                        project_description = EXCLUDED.project_description,
                        issued_date         = EXCLUDED.issued_date
                    RETURNING id, (xmax = 0) AS is_insert
                """, (
                    county_id, raw_id,
                    rec.owner_name, rec.business_name,
                    rec.address_1, rec.city or "Miami-Dade", "FL", rec.zip,
                    rec.permit_number, rec.permit_type, rec.project_description,
                    rec.issued_date,
                    (rec.permit_type or "").lower()[:100] if rec.permit_type else None,
                    f"mdc::{rec.permit_number}",
                ))
                np = cur.fetchone()
                if np and np[1]:
                    stats["normalized"] += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  [DB] Error: {e}")
        raise
    finally:
        conn.close()

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Miami-Dade permit scraper (ArcGIS API)")
    parser.add_argument("--days-back", type=int, default=90)
    parser.add_argument("--no-db",     action="store_true")
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.days_back)

    records = scrape_miami_dade(start, end)

    if records:
        snap = RAW_DIR / f"miami_dade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        snap.write_text(json.dumps([asdict(r) for r in records], default=str, indent=2))
        print(f"Saved: {snap}")

        # Show sample
        print(f"\nSample records:")
        for r in records[:5]:
            print(f"  {r.permit_number} | {r.owner_name} | {r.address_1} | {r.issued_date}")

    stats = {"raw": 0, "normalized": 0, "skipped": 0}
    if not args.no_db and records:
        stats = import_records(records)

    print(f"\n--- Miami-Dade summary ---")
    print(f"  Records fetched    : {len(records)}")
    print(f"  raw inserted       : {stats['raw']}")
    print(f"  normalized inserted: {stats['normalized']}")
    print(f"  skipped            : {stats['skipped']}")
    if len(records) == 0:
        print(f"\n  [tip] ArcGIS endpoint may use different field names")
        print(f"  [tip] Run with --no-db first to test")
        print(f"  [tip] Check opendata.miamidade.gov for current dataset URL")


if __name__ == "__main__":
    main()