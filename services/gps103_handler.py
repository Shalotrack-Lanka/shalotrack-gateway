"""
GPS103 (Coban TK103A/B) message handler.

The GPS103 counterpart of services/packet_handler.py (which is GT06-only).
tcp_server.py picks this pipeline per connection via protocols.detect_protocol,
then calls process_gps103_message() once per framed message. The signature and
return value match packet_handler.process_packet(): it returns the device IMEI
once the connection is authenticated, else None.

Differences from the GT06 path, all deliberate:

* Authentication is login-only and strict. A rejected login closes the socket
  and sends nothing back (GT06 ACKs unknown devices; there is no reason to
  talk to an IMEI that is not on the admin allowlist).
* Every GPS103 message carries the IMEI. Once a socket is bound to an IMEI, a
  message naming a DIFFERENT IMEI is treated as a spoofing attempt and the
  connection is dropped.
* Replies are plain ASCII: LOAD for login, ON for heartbeat, and an
  acknowledgement for SOS so the unit stops repeating the alarm.
* Writes are independent: a failed location insert never stops an SOS event
  from being recorded, and vice versa.
"""

from datetime import datetime, timezone

from constants.event_types import EventType
from constants.severity import Severity
from parsers import gps103_parser as p
from protocols import GPS103
from repositories.device_repository import DeviceRepository
from repositories.raw_packet_repository import RawPacketRepository
from services.device_registry import register_device, get_device, get_imei_by_socket
from services.event_service import create_event
from services.tracking_service import (
    save_tracking,
    update_current_location,
    mark_device_alive,
    set_ignition,
)
from utils.logger import log

DEFAULT_MODEL = "TK103"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe(text, limit: int = 120) -> str:
    """Printable, length-capped form of untrusted device input for log lines."""
    if isinstance(text, bytes):
        text = text.decode("ascii", errors="replace")
    return ascii(text[:limit])


def _close(writer) -> None:
    try:
        writer.close()
    except Exception:
        pass


async def _send(writer, payload: bytes) -> None:
    try:
        writer.write(payload)
        await writer.drain()
        log(f"📤 Reply sent: {payload.decode('ascii', errors='replace')}")
    except Exception as ex:
        log(f"❌ Reply error: {ex}")


async def _safely(label: str, coro) -> None:
    """Run one independent DB write; log and swallow its failure."""
    try:
        await coro
    except Exception as ex:
        log(f"❌ {label} failed: {ex}")


async def _save_raw(device_id, raw: bytes) -> None:
    await RawPacketRepository.save(
        device_id=device_id,
        protocol_number=GPS103,
        raw_hex=raw.hex(),
        parsed=True,
    )


def location_is_valid(lat, lon, speed, heading) -> bool:
    """
    Same physical-bounds rule as the GT06 path (see packet_handler), plus
    (0, 0): a receiver that reports a fix flagged 'A' at exactly 0,0 is
    emitting garbage, and a wrong-but-plausible position on the SOS map is
    worse than a visibly stale one.
    """
    if lat == 0 and lon == 0:
        return False
    return (
        -90 <= lat <= 90 and -180 <= lon <= 180
        and 0 <= speed <= 300 and 0 <= heading <= 360
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

async def process_gps103_message(raw: bytes, writer, addr):
    kind, text = p.classify(raw)

    try:
        if kind == p.LOGIN:
            return await _handle_login(text, raw, writer, addr)
        if kind == p.HEARTBEAT:
            return await _handle_heartbeat(text, writer)
        if kind == p.TRACKER:
            return await _handle_tracker(text, raw, writer)

        # Unframed noise or an unsupported GPS103 variant (e.g. 'imei:...*').
        # No reply: answering garbage invites a retransmit loop.
        log(f"❓ Unknown GPS103 message from {addr[0]}: {_safe(raw)}")
        return None

    except p.Gps103ParseError as ex:
        log(f"❌ Malformed GPS103 {kind} message: {ex} — {_safe(raw)}")
        return get_imei_by_socket(writer)
    except Exception as ex:
        log(f"❌ GPS103 PACKET ERROR [{kind}]: {ex}")
        return get_imei_by_socket(writer)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

async def _handle_login(text, raw, writer, addr):
    imei = p.parse_login(text)["imei"]
    log(f"📡 GPS103 login — IMEI: {imei}")

    device_id = await DeviceRepository.get_device_by_imei(
        imei, protocol_type=GPS103, default_model=DEFAULT_MODEL
    )

    if not device_id:
        log(f"🚫 REJECTED — IMEI not admin-activated: {imei} from {addr[0]}")
        _close(writer)
        return None

    log(f"✅ Authorised Device — IMEI: {imei} → {device_id}")
    register_device(imei, addr[0], device_id, writer, protocol=GPS103)

    # The unit waits for LOAD before it starts reporting: answer first, do the
    # bookkeeping after. Nothing below may stop the reply or the registration.
    await _send(writer, p.LOGIN_REPLY)

    await _safely("raw packet save", _save_raw(device_id, raw))
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
        metadata={"protocol": GPS103},
    ))

    return imei


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------

async def _handle_heartbeat(text, writer):
    imei = p.parse_heartbeat(text)["imei"]
    connection_imei = _authenticated_imei(writer, imei)
    if not connection_imei:
        return None

    await _send(writer, p.HEARTBEAT_REPLY)

    # No raw-packet row: a GPS103 heartbeat is only the IMEI, so storing it
    # would add a row per beat per device and nothing to replay.
    device = get_device(connection_imei)
    await _safely("heartbeat update", mark_device_alive(device["device_id"], heartbeat=True))
    return connection_imei


# ---------------------------------------------------------------------------
# position / alarm
# ---------------------------------------------------------------------------

async def _handle_tracker(text, raw, writer):
    data = p.parse_tracker(text)
    imei = _authenticated_imei(writer, data["imei"])
    if not imei:
        return None

    device = get_device(imei)
    device_id = device["device_id"]
    alarm = data["alarm_name"]

    if not data["known_keyword"]:
        log(f"ℹ️ Unrecognised GPS103 keyword {_safe(data['keyword'], 40)} from {imei} — treated as a position report")

    # Decide up-front whether the position is storable; both the location
    # write and the alarm event use the same verdict.
    position_ok = False
    if data["valid_fix"]:
        position_ok = location_is_valid(
            data["latitude"], data["longitude"], data["speed"], data["heading"]
        )
        if not position_ok:
            log(
                f"🚨 REJECTED invalid location — lat={data['latitude']} lon={data['longitude']} "
                f"speed={data['speed']} heading={data['heading']}"
            )
    else:
        log(f"ℹ️ No GPS fix in report from {imei} (fix_type={data['fix_type']}) — position skipped")

    # SOS first: the unit repeats the alarm until it is acknowledged, and a
    # person may be waiting on it. Cheap socket write, no DB involved.
    if alarm == "SOS":
        await _send(writer, p.build_help_me_ack(imei))

    await _safely("raw packet save", _save_raw(device_id, raw))

    if data["ignition"] is not None:
        await _safely("ignition update", set_ignition(device_id, data["ignition"]))

    vehicle_id = None
    if position_ok or alarm:
        try:
            vehicle_id = await DeviceRepository.get_vehicle_by_device(device_id)
        except Exception as ex:
            log(f"❌ vehicle lookup failed: {ex}")

    if position_ok:
        event_time = data["timestamp"] or datetime.now(timezone.utc)
        if data["timestamp"] is None:
            log("⚠️ Could not derive a device timestamp — using server receive time")

        await _safely("tracking save", save_tracking(
            device_id=device_id,
            latitude=data["latitude"],
            longitude=data["longitude"],
            speed=data["speed"],
            heading=data["heading"],
            satellites=0,                      # GPS103 does not report satellite count
            event_time=event_time,
        ))
        if vehicle_id:
            await _safely("current location update", update_current_location(
                device_id=device_id,
                vehicle_id=vehicle_id,
                latitude=data["latitude"],
                longitude=data["longitude"],
                speed=data["speed"],
                heading=data["heading"],
                event_time=event_time,
            ))
        else:
            log("ℹ️ Device not assigned to a vehicle. Skipping CurrentLocation update.")

    if alarm:
        severity = Severity.HIGH if alarm in p.HIGH_SEVERITY_ALARMS else Severity.MEDIUM
        await _safely("alarm event", create_event(
            device_id=device_id,
            vehicle_id=vehicle_id,
            event_type=alarm,
            severity=severity,
            latitude=data["latitude"] if position_ok else None,
            longitude=data["longitude"] if position_ok else None,
            description=f"Alarm: {alarm}",
            metadata={
                "protocol": GPS103,
                "keyword": data["keyword"],
                "speed": data["speed"] if position_ok else None,
                "ignition": data["ignition"],
                "gps_fix": position_ok,
            },
        ))
        log(f"🚨 Alarm event recorded: {alarm}")

    return imei


# ---------------------------------------------------------------------------
# connection binding
# ---------------------------------------------------------------------------

def _authenticated_imei(writer, message_imei):
    """
    Return the IMEI this socket logged in with, if the message belongs to it.

    Closes the connection (forcing a clean re-login) when the socket never
    logged in, or when the message names a different IMEI than the one the
    socket authenticated as.
    """
    connection_imei = get_imei_by_socket(writer)

    if not connection_imei:
        log("❌ GPS103 message before login — connection closed to force re-login")
        _close(writer)
        return None

    if message_imei != connection_imei:
        log(
            f"🚨 IMEI MISMATCH — socket authenticated as {connection_imei} "
            f"but message claims {message_imei}. Dropping connection."
        )
        _close(writer)
        return None

    return connection_imei
