import os
import threading
import psycopg2
from psycopg2 import pool as pg_pool
from dotenv import load_dotenv
from utils.logger import log

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError(
                        "DATABASE_URL environment variable is not set. "
                        "Check your .env file or SSM parameter."
                    )
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=5,
                    maxconn=40,
                    dsn=DATABASE_URL,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
                log("✅ DB connection pool initialised (min=5, max=40)")
    return _pool


def get_db_connection() -> psycopg2.extensions.connection:
    return _get_pool().getconn()


def release_db_connection(conn: psycopg2.extensions.connection) -> None:
    try:
        _get_pool().putconn(conn)
    except Exception as e:
        log(f"⚠️ Failed to return connection to pool: {e}")


class managed_connection:
    def __enter__(self) -> psycopg2.extensions.connection:
        self._conn = get_db_connection()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        release_db_connection(self._conn)
        return False