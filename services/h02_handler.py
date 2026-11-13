"""
H02 (LK-GPS LK208 and the "*HQ,<id>,V1,...#" family) frame handler.

Same contract as services/packet_handler.process_packet and
services/gps103_handler.process_gps103_message: tcp_server calls
process_h02_frame() once per framed unit and it returns the device id once the
connection is authenticated, else None.

Design rules, all deliberate:

* There is no login frame in this protocol: every message names the device.
  The first message on a connection authenticates it. An id that is not
  admin-activated gets the socket closed with no reply. After that the socket
  is bound to the id, and a message naming a DIFFERENT id is treated as
  spoofing and the connection is dropped.
* Replies are the ones the protocol needs (V1/NBR acknowledgement with the
  server clock, heartbeat echo) and nothing else. Unknown frames get no reply.
* Positions are stored only through tracking_service.save_location (one
  statement, correct numeric handling). A fix without usable coordinates, at
  (0, 0), outside physical bounds or stamped in the future is not stored.
* Writes are independent: a failed position write never stops an alarm event.
* Battery and signal are NOT written: their units differ between protocols, so
  they stay at the neutral placeholder until the portal defines one scale
  (docs/adding-a-protocol.md).
* Ignition and alarms come from the status word, which is decoded from a
  published reference and not yet confirmed on a physical LK208. They are OFF
  unless config.H02_STATUS_FLAGS is true. Alarm events fire once per
  transition into the alarm, not on every message while it lasts.
* Unknown message types are kept raw, always, so a new firmware variant can be
  added from a real capture instead of guessed.
* Commands: not implemented for this family. command_service refuses to write
  V5 frames to a non-GT06 socket, so a command returns 501.
"""

import time
from datetime import datetime, timedelta, timezone

import config
from constants.severity import Severity
from constants.event_types import EventType
from parsers import h02_parser as p
from protocols import H02
from repositories.device_repository import DeviceRepository
from repositories.raw_packet_repository import RawPacketRepository
from services.device_registry import register_device, get_device, get_imei_by_socket, note_position
from services.event_service import create_event
from services.raw_packet_policy import should_store_raw
from services.tracking_service import save_location, mark_device_alive, set_ignition
from utils.logger import log, debug

DEFAULT_MODEL = "H02"

HIGH_SEVERITY_ALARMS = {"SOS", "POWER_CUT"}

# A routine message proves the unit is alive; refreshing LastSeen on every one
# would add a statement per message, so it is refreshed at most this often.
# Heartbeats are rare and always refresh.
_ALIVE_REFRESH_SECONDS = 60
_last_alive: dict[str, float] = {}
_last_ignition: dict[str, bool] = {}
_last_alarm: dict[str, str | None] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe(data, limit: int = 120) -> str:
    """Printable, length-capped form of untrusted device input for log lines."""
    if isinstance(data, bytes):
        data = data.decode("ascii", errors="replace")
    return ascii(data[:limit])


def _frame_for_log(raw: bytes, limit: int = 120) -> str:
    """Untrusted frame, printable and length-capped: text escaped, binary as hex."""
    if raw[:1] == b"$":
        return raw[:limit // 2].hex()
    return _safe(raw, limit)


def _close(writer) -> None:
    try:
        writer.close()
    except Exception:
        pass


async def _send(writer, payload: bytes) -> None:
    try:
        writer.write(payload)
        await writer.drain()
        debug(f"📤 Reply sent: {payload.decode('ascii', errors='replace')}")
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
        protocol_number=H02,
        raw_hex=raw.hex(),
        parsed=parsed,
    )


def _position_ok(msg: p.H02Message) -> bool:
    """Same physical-bounds rule as the other families, plus (0, 0)."""
    if msg.latitude == 0 and msg.longitude == 0:
        return False
    return (
        -90 <= msg.latitude <= 90 and -180 <= msg.longitude <= 180
        and 0 <= msg.speed <= 300 and 0 <= msg.heading <= 360
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

async def process_h02_frame(raw: bytes, writer, addr):
    try:
        msg = p.parse_frame(raw)
    except p.H02ParseError as ex:
        log(f"❓ Unparseable H02 frame from {addr[0]}: {ex} — {_safe(raw)}")
        imei = get_imei_by_socket(writer)
        if imei is None:
            _close(writer)          # junk before any login: do not hold the slot
        return imei

    try:
        imei = get_imei_by_socket(writer)
        if imei is None:
            return await _authenticate(msg, raw, writer, addr)

        if msg.device_id != imei:
            log(f"🚨 ID MISMATCH — socket authenticated as {imei} but message claims "
                f"{msg.device_id}. Dropping connection.")
            _close(writer)
            return None

        await _handle_message(msg, raw, writer, imei, first=False)
        return imei

    except Exception as ex:
        log(f"❌ H02 PACKET ERROR [{msg.msg_type}]: {ex}")
        return get_imei_by_socket(writer)


# ---------------------------------------------------------------------------
# first message = login
# ---------------------------------------------------------------------------

async def _authenticate(msg: p.H02Message, raw, writer, addr):
    log(f"📡 H02 first message — ID: {msg.device_id} ({msg.form} {msg.msg_type})")

    device_id = await DeviceRepository.get_device_by_imei(
        msg.device_id, protocol_type=H02, default_model=DEFAULT_MODEL
    )
    if not device_id:
        hint = ""
        if len(msg.device_id) != 15:
            hint = (f" (this unit reports a {len(msg.device_id)}-digit id, not a 15-digit IMEI; "
                    "see docs/h02.md)")
        # The whole first frame goes in the log: one rejected attempt from a new
        # model then shows its form (text/binary), id, message type and status
        # word without needing RawPackets (no GpsDevices row exists to hold it).
        log(f"🚫 REJECTED — ID not admin-activated: {msg.device_id} from {addr[0]}{hint}"
            f" — first frame ({msg.form}, {msg.msg_type}): {_frame_for_log(raw)}")
        _close(writer)
        return None

    log(f"✅ Authorised Device — ID: {msg.device_id} → {device_id}")
    register_device(msg.device_id, addr[0], device_id, writer, protocol=H02)

    # Fresh connection: forget cached state so the first message refreshes it.
    _last_alive.pop(device_id, None)
    _last_ignition.pop(device_id, None)
    _last_alarm.pop(device_id, None)

    # Answer first (the unit waits for its acknowledgement), bookkeeping after.
    await _handle_message(msg, raw, writer, msg.device_id, first=True)
    return msg.device_id


async def _announce_online(device_id) -> None:
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
        metadata={"protocol": H02},
    ))


# ---------------------------------------------------------------------------
# every message
# ---------------------------------------------------------------------------

async def _handle_message(msg: p.H02Message, raw, writer, imei, first: bool) -> None:
    device_id = get_device(imei)["device_id"]

    reply = p.build_reply(msg)
    if reply:
        await _send(writer, reply)

    if first:
        await _safely("raw packet save", _save_raw(device_id, raw, always=True))
        await _announce_online(device_id)
    elif not msg.known:
        log(f"❓ Unknown H02 message type {_safe(msg.msg_type, 20)} from {imei} — raw bytes kept")
        await _safely("raw packet save", _save_raw(device_id, raw, always=True, parsed=False))
    else:
        await _safely("raw packet save", _save_raw(device_id, raw))

    position_ok = False
    if msg.kind == p.POSITION:
        if msg.fix:
            position_ok = _position_ok(msg)
            if not position_ok:
                log(f"🚨 REJECTED invalid location from {imei} — lat={msg.latitude} "
                    f"lon={msg.longitude} speed={msg.speed} heading={msg.heading}")
        else:
            debug(f"H02 {imei}: no GPS fix in {msg.msg_type} report — position skipped")

    event_time = None
    if position_ok:
        event_time = msg.timestamp or datetime.now(timezone.utc)
        if msg.timestamp is None:
            log(f"⚠️ H02 {imei}: no device timestamp in this report — using server receive time")
        latest_allowed = datetime.now(timezone.utc) + timedelta(seconds=config.H02_MAX_FUTURE_SECONDS)
        if event_time > latest_allowed:
            log(f"⚠️ H02 {imei}: timestamp {event_time.isoformat()} is in the future — position skipped")
            position_ok = False

    # Status word first, so the position saved right after carries the right ignition.
    alarm = None
    ignition = None
    if config.H02_STATUS_FLAGS and msg.status is not None:
        ignition, alarm = p.decode_status(msg.status)
        if _last_ignition.get(device_id) != ignition:
            await _safely("ignition update", set_ignition(device_id, ignition))
            _last_ignition[device_id] = ignition
            _last_alive[device_id] = time.monotonic()

    await _refresh_alive(device_id, heartbeat=msg.kind == p.HEARTBEAT)

    if position_ok:
        await _safely("location save", save_location(
            device_id=device_id,
            latitude=msg.latitude,
            longitude=msg.longitude,
            speed=msg.speed,
            heading=msg.heading,
            satellites=0,                      # H02 does not report a satellite count
            event_time=event_time,
        ))
        note_position(imei, msg.speed)
        debug(f"📍 H02 {imei}: {msg.latitude:.6f},{msg.longitude:.6f} {msg.speed} km/h {event_time.isoformat()}")

    if config.H02_STATUS_FLAGS and msg.status is not None:
        await _alarm_transition(device_id, imei, alarm, msg, position_ok)


async def _refresh_alive(device_id, heartbeat: bool) -> None:
    now = time.monotonic()
    if heartbeat or now - _last_alive.get(device_id, float("-inf")) >= _ALIVE_REFRESH_SECONDS:
        await _safely("mark alive", mark_device_alive(device_id, heartbeat=heartbeat))
        _last_alive[device_id] = now


async def _alarm_transition(device_id, imei, alarm, msg, position_ok) -> None:
    """Record an alarm event once when the unit enters the alarm state."""
    previous = _last_alarm.get(device_id)
    _last_alarm[device_id] = alarm
    if alarm is None or alarm == previous:
        return

    try:
        vehicle_id = await DeviceRepository.get_vehicle_by_device(device_id)
    except Exception as ex:
        log(f"❌ vehicle lookup failed: {ex}")
        vehicle_id = None

    severity = Severity.HIGH if alarm in HIGH_SEVERITY_ALARMS else Severity.MEDIUM
    await _safely("alarm event", create_event(
        device_id=device_id,
        vehicle_id=vehicle_id,
        event_type=alarm,
        severity=severity,
        latitude=msg.latitude if position_ok else None,
        longitude=msg.longitude if position_ok else None,
        description=f"Alarm: {alarm}",
        metadata={
            "protocol": H02,
            "status": f"{msg.status:08X}",
            "speed": msg.speed if position_ok else None,
            "gps_fix": position_ok,
        },
    ))
    log(f"🚨 Alarm event recorded: {alarm} ({imei})")
