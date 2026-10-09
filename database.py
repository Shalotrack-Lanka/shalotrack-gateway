import os
import asyncpg
from dotenv import load_dotenv
from utils.logger import log

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "5"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "150"))

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set. "
                "Check your .env file or SSM parameter."
            )
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=DB_POOL_MIN,
            max_size=DB_POOL_MAX,
            command_timeout=10,
            max_inactive_connection_lifetime=300,
            # Required because DATABASE_URL points at Supabase's Supavisor
            # pooler in TRANSACTION mode (port 6543). Transaction mode
            # multiplexes every app-side connection across a small shared
            # pool of real backend Postgres connections (15 on Micro tier) —
            # a given logical connection can be served by a *different*
            # backend connection on every transaction. asyncpg's default
            # server-side prepared-statement cache assumes one backend
            # connection stays fixed, so with transaction-mode pooling it
            # will intermittently bind a statement that no longer exists on
            # whichever backend connection the pooler happens to hand back.
            # That shows up as sporadic, hard-to-reproduce query failures
            # under load instead of a clean error — disable it entirely.
            statement_cache_size=0,
        )
        log(f"✅ DB connection pool initialised (min={DB_POOL_MIN}, max={DB_POOL_MAX})")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_db_connection() -> asyncpg.pool.PoolConnectionProxy:
    return await get_pool().acquire()


async def release_db_connection(conn) -> None:
    await get_pool().release(conn)


class managed_connection:
    """
    Async context manager for borrowing a pooled connection:

        async with managed_connection() as conn:
            await conn.execute(...)

    Always releases back to the pool, even on exception — never call
    conn.close() directly on a pooled connection, that destroys it
    instead of returning it and silently shrinks the pool under load.
    """

    async def __aenter__(self) -> asyncpg.pool.PoolConnectionProxy:
        self._conn = await get_pool().acquire()
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        await get_pool().release(self._conn)
        return False