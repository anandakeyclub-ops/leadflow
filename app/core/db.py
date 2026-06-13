import os
import psycopg2
from psycopg2 import pool
from urllib.parse import urlparse

# ── Connection pool (prevents "too many clients" errors) ──────────────────────
# Max 5 connections shared across all scripts on this machine.
# Scripts must call get_connection() and close() when done.
_pool = None

def _build_pool():
    global _pool
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        result = urlparse(database_url)
        _pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=5,
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port or 5432,
            sslmode='require'
        )
    else:
        from app.core.settings import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
        _pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=5,
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            host=DB_HOST, port=DB_PORT
        )

def get_connection():
    global _pool
    if _pool is None or _pool.closed:
        _build_pool()
    conn = _pool.getconn()
    # Auto-rollback any leftover transaction from a previous crash
    try:
        conn.rollback()
    except Exception:
        pass
    return conn

def release_connection(conn):
    """Return connection to pool instead of closing it."""
    global _pool
    if _pool and not _pool.closed and conn:
        try:
            conn.rollback()
            _pool.putconn(conn)
        except Exception:
            pass

def close_all():
    """Close all pool connections — call at end of long-running scripts."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None