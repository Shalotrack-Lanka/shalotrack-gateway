"""
Read-only report for the ERP "Gateway devices" page: which registered devices are connected, which are
in their free trial (and for how long), which have run out and wait for the office to bind them, and
which are stopped.

It answers "what would the trial rule do to each device right now", whatever DEVICE_TRIAL_MODE is, and
says which mode is active so the page can label it. Nothing is written.
"""

from datetime import datetime, timedelta, timezone

from services import device_access, device_enforcer
from services.device_registry import get_all_devices
from database import get_db_connection, release_db_connection

MAX_ROWS = 1000

# Order rows are listed in: things that need the office first.
_ORDER = {"trial_expired": 0, "stopped": 1, "trial": 2, "trial_not_started": 3, "allowed": 4}


async def _fetch_all() -> list[dict]:
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            'SELECT "ImeiNumber", "Status", "BoundAt", "FirstSeenAt" FROM "SetupShalotrackDevices"'
        )
        return [dict(r) for r in rows]
    finally:
        await release_db_connection(conn)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def classify(row: dict, now: datetime, trial_hours: int) -> tuple[str, float | None]:
    """-> (category, trial hours left or None). Categories: allowed, trial, trial_not_started, trial_expired, stopped."""
    d = device_access.decide(row.get("Status"), row.get("BoundAt"), row.get("FirstSeenAt"), now, trial_hours)
    if d.reason in ("activated", "bound"):
        return "allowed", None
    if d.reason == "stopped":
        return "stopped", None
    if d.reason == "trial_started":
        return "trial_not_started", float(trial_hours)
    first = row["FirstSeenAt"]
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    left = (first + timedelta(hours=trial_hours) - now).total_seconds() / 3600
    if d.reason == "trial":
        return "trial", round(left, 1)
    return "trial_expired", 0.0


async def build_report(fetch=None, now: datetime | None = None, trial_hours: int | None = None) -> dict:
    fetch = fetch or _fetch_all
    now = now or datetime.now(timezone.utc)
    trial_hours = device_access.TRIAL_HOURS if trial_hours is None else trial_hours

    rows = await fetch()
    connected = get_all_devices()

    counts = {"allowed": 0, "trial": 0, "trial_not_started": 0, "trial_expired": 0, "stopped": 0}
    out = []
    for r in rows:
        cat, left = classify(r, now, trial_hours)
        counts[cat] += 1
        is_conn = r["ImeiNumber"] in connected
        if cat == "allowed" and not is_conn:
            continue                      # nothing to tell the office about a quiet, fine device
        out.append({
            "imei": r["ImeiNumber"],
            "status": r["Status"],
            "category": cat,
            "trial_hours_left": left,
            "first_seen_at": _iso(r.get("FirstSeenAt")),
            "bound_at": _iso(r.get("BoundAt")),
            "connected": is_conn,
        })
    out.sort(key=lambda x: (_ORDER[x["category"]], x["trial_hours_left"] if x["trial_hours_left"] is not None else 1e9))

    known = {r["ImeiNumber"] for r in rows}
    return {
        "generated_at": now.isoformat(),
        "trial_mode": device_access.MODE,
        "recheck_mode": device_enforcer.RECHECK_MODE,
        "trial_hours": trial_hours,
        "total_devices": len(rows),
        "connected_now": len(connected),
        "connected_not_registered": sorted(i for i in connected if i not in known)[:50],
        "counts": counts,
        "truncated": len(out) > MAX_ROWS,
        "devices": out[:MAX_ROWS],
    }
