"""
H02 protocol parser (LK-GPS LK208 and the "*HQ,<id>,V1,...#" device family).

Pure functions only: bytes in, plain objects out. No sockets, no database, no
asyncio, so every rule here is unit-testable (see test/test_h02_parser.py).

Two wire forms exist and a device uses one of them for its whole life:

Text (ASCII, one message per frame, terminated by '#')
    *HQ,<id>,V1,<hhmmss>,<A|V>,<lat>,<N|S>,<lon>,<E|W>,<speed>,<course>,<ddmmyy>,<status>#
    *HQ,<id>,HTBT,<battery>#                      heartbeat
    *HQ,<id>,NBR|V3|LINK|...#                     cell-tower / status reports (no coordinates)

Binary (starts with '$', fixed length, BCD digits)
    $ <id:5> <hhmmss:3> <ddmmyy:3> <lat:4> <battery:1> <lon:5 + flags> <speed+course:3> <status:4> ...

There is NO separate login frame: the device announces itself with <id> in
every message, so the handler authenticates on the first message.

Field notes
-----------
* <id> is whatever the unit was programmed with: a 15-digit IMEI on some
  models, a 10-digit device number on others. The ERP only accepts 15-digit
  IMEIs today (docs/h02.md).
* Date/time are GPS time = UTC. Year is two digits (20yy).
* Coordinates come in three spellings seen in the field:
      ddmm.mmmm / dddmm.mmmm          (usual; the leading degrees may be absent
                                       below 1 degree, "9.9068" = 0 deg 9.9068')
      -dd-mm.mmmm                     (dashed)
      ddmmmmmm / dddmmmmmm            (no decimal point, last 4 digits = minutes fraction)
* Speed is in knots (see config.H02_SPEED_IN_KMH), course in degrees.
* <status> is 8 hex digits; a CLEARED bit means "active". See decode_status().

Reference: Traccar's open-source H02 decoder, its frame decoder and the sample
messages in its test suite (real captures from many H02 models). No vendor
datasheet for the LK208 itself was available: confirm against a real unit
(docs/h02.md) before relying on anything marked "verify".
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from config import H02_MAX_UNFRAMED_BYTES, H02_SPEED_IN_KMH

KNOTS_TO_KMH = 1.852

# Wire form
TEXT = "text"
BINARY = "binary"

# Message kinds
POSITION = "position"      # carries coordinates (fix may still be False)
HEARTBEAT = "heartbeat"    # HTBT / V0
OTHER = "other"            # cell-tower, status, SMS, unknown types: proves the unit is alive

# Text message types this parser knows. Anything else is OTHER and its raw
# bytes are always kept, so a new variant can be added from a real capture.
_POSITION_TYPES = {"V1", "V2", "V4", "V6", "V8", "VI1", "V19", "VP1"}
_HEARTBEAT_TYPES = {"V0", "HTBT"}
_KNOWN_OTHER_TYPES = {"NBR", "V3", "LINK", "SMS", "XT", "BS", "BASE"}

# Binary frame sizes seen in real captures (short 32; long 42 with an 8-byte
# id; 45/46/48 with extra tail data). The format has no length field.
BINARY_LENGTHS = (32, 42, 45, 46, 48)
_BINARY_MIN = 32
_BINARY_MAX = max(BINARY_LENGTHS)

_MFR_RE = re.compile(r"^[A-Za-z]{2}$")
_ID_RE = re.compile(r"^\d{10,17}$")
_DOTTED_RE = re.compile(r"^(\d*?)(\d{1,2}\.\d+)$")
_DASHED_RE = re.compile(r"^-(\d+)-(\d+(?:\.\d+)?)$")
_PLAIN_RE = re.compile(r"^(\d+)(\d{2})(\d{4})$")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d*)?$")
_STATUS_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


class H02ParseError(ValueError):
    """A frame looked like H02 but could not be decoded (or names no usable id)."""


class H02FrameError(Exception):
    """
    The stream cannot be H02 or cannot be resynchronised (unexpected byte,
    oversized unterminated frame). The caller drops the connection; a real
    device just reconnects.
    """


@dataclass(frozen=True)
class H02Message:
    device_id: str
    form: str                    # TEXT / BINARY
    msg_type: str                # "V1", "HTBT", "NBR", ... ("BIN" for binary)
    kind: str                    # POSITION / HEARTBEAT / OTHER
    known: bool                  # False for a type this parser does not know
    fix: bool = False
    latitude: float | None = None
    longitude: float | None = None
    speed: float | None = None   # km/h
    heading: float | None = None
    timestamp: datetime | None = None
    status: int | None = None
    prefix: str = "HQ"           # 2-letter maker code as the unit wrote it
    echo: str | None = None      # exact "*HQ,<id>,<type>" the unit used, for the heartbeat reply


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def _binary_length(buf: bytes) -> int | None:
    """
    Length of the binary frame at the start of `buf`, or None when more bytes
    are needed. The format has no length field, so this is deliberate and
    conservative: a buffer that is exactly one known size is one frame (a unit
    sends one frame per TCP write); a longer buffer is split where the next
    frame marker sits. Anything else is waited on, or rejected when it can no
    longer be a prefix of a valid stream.
    """
    n = len(buf)
    if n < _BINARY_MIN:
        return None
    if n in BINARY_LENGTHS:
        return n
    for length in BINARY_LENGTHS:
        if n > length and buf[length] in (0x24, 0x2A):
            return length
    if n <= _BINARY_MAX:
        return None                     # a partial frame: wait for the rest
    raise H02FrameError(f"cannot frame {n} bytes of binary data")


def extract_frames(buffer: bytes):
    """
    Split a TCP stream buffer into complete H02 frames.

    Returns (frames, remaining_buffer). Same contract as the other families'
    framers so tcp_server treats them identically. Text frames are returned
    whole, including '*' and '#'. Raises H02FrameError on a stream that
    cannot be H02 or that exceeds the size limits (never buffers unboundedly).
    """
    frames = []

    while buffer:
        first = buffer[0]

        if first in (0x0D, 0x0A):                 # CR/LF between frames
            buffer = buffer[1:]
            continue

        if first == 0x2A:                         # '*' text frame
            end = buffer.find(b"#")
            if end == -1:
                break
            frames.append(buffer[:end + 1])
            buffer = buffer[end + 1:]
            continue

        if first == 0x24:                         # '$' binary frame
            length = _binary_length(buffer)
            if length is None:
                break
            frames.append(buffer[:length])
            buffer = buffer[length:]
            continue

        raise H02FrameError(f"unexpected byte 0x{first:02x} where a frame should start")

    if len(buffer) > H02_MAX_UNFRAMED_BYTES:
        raise H02FrameError(
            f"{len(buffer)} bytes without a frame terminator (limit {H02_MAX_UNFRAMED_BYTES})"
        )
    return frames, buffer


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _timestamp(hour: int, minute: int, second: int, day: int, month: int, year2: int):
    try:
        return datetime(2000 + year2, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def _speed_kmh(raw_speed: float) -> float:
    return round(raw_speed if H02_SPEED_IN_KMH else raw_speed * KNOTS_TO_KMH, 2)


def decode_status(status: int):
    """
    Decode the 8-hex-digit status word -> (ignition, alarm_name_or_None).

    Per the published reference decoder: a CLEARED bit means "active".
    bit 0 vibration, bit 1 or 18 SOS, bit 2 overspeed, bit 19 power cut
    (checked in that order, first match wins); bit 10 SET means ignition on.
    Names reuse the gateway's alarm vocabulary (v5_parser.get_alarm_name).
    """
    def bit(n):
        return bool((status >> n) & 1)

    if not bit(0):
        alarm = "VIBRATION"
    elif not bit(1) or not bit(18):
        alarm = "SOS"
    elif not bit(2):
        alarm = "OVERSPEED"
    elif not bit(19):
        alarm = "POWER_CUT"
    else:
        alarm = None
    return bit(10), alarm


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _to_degrees(value: str, hemisphere: str):
    """One of the three coordinate spellings + hemisphere -> signed degrees, or None."""
    value = value.strip()
    hemisphere = hemisphere.strip().upper()
    if hemisphere not in ("N", "S", "E", "W"):
        return None

    match = _DASHED_RE.match(value)
    if match:
        degrees, minutes = int(match.group(1)), float(match.group(2))
    else:
        match = _DOTTED_RE.match(value)
        if match:
            degrees, minutes = int(match.group(1) or 0), float(match.group(2))
        else:
            match = _PLAIN_RE.match(value)
            if not match:
                return None
            degrees, minutes = int(match.group(1)), float(f"{match.group(2)}.{match.group(3)}")

    if minutes >= 60:
        return None
    result = degrees + minutes / 60.0
    return -result if hemisphere in ("S", "W") else result


def _anchor(fields):
    """Index of the latitude hemisphere field ('N'/'S' followed by lon, 'E'/'W'), or None."""
    for i in range(4, len(fields) - 2):
        if fields[i].strip().upper() in ("N", "S") and fields[i + 2].strip().upper() in ("E", "W"):
            return i
    return None


def _parse_position_fields(fields):
    """
    Decode the position tail of a text message. Returns a dict with fix /
    latitude / longitude / speed / heading / timestamp / status. A message
    without usable coordinates ("V1,,V,,N,,E,...") yields fix False and no
    coordinates: it still proves the unit is alive.
    """
    out = dict(fix=False, latitude=None, longitude=None, speed=None, heading=None,
               timestamp=None, status=None)

    a = _anchor(fields)
    if a is None:
        # No hemisphere letters: either an LBS-only report or a no-fix report
        # with zeroed coordinates. Still pick up the status word if present.
        for field in reversed(fields):
            if _STATUS_RE.match(field.strip()):
                out["status"] = int(field.strip(), 16)
                break
        return out

    validity = fields[a - 2].strip().upper()
    lat = _to_degrees(fields[a - 1], fields[a])
    lon = _to_degrees(fields[a + 1], fields[a + 2])

    # 'A' = valid fix. A numeric "coding scheme" in that slot also means a
    # fix (reference decoder). 'V', 'B' and empty mean no fix.
    has_fix = validity == "A" or validity.isdigit()

    speed = heading = None
    e = a + 2                                   # index of the longitude hemisphere
    if e + 1 < len(fields) and _NUMBER_RE.match(fields[e + 1].strip()):
        speed = float(fields[e + 1].strip())
    if e + 2 < len(fields) and _NUMBER_RE.match(fields[e + 2].strip()):
        heading = float(fields[e + 2].strip())

    # After course: optional battery digits (1-3), then ddmmyy, then status.
    date = None
    nxt = e + 3
    tail = [f.strip() for f in fields[nxt:nxt + 2]]
    if tail and re.fullmatch(r"\d{6}", tail[0]):
        date, nxt = tail[0], nxt + 1
    elif len(tail) == 2 and re.fullmatch(r"\d{1,3}", tail[0]) and re.fullmatch(r"\d{6}", tail[1]):
        date, nxt = tail[1], nxt + 2

    if nxt < len(fields) and _STATUS_RE.match(fields[nxt].strip()):
        out["status"] = int(fields[nxt].strip(), 16)

    clock = fields[a - 3].strip() if a - 3 >= 3 else ""
    stamp = None
    if date and re.fullmatch(r"\d{6}", clock):
        stamp = _timestamp(int(clock[0:2]), int(clock[2:4]), int(clock[4:6]),
                           int(date[0:2]), int(date[2:4]), int(date[4:6]))

    out["timestamp"] = stamp
    if has_fix and lat is not None and lon is not None:
        out.update(fix=True, latitude=lat, longitude=lon,
                   speed=_speed_kmh(speed) if speed is not None else 0.0,
                   heading=heading if heading is not None else 0.0)
    return out


def _parse_text(raw: bytes) -> H02Message:
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise H02ParseError("text frame is not ASCII") from None
    if not (text.startswith("*") and text.endswith("#")):
        raise H02ParseError("text frame must start with '*' and end with '#'")

    fields = text[1:-1].split(",")
    if len(fields) < 3 or not _MFR_RE.match(fields[0]):
        raise H02ParseError("not an H02 header (*XX,<id>,<type>)")
    ident = fields[1].strip()
    if not _ID_RE.match(ident):
        raise H02ParseError("frame carries no usable device id")

    msg_type = fields[2].strip().upper()
    base = dict(device_id=ident, form=TEXT, msg_type=msg_type, prefix=fields[0].upper(),
                echo=f"*{fields[0]},{ident},{fields[2].strip()}")

    if msg_type in _HEARTBEAT_TYPES:
        return H02Message(kind=HEARTBEAT, known=True, **base)

    if msg_type in _POSITION_TYPES or msg_type.startswith("V") and msg_type not in _KNOWN_OTHER_TYPES:
        pos = _parse_position_fields(fields)
        return H02Message(kind=POSITION, known=msg_type in _POSITION_TYPES, **pos, **base)

    return H02Message(kind=OTHER, known=msg_type in _KNOWN_OTHER_TYPES, **base)


# ---------------------------------------------------------------------------
# Binary
# ---------------------------------------------------------------------------

def _bcd_byte(value: int) -> int:
    hi, lo = value >> 4, value & 0x0F
    if hi > 9 or lo > 9:
        raise H02ParseError("non-BCD byte in a field that must be decimal")
    return hi * 10 + lo


def _digit(nibble: int) -> int:
    if nibble > 9:
        raise H02ParseError("non-BCD nibble in a field that must be decimal")
    return nibble


def _parse_binary(raw: bytes) -> H02Message:
    n = len(raw)
    if raw[:1] != b"$" or n < _BINARY_MIN:
        raise H02ParseError("not a binary H02 frame")

    if n == 42:                                  # long form: 8-byte id, first 15 digits
        ident, base = raw[1:9].hex()[:15], 9
    else:
        ident, base = raw[1:6].hex(), 6
    if not _ID_RE.match(ident):
        raise H02ParseError("binary frame carries no usable device id")

    f = raw
    hour, minute, second = (_bcd_byte(f[base + k]) for k in range(3))
    day, month, year = (_bcd_byte(f[base + 3 + k]) for k in range(3))
    stamp = _timestamp(hour, minute, second, day, month, year)
    if stamp is None:
        raise H02ParseError("binary frame has an impossible date/time")

    lat_deg = _bcd_byte(f[base + 6])
    lat_min = (_bcd_byte(f[base + 7]) * 10000 + _bcd_byte(f[base + 8]) * 100
               + _bcd_byte(f[base + 9])) / 10000.0

    a, b, c, d, e = f[base + 11:base + 16]       # base + 10 is the battery byte
    lon_deg = _bcd_byte(a) * 10 + _digit(b >> 4)
    lon_min = _digit(b & 0x0F) * 10 + (
        _bcd_byte(c) * 1000 + _bcd_byte(d) * 10 + _digit(e >> 4)) / 10000.0
    flags = e & 0x0F

    s1, s2, s3 = f[base + 16:base + 19]
    speed = _bcd_byte(s1) * 10 + _digit(s2 >> 4)
    heading = _digit(s2 & 0x0F) * 100 + _bcd_byte(s3)
    status = int.from_bytes(f[base + 19:base + 23], "big")

    if lat_min >= 60 or lon_min >= 60:
        raise H02ParseError("binary frame has minutes >= 60")

    lat = lat_deg + lat_min / 60.0
    lon = lon_deg + lon_min / 60.0
    if not flags & 0x04:
        lat = -lat
    if not flags & 0x08:
        lon = -lon

    return H02Message(
        device_id=ident, form=BINARY, msg_type="BIN", kind=POSITION, known=True,
        fix=bool(flags & 0x02), latitude=lat, longitude=lon,
        speed=_speed_kmh(float(speed)), heading=float(heading),
        timestamp=stamp, status=status,
    )


def parse_frame(raw: bytes) -> H02Message:
    """Decode one framed unit (see extract_frames). Raises H02ParseError."""
    if raw[:1] == b"*":
        return _parse_text(raw)
    if raw[:1] == b"$":
        return _parse_binary(raw)
    raise H02ParseError("frame starts with neither '*' nor '$'")


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------

def build_reply(msg: H02Message, now: datetime | None = None) -> bytes | None:
    """
    What the server must send back, or None.

    V1 and NBR are acknowledged with the server's UTC time (this is also the
    unit's time sync); a heartbeat is echoed back. Everything else, and every
    binary frame, gets no reply: answering something we do not understand
    invites a retransmit loop. The maker code is always "HQ" in a reply, as in
    the reference implementation.
    """
    if msg.form != TEXT:
        return None
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    if msg.msg_type == "V1" or msg.msg_type == "NBR":
        return f"*HQ,{msg.device_id},V4,{msg.msg_type},{stamp}#".encode("ascii")
    if msg.kind == HEARTBEAT and msg.echo:
        return (msg.echo + "#").encode("ascii")
    return None
