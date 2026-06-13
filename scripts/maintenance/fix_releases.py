import psycopg2
import requests
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# Find all taxpayer names that have a release filed
# Strategy: scrape "Release of Federal Tax Lien" from portal
# then mark those debtors as released in the DB

conn = psycopg2.connect(host='localhost', port=5434, dbname='leadflow', user='postgres', password='postgres')
cur = conn.cursor()

# Check how many have no release status
cur.execute("SELECT COUNT(*) FROM texas_liens WHERE county='Dallas' AND status IS NULL")
print(f"Dallas liens with no status: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM texas_liens WHERE county='Dallas' AND status='active'")
print(f"Dallas active liens: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM texas_liens WHERE county='Dallas' AND status='released'")
print(f"Dallas released liens: {cur.fetchone()[0]}")

conn.close()