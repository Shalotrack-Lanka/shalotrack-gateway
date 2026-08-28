import os
import time
import threading
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2 import OperationalError
from dotenv import load_dotenv
from utils.logger import log

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "5"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "150"))

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
                    minconn=DB_POOL_MIN,
                    maxconn=DB_POOL_MAX,
                    dsn=DATABASE_URL,
                    # Keepalives prevent Supabase from silently dropping
                    # idle connections after ~5 minutes of inactivity
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                    # Connection timeout — fail fast if Supabase is unreachable
                    connect_timeout=10,
                )
                log(f"✅ DB connection pool initialised (min={DB_POOL_MIN}, max={DB_POOL_MAX})")
    return _pool


def _validate_connection(conn) -> bool:
    """
    Check if a borrowed connection is still alive.

    Supabase's PgBouncer may silently close idle connections. If we hand
    out a dead connection, the caller gets an error mid-query. This check
    catches dead connections early and discards them so the pool replaces
    them with fresh ones.

    Uses a cheap SELECT 1 — fast, no table scan, always succeeds on live conn.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return True
    except Exception:
        return False


def get_db_connection(timeout: float = 5.0) -> psycopg2.extensions.connection:
    """
    Borrow a validated connection from the pool.

    Retries for up to `timeout` seconds if:
    - Pool is exhausted (all slots in use)
    - Borrowed connection is dead (silently replaced)

    This ensures callers always receive a live, usable connection.
    """
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        try:
            conn = _get_pool().getconn()

            # Validate the connection before handing it out
            if not _validate_connection(conn):
                # Dead connection — discard and let pool create a fresh one
                try:
                    _get_pool().putconn(conn, close=True)
                except Exception:
                    pass
                time.sleep(0.1)
                continue

            return conn

        except pg_pool.PoolError as e:
            last_error = e
            time.sleep(0.1)
        except OperationalError as e:
            # DB unreachable — wait and retry
            last_error = e
            time.sleep(0.5)

    log(f"❌ DB pool exhausted after {timeout}s — all {DB_POOL_MAX} connections in use")
    raise last_error or Exception("DB pool exhausted")


def release_db_connection(conn: psycopg2.extensions.connection) -> None:
    """
    Return a connection to the pool.
    Always call in a finally block — never let a borrowed connection leak.
    If the connection is broken, it is discarded and the pool opens a fresh one.
    """
    try:
        # Check if connection is in a bad state
        if conn.closed or conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
            try:
                conn.rollback()
            except Exception:
                pass

        _get_pool().putconn(conn)
    except Exception as e:
        log(f"⚠️ Failed to return connection to pool: {e}")
        # Try to discard the broken connection
        try:
            _get_pool().putconn(conn, close=True)
        except Exception:
            pass


class managed_connection:
    """
    Context manager that guarantees the connection is always returned
    to the pool, even if an exception is raised mid-query.

    Usage:
        with managed_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
            conn.commit()
            cursor.close()
    """
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