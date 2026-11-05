"""Pure functions that turn gateway data into what the UI shows. No Streamlit, no network."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .gateway_client import Device

# A device that stays silent longer than the gateway's own "no packets at all" alarm
# (HEALTH_SILENCE_SECONDS, default 600 s) deserves a look.
ACTIVE_SECONDS = 600
QUIET_SECONDS = 1800
# Same threshold as the gateway's own check (health_service.SLOW_DB_MS), so the table and
# the banner never disagree.
SLOW_DB_MS = 1500

# What each command does, from the gateway's command catalogue. Anything the gateway
# adds later that is not listed here lands in "other" and is shown as such, not hidden.
QUERY_COMMANDS = frozenset({
    "where", "status", "version", "imei", "params", "gprsset", "url", "position",
    "fence_query", "moving_query", "speed_query", "sos_query", "timer_query",
    "apn_query", "server_query",
})
CONTROL_COMMANDS = frozenset({"relay_on", "relay_off", "reset"})
CONFIG_COMMANDS = frozenset({
    "timer", "distance", "speed_alarm", "moving_alarm", "fence_circle",
    "sos_add", "sos_delete", "apn", "server", "batalm", "poweralm",
})
# These two can silently re-point or cut off a device; call them out in the UI.
HIGH_IMPACT = frozenset({"relay_off", "server", "apn"})


def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def fmt_ms(ms: float | int | None) -> str:
    if ms is None:
        return "n/a"
    return f"{ms:.0f} ms" if ms >= 100 else f"{ms:.1f} ms"


def device_state(idle_seconds: float) -> str:
    if idle_seconds <= ACTIVE_SECONDS:
        return "active"
    if idle_seconds <= QUIET_SECONDS:
        return "quiet"
    return "stale"


def local_time(dt: datetime, tz_name: str) -> str:
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")


def device_rows(devices: list[Device], now: datetime) -> list[dict]:
    """Longest-silent first: the rows an operator needs to see are at the top."""
    rows = []
    for d in devices:
        idle = max((now - d.last_seen).total_seconds(), 0)
        rows.append({
            "IMEI": d.imei,
            "Protocol": d.protocol,
            "IP": d.ip,
            "State": device_state(idle),
            "Last packet": fmt_duration(idle) + " ago",
            "Connected for": fmt_duration((now - d.connected_at).total_seconds()),
            "_idle": idle,
        })
    rows.sort(key=lambda r: r["_idle"], reverse=True)
    return rows


def state_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"active": 0, "quiet": 0, "stale": 0}
    for r in rows:
        counts[r["State"]] += 1
    return counts


def group_commands(names: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"query": [], "config": [], "control": [], "other": []}
    for name in sorted(names):
        if name in QUERY_COMMANDS:
            groups["query"].append(name)
        elif name in CONTROL_COMMANDS:
            groups["control"].append(name)
        elif name in CONFIG_COMMANDS:
            groups["config"].append(name)
        else:
            groups["other"].append(name)
    return groups


def health_headline(report: dict) -> tuple[str, str]:
    """(level, text) where level is ok | degraded | down."""
    status = report.get("status")
    if status == "ok":
        return "ok", "Gateway healthy"
    if status == "degraded":
        return "degraded", "Gateway degraded"
    return "down", "Gateway down"


def check_rows(report: dict) -> list[dict]:
    """Flatten the health report into a table. Tolerates missing keys: the gateway may add or drop checks."""
    checks = report.get("checks") or {}
    rows: list[dict] = []

    loop = checks.get("event_loop") or {}
    rows.append({
        "Check": "Event loop (serves device sockets)",
        "Result": "pass" if loop.get("ok") else "FAIL",
        "Detail": fmt_ms(loop["ms"]) if loop.get("ms") is not None else (loop.get("error") or ""),
    })

    db = checks.get("database") or {}
    if db.get("ok") is None:
        rows.append({"Check": "Database", "Result": "skipped", "Detail": db.get("skipped", "")})
    else:
        detail = f"SELECT 1 in {fmt_ms(db['ms'])}" if db.get("ms") is not None else (db.get("error") or "")
        result = "FAIL" if not db["ok"] else ("slow" if (db.get("ms") or 0) > SLOW_DB_MS else "pass")
        rows.append({"Check": "Database", "Result": result, "Detail": detail})
        pool = db.get("pool")
        if isinstance(pool, dict) and {"size", "idle", "max"} <= pool.keys():
            used = pool["size"] - pool["idle"]
            full = bool(pool["max"]) and pool["idle"] == 0 and pool["size"] >= pool["max"]
            rows.append({
                "Check": "Connection pool",
                "Result": "EXHAUSTED" if full else "pass",
                "Detail": f"{used} in use of {pool['max']} max",
            })

    age = report.get("last_packet_age_s")
    rows.append({
        "Check": "Packet flow",
        "Result": "pass" if age is not None else "none yet",
        "Detail": f"last valid packet {fmt_duration(age)} ago" if age is not None else "no packet since start",
    })
    return rows
