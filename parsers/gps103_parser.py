"""
GPS103 text protocol parser (Coban TK103A/B family).

Pure functions only: bytes/str in, dicts out. No sockets, no database, no
asyncio, so every rule here is unit-testable (see test/test_gps103_parser.py).

Wire format (ASCII, every message terminated by ';')
----------------------------------------------------
    Login      ##,imei:359586015829802,A;        server must reply  LOAD
    Heartbeat  359586015829802;                  server must reply  ON
    Position   imei:<id>,<keyword>,<yymmddhhmm[ss]>,<rfid|batt>,F,<hhmmss.sss>,A,
               <ddmm.mmmm>,N,<dddmm.mmmm>,E,<speed>,<course>,<alt>,<acc>,<door>,...;
    LBS only   imei:<id>,<keyword>,<yymmddhhmm>,,L,...;   (no GPS fix, no coords)

Field notes
-----------
* <keyword> is "tracker" for a normal periodic report, otherwise an alarm
  ("help me", "low battery", "stockade", "move", "speed", "ac alarm",
  "acc on", "acc off", ...).
* The date/time right after the keyword is the device's LOCAL time. The UTC
  time-of-day is inside the F block. We rebuild a UTC timestamp from the two,
  wrapping the offset into +/-12h, so it is correct for any timezone and
  across midnight.
* Speed is in knots (see config.GPS103_SPEED_IN_KMH).
* Latitude/longitude are ddmm.mmmm / dddmm.mmmm (degrees + decimal minutes),
  NOT decimal degrees.

Reference: Traccar's open-source gps103 decoder and its published sample
messages. The original vendor document is a binary .doc that could not be
retrieved from this environment; confirm against the unit's own datasheet.
"""

import re
from datetime import datetime, timedelta, timezone

from config import GPS103_MAX_UNFRAMED_BYTES, GPS103_SPEED_IN_KMH

KNOTS_TO_KMH = 1.852

# Message kinds returned by classify()
LOGIN = "login"
HEARTBEAT = "heartbeat"
TRACKER = "tracker"
UNKNOWN = "unknown"

_IMEI_RE = re.compile(r"^\d{10,17}$")
_LOGIN_IMEI_RE = re.compile(r"imei:(\d{10,17})")

# keyword (lower-case) -> event name. None means "plain position report".
# Names reuse the gateway's existing vocabulary (see v5_parser.get_alarm_name)
# so the admin portal and mobile app show the same labels for both device types.
ALARM_NAMES = {
    "tracker": None,
    "": None,
    "help me": "SOS",
    "low battery": "LOW_BATTERY",
    "stockade": "GEOFENCE",
    "move": "MOVE",
    "speed": "OVERSPEED",
    "ac alarm": "POWER_CUT",
    "door alarm": "DOOR_OPEN",
    "sensor alarm": "SENSOR",
    "acc on": "ACC_ON",
    "acc off": "ACC_OFF",
}

HIGH_SEVERITY_ALARMS = {"SOS", "POWER_CUT"}


class Gps103ParseError(ValueError):
    """A message looked like GPS103 but could not be decoded."""


class FrameOverflowError(Exception):
    """
    Too many bytes arrived with no ';' terminator. The stream is garbage or an
    attack and cannot be resynchronised reliably (we would not know where the
    junk ends), so the caller must drop the connection. A real device simply
    reconnects and logs in again.
    """


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def extract_messages(buffer: bytes):
    """
    Split a TCP stream buffer into complete GPS103 messages.

    Returns (messages, remaining_buffer). Messages are returned without the
    trailing ';' and with surrounding whitespace/CRLF stripped. Same contract
    as utils.packet_buffer.extract_packets so tcp_server can treat both
    framers identically.

    Raises FrameOverflowError when the unterminated remainder exceeds
    config.GPS103_MAX_UNFRAMED_BYTES.
    """
    messages = []

    while True:
        idx = buffer.find(b";")
        if idx == -1:
            break
        raw, buffer = buffer[:idx], buffer[idx + 1:]
        raw = raw.strip()
        if raw:
            messages.append(raw)

    if len(buffer) > GPS103_MAX_UNFRAMED_BYTES:
        raise FrameOverflowError(
            f"{len(buffer)} bytes without a ';' terminator (limit {GPS103_MAX_UNFRAMED_BYTES})"
        )

    return messages, buffer


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(raw: bytes):
    """
    Decide what a framed message is.

    Returns (kind, text). kind is LOGIN / HEARTBEAT / TRACKER / UNKNOWN.
    Non-ASCII input is UNKNOWN; we never guess at binary garbage.
    """
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return UNKNOWN, ""

    text = text.strip()

    if text.startswith("##") and "imei:" in text:
        return LOGIN, text
    if _IMEI_RE.match(text):
        return HEARTBEAT, text
    if text.startswith("imei:"):
        return TRACKER, text
    return UNKNOWN, text


def parse_login(text: str) -> dict:
    match = _LOGIN_IMEI_RE.search(text)
    if not match:
        raise Gps103ParseError("login without a valid IMEI")
    return {"imei": match.group(1)}


def parse_heartbeat(text: str) -> dict:
    if not _IMEI_RE.match(text):
        raise Gps103ParseError("heartbeat is not a bare IMEI")
    return {"imei": text}


# ---------------------------------------------------------------------------
# Position / alarm report
# ---------------------------------------------------------------------------

def _field(fields, index):
    """fields[index] stripped, or '' when the message is shorter than that."""
    return fields[index].strip() if index < len(fields) else ""


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nmea_to_degrees(value: str, hemisphere: str):
    """
    ddmm.mmmm (lat) or dddmm.mmmm (lon) -> signed decimal degrees.

    The last two digits before the decimal point are whole minutes, so the
    same code handles both widths. Returns None on anything malformed.
    """
    if not value or hemisphere not in ("N", "S", "E", "W"):
        return None
    head, _, fraction = value.partition(".")
    if not head.isdigit() or len(head) < 3 or (fraction and not fraction.isdigit()):
        return None
    degrees = int(head[:-2])
    minutes = float(head[-2:] + ("." + fraction if fraction else ""))
    if minutes >= 60:
        return None
    result = degrees + minutes / 60.0
    return -result if hemisphere in ("S", "W") else result


def _parse_local_datetime(value: str):
    """yymmddhhmm or yymmddhhmmss -> naive datetime, or None."""
    if not value.isdigit() or len(value) not in (10, 12):
        return None
    try:
        return datetime(
            2000 + int(value[0:2]), int(value[2:4]), int(value[4:6]),
            int(value[6:8]), int(value[8:10]),
            int(value[10:12]) if len(value) == 12 else 0,
        )
    except ValueError:
        return None


def _parse_utc_time(value: str):
    """hhmmss or hhmmss.sss -> (h, m, s), or None."""
    head = value.split(".")[0]
    if not head.isdigit() or len(head) != 6:
        return None
    h, m, s = int(head[0:2]), int(head[2:4]), int(head[4:6])
    if h > 23 or m > 59 or s > 59:
        return None
    return h, m, s


def resolve_utc_timestamp(local_value: str, utc_value: str):
    """
    Build an aware UTC datetime from the device-local date/time and the UTC
    time-of-day. Returns None when either is missing/malformed (caller falls
    back to server receive time).

    offset = local - utc, wrapped into (-12h, +12h], so a +05:30 device at
    00:10 local (18:40 UTC the previous day) resolves to the right UTC date.
    """
    local = _parse_local_datetime(local_value)
    utc = _parse_utc_time(utc_value)
    if local is None or utc is None:
        return None

    local_minutes = local.hour * 60 + local.minute
    utc_minutes = utc[0] * 60 + utc[1]
    offset = local_minutes - utc_minutes
    while offset > 720:
        offset -= 1440
    while offset <= -720:
        offset += 1440

    # Take the date/time from the local clock, shift by the offset, then use
    # the UTC seconds field (the local string often has no seconds).
    shifted = local.replace(second=0) - timedelta(minutes=offset)
    shifted = shifted.replace(second=utc[2])
    return shifted.replace(tzinfo=timezone.utc)


def parse_tracker(text: str) -> dict:
    """
    Parse an 'imei:...' position / alarm report.

    Always returns the same keys. latitude/longitude/speed/heading are None
    when the report has no usable GPS fix (LBS-only 'L' reports, or 'V').
    `valid_fix` is True only for an 'F' report flagged 'A'.
    """
    if not text.startswith("imei:"):
        raise Gps103ParseError("not a tracker message")

    fields = text.split(",")

    imei = fields[0][len("imei:"):].strip()
    if not _IMEI_RE.match(imei):
        raise Gps103ParseError("invalid IMEI in tracker message")

    keyword = _field(fields, 1).lower()
    local_time = _field(fields, 2)
    fix_type = _field(fields, 4).upper()          # 'F' = GPS fix block, 'L' = LBS only

    result = {
        "imei": imei,
        "keyword": keyword,
        "alarm_name": ALARM_NAMES.get(keyword),
        "known_keyword": keyword in ALARM_NAMES,
        "fix_type": fix_type or None,
        "valid_fix": False,
        "timestamp": None,
        "latitude": None,
        "longitude": None,
        "speed": None,
        "heading": None,
        "ignition": None,
    }

    if keyword == "acc on":
        result["ignition"] = True
    elif keyword == "acc off":
        result["ignition"] = False

    if fix_type != "F":
        return result

    utc_value = _field(fields, 5)
    gps_flag = _field(fields, 6).upper()
    latitude = _nmea_to_degrees(_field(fields, 7), _field(fields, 8).upper())
    longitude = _nmea_to_degrees(_field(fields, 9), _field(fields, 10).upper())

    if gps_flag != "A" or latitude is None or longitude is None:
        return result

    speed = _to_float(_field(fields, 11))
    heading = _to_float(_field(fields, 12))

    if speed is not None and not GPS103_SPEED_IN_KMH:
        speed = speed * KNOTS_TO_KMH

    result.update(
        valid_fix=True,
        timestamp=resolve_utc_timestamp(local_time, utc_value),
        latitude=latitude,
        longitude=longitude,
        speed=round(speed, 2) if speed is not None else 0.0,
        heading=heading if heading is not None else 0.0,
    )

    # Ignition flag sits after altitude: speed, course, altitude, acc, door...
    acc_flag = _field(fields, 14)
    if acc_flag in ("0", "1") and result["ignition"] is None:
        result["ignition"] = acc_flag == "1"

    return result


def build_help_me_ack(imei: str) -> bytes:
    """Reply that stops a device repeating its SOS ('help me') alarm."""
    return f"**,imei:{imei},E;".encode("ascii")


LOGIN_REPLY = b"LOAD"
HEARTBEAT_REPLY = b"ON"