"""
Deep health report for the gateway -- what GET /health on the Command API returns.

Why this exists
---------------
The old /health returned a hard-coded {"status": "ok"}. The Command API runs on its
own OS thread, so that answer stayed "ok" even when the asyncio loop that actually
serves devices was wedged (for example every pooled DB connection checked out and
nothing returning them). A health check that cannot fail is not a health check.

What it checks (all cheap, all bounded in time)
-----------------------------------------------
  event_loop  A trivial coroutine scheduled on the MAIN loop must run within 1 s.
              If it does not, the loop that reads device sockets is stuck -> "down".
  database    SELECT 1 through the real connection pool, 2 s budget. The result is
              cached (10 s when ok, 3 s when failed) so a polling client cannot drain
              the pool. Unreachable -> "down" (nothing can be saved). Slow -> "degraded".
  pool        Connections in use vs max. Fully checked out -> "degraded": an early
              warning for the connection-exhaustion incident class.
  capacity    Registered devices vs MAX_CONNECTIONS; >= 90 % -> "degraded".
  silence     No valid packet from ANY device for HEALTH_SILENCE_SECONDS (default 600)
              -> "degraded". Parked V5 units still report every ~300 s, so ten minutes
              of total silence is not a quiet night, it is a problem. Measured from
              process start until the first packet, so a fresh restart gets a grace
              period instead of an instant alarm.

Deliberately NOT in this report: IMEIs, IPs, connection strings. /devices has the device
list; this answers "is the gateway healthy" and nothing else. Exception text is reduced
to the exception CLASS name -- asyncpg messages can embed host names.

Deliberately NOT a load-balancer / ASG liveness probe either. It includes the database;
if an autoscaling health check killed the gateway whenever Supabase blinked, a database
blip would turn into a full device outage. A liveness-only endpoint is a separate,
infra-side change.

This module is imported at gateway start-up (command_api -> health_service). It must never
raise at import time: env overrides are parsed defensively, and asyncpg/database are
imported lazily inside the probe so a problem there cannot stop device ingestion booting.
"""

import asyncio
import concurrent.futures
import os
import threading
import time
from datetime import datetime, timezone

from config import MAX_CONNECTIONS
from services.device_registry import get_all_devices, get_last_packet_at
from utils.logger import log

OK, DEGRADED, DOWN = "ok", "degraded", "down"
_RANK = {OK: 0, DEGRADED: 1, DOWN: 2}


def _env_number(name: str, default, cast):
    """A malformed override falls back to the default instead of crashing start-up."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        log(f"⚠️ Ignoring invalid {name}={raw!r}; using {default}")
        return default


LOOP_PROBE_TIMEOUT = 1.0          # seconds the main loop gets to run a no-op
DB_PROBE_TIMEOUT = 2.0            # seconds the SELECT 1 gets, pool wait included
SLOW_DB_MS = 1500.0               # SELECT 1 slower than this -> degraded
DB_CACHE_OK_SECONDS = _env_number("HEALTH_DB_CACHE_SECONDS", 10.0, float)
DB_CACHE_FAIL_SECONDS = 3.0       # re-test a failure sooner than a success
SILENCE_SECONDS = _env_number("HEALTH_SILENCE_SECONDS", 600, int)
CAPACITY_WARN_RATIO = 0.9

_started_mono = time.monotonic()
_started_at = datetime.now(timezone.utc)

_db_lock = threading.Lock()
_db_cache: dict | None = None

_log_lock = threading.Lock()
_last_logged_state: str | None = None


async def _noop() -> bool:
    return True


async def _db_ping() -> None:
    # Lazy import: keeps this module importable (and unit-testable) without asyncpg.
    from database import managed_connection

    async with managed_connection() as conn:
        await conn.fetchval("SELECT 1")


async def _db_probe() -> None:
    await asyncio.wait_for(_db_ping(), timeout=DB_PROBE_TIMEOUT)


def _call_on_loop(loop, coro, timeout: float):
    """
    Run `coro` on the gateway's MAIN event loop from the HTTP server thread and wait
    up to `timeout` seconds. Returns (ok, elapsed_ms, error_name). Never raises.

    A timed-out coroutine is cancelled so a stuck loop cannot accumulate a queue of
    abandoned probes.
    """
    if loop is None or loop.is_closed():
        coro.close()
        return False, None, "no_event_loop"

    started = time.perf_counter()
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as ex:  # loop closed between the check above and here
        coro.close()
        return False, None, type(ex).__name__

    try:
        future.result(timeout=timeout)
        return True, (time.perf_counter() - started) * 1000.0, None
    except (concurrent.futures.TimeoutError, TimeoutError):
        future.cancel()
        return False, None, "timeout"
    except Exception as ex:
        return False, None, type(ex).__name__


def _db_check(loop) -> dict:
    """SELECT 1 through the pool, cached so polling cannot hammer the pooler."""
    global _db_cache

    with _db_lock:
        now = time.monotonic()
        cached = _db_cache
        if cached is not None:
            ttl = DB_CACHE_OK_SECONDS if cached["ok"] else DB_CACHE_FAIL_SECONDS
            if now - cached["at"] < ttl:
                return {
                    "ok": cached["ok"],
                    "ms": cached["ms"],
                    "error": cached["error"],
                    "cached_age_s": round(now - cached["at"], 1),
                }

        ok, ms, error = _call_on_loop(loop, _db_probe(), DB_PROBE_TIMEOUT + 0.5)
        ms = round(ms, 1) if ms is not None else None
        _db_cache = {"ok": ok, "ms": ms, "error": error, "at": now}
        return {"ok": ok, "ms": ms, "error": error, "cached_age_s": 0.0}


def _pool_stats() -> dict | None:
    """Pool occupancy -- plain attribute reads on asyncpg's pool, safe off-loop."""
    try:
        from database import get_pool

        pool = get_pool()
        return {
            "size": pool.get_size(),
            "idle": pool.get_idle_size(),
            "max": pool.get_max_size(),
        }
    except Exception:
        return None


def _round(value, places: int = 1):
    return round(value, places) if value is not None else None


def get_health(loop) -> dict:
    """
    Build the health report. Called from the Command API's HTTP thread; `loop` is the
    gateway's main asyncio loop. Worst case runtime is ~3 s (a slow database); if the
    loop itself is stuck it returns after ~1 s without touching the database.
    """
    state = OK
    reasons: list[str] = []

    def flag(level: str, reason: str) -> None:
        nonlocal state
        reasons.append(reason)
        if _RANK[level] > _RANK[state]:
            state = level

    # 1. Is the loop that reads device sockets actually turning?
    loop_ok, loop_ms, loop_error = _call_on_loop(loop, _noop(), LOOP_PROBE_TIMEOUT)
    checks: dict = {"event_loop": {"ok": loop_ok, "ms": _round(loop_ms, 2), "error": loop_error}}

    if not loop_ok:
        flag(DOWN, f"event loop not responding ({loop_error}) - device sockets are not being served")
        checks["database"] = {"ok": None, "skipped": "event loop not responding"}
    else:
        # 2. Can we actually save what devices send?
        db = _db_check(loop)
        pool = _pool_stats()
        if pool is not None:
            db["pool"] = pool
        checks["database"] = db

        if not db["ok"]:
            flag(DOWN, f"database unreachable ({db['error']}) - location data cannot be saved")
        elif db["ms"] is not None and db["ms"] > SLOW_DB_MS:
            flag(DEGRADED, f"database slow ({int(db['ms'])} ms for SELECT 1)")

        if pool and pool["max"] and pool["idle"] == 0 and pool["size"] >= pool["max"]:
            flag(DEGRADED, f"database pool exhausted ({pool['size']}/{pool['max']} connections in use)")

    # 3. Headroom on the device side.
    connected = len(get_all_devices())
    if MAX_CONNECTIONS > 0 and connected >= MAX_CONNECTIONS * CAPACITY_WARN_RATIO:
        flag(DEGRADED, f"{connected} devices connected, near the {MAX_CONNECTIONS} connection limit")

    # 4. Is anything arriving at all?
    now = datetime.now(timezone.utc)
    last_packet = get_last_packet_at()
    silent_for = (now - (last_packet or _started_at)).total_seconds()
    if silent_for > SILENCE_SECONDS:
        minutes = int(silent_for // 60)
        if last_packet is not None:
            flag(DEGRADED, f"no valid packet from any device for {minutes} min")
        else:
            flag(DEGRADED, f"no valid packet from any device since the gateway started {minutes} min ago")

    report = {
        "status": state,
        "reasons": reasons,
        "checks": checks,
        "devices_connected": connected,
        "last_packet_age_s": _round((now - last_packet).total_seconds()) if last_packet else None,
        "uptime_s": int(time.monotonic() - _started_mono),
    }
    _log_state_change(state, reasons)
    return report


def _log_state_change(state: str, reasons: list[str]) -> None:
    """One log line per transition, not one per poll."""
    global _last_logged_state
    with _log_lock:
        if state == _last_logged_state:
            return
        previous, _last_logged_state = _last_logged_state, state
    detail = "; ".join(reasons) if reasons else "all checks passing"
    log(f"🩺 Health {previous or 'unknown'} -> {state}: {detail}")