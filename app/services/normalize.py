import re
import hashlib

ENTITY_WORDS = [
    "llc", "inc", "corp", "corporation", "co", "company",
    "ltd", "pllc", "plc", "trust"
]

def clean_text(value: str) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value

def normalize_name(name: str) -> str:
    name = clean_text(name)
    for word in ENTITY_WORDS:
        name = re.sub(rf"\b{word}\b", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

def normalize_address(address: str) -> str:
    address = clean_text(address)
    replacements = {
        " street": " st",
        " avenue": " ave",
        " road": " rd",
        " boulevard": " blvd",
        " drive": " dr",
        " lane": " ln",
        " court": " ct",
        " place": " pl"
    }
    for old, new in replacements.items():
        address = address.replace(old, new)
    address = re.sub(r"[^a-z0-9\s]", "", address)
    address = re.sub(r"\s+", " ", address).strip()
    return address

def make_hash(*values) -> str:
    raw = "|".join([clean_text(str(v)) for v in values if v is not None])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()