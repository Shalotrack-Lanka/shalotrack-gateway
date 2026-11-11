"""
Acts on the login rule for devices that are ALREADY connected.

The login rule (services/device_access.py) is only checked when a device logs in. A socket that stays open
would otherwise be trusted forever, so a device that is stopped in the ERP, or whose 24h trial ran out, would
keep reporting until it happened to reconnect. Two things close that gap:

  drop_device(imei)   one device, on demand. The API calls it (POST /drop on the Command API) the moment the
                      ERP stops a device. Works in every mode: it is an explicit order, not a rule.
  run_recheck_loop()  every DEVICE_RECHECK_SECONDS, one batched query for all connected devices; any device
                      the current rule would refuse is dropped. Safety net for a missed kick and for trial
                      expiry while connected.

DEVICE_RECHECK_MODE: off (default, loop does not run) | shadow (log "would drop", drop nothing) | enforce.
A refused device cannot log back in: the same rule runs at login.

Safety valves: a database error never drops anything, and one cycle never drops more than
DEVICE_RECHECK_MAX_DROP_FRACTION of the connected devices (minimum 5), so a bad sync cannot disconnect the
whole fleet at once. If the valve trips, nothing is dropped and a loud line is logged.
"""

import asyncio
import os

from database import get_db_connection, release_db_connection
from services import device_access
from services.device_registry import get_all_devices, get_device
from utils.logger import log

RECHECK_MODE = os.getenv("DEVICE_RECHECK_MODE", "off").strip().lower()
if RECHECK_MODE not in ("off", "shadow", "enforce"):
    RECHECK_MODE = "off"

RECHECK_SECONDS = int(os.getenv("DEVICE_RECHECK_SECONDS", "300"))
MAX_DROP_FRACTION = float(os.getenv("DEVICE_RECHECK_MAX_DROP_FRACTION", "0.2"))
MIN_DROP_ALLOWANCE = 5
_BATCH = 1000


async def drop_device(imei: str, reason: str = "dropped") -> bool:
    """Close the device's socket. True if it was connected. Its connection handler cleans up (offline, unregister)."""
    entry = get_device(imei)
    if not entry:
        return False
    writer = entry["socket"]
    log(f"✂️  Dropping IMEI {imei} ({reason})")
    try:
        writer.close()
    except Exception as ex:  # already closing
        log(f"⚠️ Drop of {imei}: {ex}")
    return True


def connected_rule_allows(row, mode: str | None = None) -> tuple[bool, str]:
    """Would the CURRENT login rule still let this device connect? row = SetupShalotrackDevices row, or None."""
    mode = device_access.MODE if mode is None else mode
    if row is None:
        return True, "no_setup_row"          # legacy/demo device: allowed, as at login
    status = row["Status"]
    if mode == "enforce":
        d = device_access.decide(status, row.get("BoundAt"), row.get("FirstSeenAt"))
        if d.stamp_first_seen:               # connected but clock never started: do not cut it off here
            return True, "trial_clock_missing"
        return d.allowed, d.reason
    ok = device_access.old_rule(status)
    return ok, "activated" if ok else "not_activated"


async def _fetch_rows(imeis: list[str]) -> dict:
    """One query per batch. Returns {imei: row-as-dict}; raises on database error (caller drops nothing)."""
    trial_on = device_access.MODE == "enforce"
    cols = '"ImeiNumber", "Status"' + (', "BoundAt", "FirstSeenAt"' if trial_on else "")
    out: dict = {}
    conn = await get_db_connection()
    try:
        for i in range(0, len(imeis), _BATCH):
            chunk = imeis[i:i + _BATCH]
            rows = await conn.fetch(
                f'SELECT {cols} FROM "SetupShalotrackDevices" WHERE "ImeiNumber" = ANY($1::text[])', chunk
            )
            for r in rows:
                out[r["ImeiNumber"]] = dict(r)
    finally:
        await release_db_connection(conn)
    return out


async def recheck_once(mode: str | None = None, fetch_rows=None, drop=None) -> dict:
    """One pass over every connected device. Returns counts (also used by tests)."""
    mode = RECHECK_MODE if mode is None else mode
    fetch_rows = fetch_rows or _fetch_rows
    drop = drop or drop_device

    imeis = list(get_all_devices().keys())
    result = {"checked": len(imeis), "would_drop": [], "dropped": [], "blocked": False, "error": None}
    if not imeis or mode == "off":
        return result

    try:
        rows = await fetch_rows(imeis)
    except Exception as ex:
        result["error"] = str(ex)
        log(f"❌ Re-check skipped (database error, nothing dropped): {ex}")
        return result

    to_drop = []
    for imei in imeis:
        ok, reason = connected_rule_allows(rows.get(imei))
        if not ok:
            to_drop.append((imei, reason))
    result["would_drop"] = [i for i, _ in to_drop]
    if not to_drop:
        return result

    allowance = max(MIN_DROP_ALLOWANCE, int(len(imeis) * MAX_DROP_FRACTION))
    if len(to_drop) > allowance:
        result["blocked"] = True
        log(f"🚨 Re-check wants to drop {len(to_drop)} of {len(imeis)} devices (limit {allowance} per cycle). "
            f"Dropping NOTHING: this looks like a bad sync, not real changes. Check SetupShalotrackDevices.")
        return result

    for imei, reason in to_drop:
        if mode == "shadow":
            log(f"🧪 RECHECK-SHADOW would drop IMEI {imei} ({reason})")
        else:
            if await drop(imei, f"re-check: {reason}"):
                result["dropped"].append(imei)
    return result


async def run_recheck_loop() -> None:
    log(f"⚙️  Device re-check    : {RECHECK_MODE}, every {RECHECK_SECONDS}s")
    while True:
        await asyncio.sleep(RECHECK_SECONDS)
        try:
            await recheck_once()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            log(f"❌ Re-check error: {ex}")
