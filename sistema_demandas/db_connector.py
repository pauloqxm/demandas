# db_connector.py

import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from .config import get_db_config

@contextmanager
def get_db_connection():
    """
    Context manager para gerenciar a conexão com o banco de dados PostgreSQL.
    Garante que a conexão seja fechada automaticamente.
    """
    config = get_db_config()
    conn = None
    try:
        conn = psycopg2.connect(
            host=config["host"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            port=config["port"],
            sslmode=config.get("sslmode", "require"),
            connect_timeout=10,
        )
        conn.autocommit = False
        yield conn
    finally:
        if conn:
            conn.close()


def test_db_connection():
    """Testa a conexão com o banco de dados."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                v = cur.fetchone()
                return True, f"✅ Conectado ao PostgreSQL: {v[0]}"
    except Exception as e:
        return False, f"❌ Falha na conexão: {str(e)}"
