"""
Safety net for GPS103 (TK103) trackers that stay connected but only report a
position when asked.

Every GPS103_POLL_SECONDS the poller looks at each connected GPS103 device. If no
position has been stored for it within its interval (GPS103_POLL_SECONDS while
the last stored speed was above 0, GPS103_POLL_IDLE_SECONDS while parked), it
sends the single-position command (`**,imei:<imei>,B;`, the same frame as the
portal's Where button). The tracker answers with a normal report, which the GPS103
handler stores like any other. Off unless GPS103_POLL_SECONDS > 0.

This does not replace configuring the unit to report on its own (SMS, see
docs/gps103-commands.md); it keeps the map alive until that is done, and covers
clones that cannot be configured.
"""

import asyncio
from datetime import datetime, timezone

import config
from services.device_registry import get_all_devices
from utils.gps103_command_builder import build_single_position
from utils.logger import log

GPS103 = "GPS103"
_WRITE_TIMEOUT = 5.0


def _interval_for(entry: dict) -> int:
    speed = entry.get("last_speed")
    moving = speed is not None and speed > 0
    return config.GPS103_POLL_SECONDS if moving else config.GPS103_POLL_IDLE_SECONDS


def due_devices(now: datetime | None = None) -> list[str]:
    """IMEIs of connected GPS103 devices that should be asked for a position now."""
    now = now or datetime.now(timezone.utc)
    due = []
    for imei, entry in get_all_devices().items():
        if entry.get("protocol") != GPS103:
            continue
        # Never reported yet: count from the moment it connected.
        since = entry.get("last_position_at") or entry["connected_at"]
        last_poll = entry.get("last_poll_at")
        newest = max(since, last_poll) if last_poll else since
        if (now - newest).total_seconds() >= _interval_for(entry):
            due.append(imei)
    return due


async def poll_once(now: datetime | None = None) -> int:
    """Send one position request to every due device. Returns how many were sent."""
    sent = 0
    for imei in due_devices(now):
        entry = get_all_devices().get(imei)
        if not entry:
            continue
        try:
            writer = entry["socket"]
            writer.write(build_single_position(imei))
            await asyncio.wait_for(writer.drain(), _WRITE_TIMEOUT)
            entry["last_poll_at"] = datetime.now(timezone.utc)
            sent += 1
        except Exception as ex:      # one bad socket must not stop the others
            log(f"⚠️ GPS103 poll failed for {imei}: {ex}")
    if sent:
        log(f"📡 GPS103 poller asked {sent} silent device(s) for a position")
    return sent


async def run_gps103_poller() -> None:
    # Wake often enough to honour the shortest interval, never faster than 5 s.
    tick = max(5, config.GPS103_POLL_SECONDS // 2)
    while True:
        await asyncio.sleep(tick)
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            log(f"⚠️ GPS103 poller error: {ex}")
