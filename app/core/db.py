import os
import psycopg2
from urllib.parse import urlparse

def get_connection():
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        # Render PostgreSQL connection
        result = urlparse(database_url)
        return psycopg2.connect(
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port or 5432,
            sslmode='require'
        )
    else:
        # Local development
        from app.core.settings import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
        return psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            host=DB_HOST, port=DB_PORT
        )
