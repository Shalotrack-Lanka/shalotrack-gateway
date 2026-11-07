"""
Teltonika (FMB series, Codec 8 / 8E) frame handler.

Same contract as services/packet_handler.process_packet and
services/gps103_handler.process_gps103_message: tcp_server calls
process_teltonika_frame() once per framed unit and it returns the device IMEI
once the connection is authenticated, else None.

Design rules, all deliberate:

* Login is strict. An IMEI that is not admin-activated gets the socket closed
  with no reply. After login the socket is bound to that IMEI; Teltonika data
  packets carry no IMEI of their own, so data before a login is dropped.
* At-least-once delivery. The device keeps records until the server says how
  many it took. A record is counted as accepted only when it was stored or
  deliberately skipped (no GPS fix, impossible position, absurd timestamp).
  If a database write fails we stop and acknowledge only what was stored, so
  the device resends the rest instead of losing it. Enable
  GPS_TRACKING_IDEMPOTENT once the unique index exists so that resends never
  create duplicate rows (docs/gps-tracking-idempotency.md).
* A bad CRC or an undecodable packet is NOT acknowledged. The raw bytes are
  kept (RawPackets, always) so a new codec or firmware quirk can be added from
  a real capture instead of guessed.
* Position records go through the same single-statement save_location as
  every other family. Battery and signal are not written: their units differ
  between protocols (GT06 reports a 0-6 level), so they stay at the neutral
  placeholder until the portal defines one scale.
* Not implemented yet: alarm/event IOs (panic button, geofence, towing),
  Codec 12 command responses, outbound commands. Commands return 501 for
  these devices like they do for GPS103.
"""

import time
from datetime import datetime, timedelta, timezone

from config import TELTONIKA_MAX_FUTURE_SECONDS
from constants.event_types import EventType
from constants.severity import Severity
from parsers import teltonika_parser as p
from protocols import TELTONIKA
from repositories.device_repository import DeviceRepository
from repositories.raw_packet_repository import RawPacketRepository
from services.device_registry import register_device, get_device, get_imei_by_socket
from services.event_service import create_event
from services.raw_packet_policy import should_store_raw
from services.tracking_service import save_location, mark_device_alive, set_ignition
from utils.logger import log, debug

DEFAULT_MODEL = "FMB"

# A Teltonika device has no separate heartbeat: every data packet proves it is
# alive. Refreshing LastSeen on every packet would add a statement per packet,
# so it is refreshed at most this often per device.
_ALIVE_REFRESH_SECONDS = 60
_last_alive: dict[str, float] = {}
_last_ignition: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _close(writer) -> None:
    try:
        writer.close()
    except Exception:
        pass


async def _send(writer, payload: bytes) -> None:
    try:
        writer.write(payload)
        await writer.drain()
        debug(f"📤 Reply sent: {payload.hex()}")
    except Exception as ex:
        log(f"❌ Reply error: {ex}")


async def _safely(label: str, coro) -> None:
    """Run one independent DB write; log and swallow its failure."""
    try:
        await coro
    except Exception as ex:
        log(f"❌ {label} failed: {ex}")


async def _save_raw(device_id, raw: bytes, always: bool = False, parsed: bool = True) -> None:
    """Subject to RAW_PACKET_MODE (services/raw_packet_policy.py)."""
    if not should_store_raw(always):
        return
    await RawPacketRepository.save(
        device_id=device_id,
        protocol_number=TELTONIKA,
        raw_hex=raw.hex(),
        parsed=parsed,
    )


def _position_ok(rec: p.AvlRecord) -> bool:
    """Same physical-bounds rule as the other families."""
    return (
        -90 <= rec.latitude <= 90 and -180 <= rec.longitude <= 180
        and 0 <= rec.speed <= 300 and 0 <= rec.angle <= 360
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

async def process_teltonika_frame(raw: bytes, writer, addr):
    try:
        if p.is_login_frame(raw):
            return await _handle_login(raw, writer, addr)
        return await _handle_avl(raw, writer, addr)
    except Exception as ex:
        log(f"❌ TELTONIKA PACKET ERROR: {ex}")
        return get_imei_by_socket(writer)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

async def _handle_login(raw, writer, addr):
    try:
        imei = p.parse_login(raw)
    except p.TeltonikaParseError as ex:
        log(f"❌ Malformed Teltonika login from {addr[0]}: {ex}")
        _close(writer)
        return None

    log(f"📡 Teltonika login — IMEI: {imei}")

    device_id = await DeviceRepository.get_device_by_imei(
        imei, protocol_type=TELTONIKA, default_model=DEFAULT_MODEL
    )
    if not device_id:
        log(f"🚫 REJECTED — IMEI not admin-activated: {imei} from {addr[0]}")
        _close(writer)
        return None

    log(f"✅ Authorised Device — IMEI: {imei} → {device_id}")
    register_device(imei, addr[0], device_id, writer, protocol=TELTONIKA)

    # Fresh connection: forget cached state so the first packet refreshes it.
    _last_alive.pop(device_id, None)
    _last_ignition.pop(device_id, None)

    # The device sends nothing until it sees 01, so answer before bookkeeping.
    await _send(writer, p.LOGIN_ACCEPT)

    await _safely("raw packet save", _save_raw(device_id, raw, always=True))
    await _safely("mark online", mark_device_alive(device_id))
    try:
        vehicle_id = await DeviceRepository.get_vehicle_by_device(device_id)
    except Exception:
        vehicle_id = None
    await _safely("online event", create_event(
        device_id=device_id,
        vehicle_id=vehicle_id,
        event_type=EventType.DEVICE_ONLINE.value,
        severity=Severity.LOW,
        description="Device connected to the gateway",
        metadata={"protocol": TELTONIKA},
    ))
    return imei


# ---------------------------------------------------------------------------
# AVL data
# ---------------------------------------------------------------------------

async def _handle_avl(raw, writer, addr):
    imei = get_imei_by_socket(writer)
    if not imei:
        log(f"🚫 Teltonika data before login from {addr[0]} — closing")
        _close(writer)
        return None

    device_id = get_device(imei)["device_id"]

    try:
        packet = p.parse_avl_packet(raw)
    except p.TeltonikaCrcError:
        log(f"❌ Teltonika CRC mismatch from {imei} — not acknowledged, the device will resend")
        return imei
    except p.TeltonikaParseError as ex:
        # Includes codecs we do not decode. Keep the bytes for a future parser.
        log(f"❓ Undecodable Teltonika packet from {imei}: {ex} — raw bytes kept, not acknowledged")
        await _safely("raw packet save", _save_raw(device_id, raw, always=True, parsed=False))
        return imei

    await _safely("raw packet save", _save_raw(device_id, raw))
    await _refresh_status(device_id, packet.records)

    accepted = 0
    for record in packet.records:
        if not await _store_record(device_id, imei, record):
            break
        accepted += 1

    await _send(writer, p.ack(accepted))
    if accepted < len(packet.records):
        log(f"⚠️ Teltonika {imei}: stored {accepted}/{len(packet.records)} records — the device will resend the rest")
    return imei


async def _store_record(device_id: str, imei: str, rec: p.AvlRecord) -> bool:
    """True when the record is dealt with (stored or skipped on purpose); False on a DB failure."""
    if not rec.has_fix:
        debug(f"Teltonika {imei}: record without GPS fix skipped")
        return True

    latest_allowed = datetime.now(timezone.utc) + timedelta(seconds=TELTONIKA_MAX_FUTURE_SECONDS)
    if rec.timestamp > latest_allowed:
        log(f"⚠️ Teltonika {imei}: record timestamp {rec.timestamp.isoformat()} is in the future — skipped")
        return True

    if not _position_ok(rec):
        log(f"🚨 REJECTED invalid location from {imei} — lat={rec.latitude} lon={rec.longitude} "
            f"speed={rec.speed} heading={rec.angle}")
        return True

    try:
        await save_location(
            device_id=device_id,
            latitude=rec.latitude,
            longitude=rec.longitude,
            speed=rec.speed,
            heading=rec.angle,
            satellites=rec.satellites,
            event_time=rec.timestamp,
        )
    except Exception as ex:
        log(f"❌ Teltonika location save failed for {imei}: {ex}")
        return False

    debug(f"📍 Teltonika {imei}: {rec.latitude:.6f},{rec.longitude:.6f} {rec.speed} km/h {rec.timestamp.isoformat()}")
    return True


async def _refresh_status(device_id: str, records) -> None:
    """
    Ignition from the newest record that reports it (written first, so the
    position saved right after carries the right IgnitionStatus), and a
    rate-limited LastSeen refresh.
    """
    now = time.monotonic()

    ignition = None
    for rec in sorted(records, key=lambda r: r.timestamp, reverse=True):
        if rec.ignition is not None:
            ignition = rec.ignition
            break

    if ignition is not None and _last_ignition.get(device_id) != ignition:
        await _safely("ignition update", set_ignition(device_id, ignition))
        _last_ignition[device_id] = ignition
        _last_alive[device_id] = now
        return

    if now - _last_alive.get(device_id, float("-inf")) >= _ALIVE_REFRESH_SECONDS:
        await _safely("mark alive", mark_device_alive(device_id, heartbeat=True))
        _last_alive[device_id] = now
