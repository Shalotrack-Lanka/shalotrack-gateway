"""
Teltonika (FMB series) TCP parser: Codec 8 and Codec 8 Extended.

Pure functions only: bytes in, plain objects out. No sockets, no database, no
asyncio, so every rule is unit-testable (test/test_teltonika_parser.py).

Wire format (all integers big-endian)
-------------------------------------
Login (device -> server, once per connection)
    00 0F | IMEI as 15 ASCII digits                       17 bytes
    server replies 01 (accept) or closes the socket (reject)

AVL data packet (device -> server)
    00 00 00 00                preamble
    DataFieldLength  (4)       bytes from CodecID to NumberOfData2 inclusive
    CodecID          (1)       0x08 = Codec 8, 0x8E = Codec 8 Extended
    NumberOfData1    (1)       record count
    AVL records ...
    NumberOfData2    (1)       must equal NumberOfData1
    CRC-16           (4)       CRC-16/IBM (ARC) of the data field, upper 2 bytes 0
    server replies with the number of records it accepted as a 4-byte integer.
    Replying with fewer than were sent makes the device resend the rest, which
    is how a failed database write loses nothing.

AVL record
    Timestamp (8)  milliseconds since the Unix epoch, UTC
    Priority  (1)
    GPS element (15): longitude(4, signed) latitude(4, signed) altitude(2,
        signed) angle(2) satellites(1) speed(2, km/h). Coordinates are degrees
        x 10,000,000, two's complement for the southern / western hemisphere.
        No fix is reported as all zeros with 0 satellites.
    IO element: event IO id, total IO count, then groups of 1/2/4/8-byte
        values (Codec 8E adds a variable-length group and uses 2-byte ids and
        counts everywhere).

Reference: Teltonika's public protocol documentation (wiki.teltonika-gps.com,
"Codec"). The packet in test/test_teltonika_parser.py is the example published
there. NOT yet verified against a physical FMB unit.
"""

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import TELTONIKA_MAX_PACKET_BYTES

CODEC_8 = 0x08
CODEC_8E = 0x8E
SUPPORTED_CODECS = (CODEC_8, CODEC_8E)

LOGIN_ACCEPT = b"\x01"

# Standard FMB AVL IO ids (configurable per device in the Teltonika
# Configurator; these are the factory meanings).
IO_GSM_SIGNAL = 21
IO_EXTERNAL_VOLTAGE_MV = 66
IO_BATTERY_VOLTAGE_MV = 67
IO_BATTERY_LEVEL = 113
IO_IGNITION = 239
IO_MOVEMENT = 240


class TeltonikaError(Exception):
    """Base class for everything this parser raises."""


class TeltonikaFrameError(TeltonikaError):
    """The byte stream cannot be a Teltonika stream (drop the connection)."""


class TeltonikaParseError(TeltonikaError):
    """A complete frame whose contents are structurally invalid."""


class TeltonikaCrcError(TeltonikaParseError):
    """CRC mismatch. The packet is not acknowledged so the device resends it."""


class UnsupportedCodecError(TeltonikaParseError):
    """A valid frame in a codec this gateway does not decode (e.g. Codec 16)."""

    def __init__(self, codec: int):
        super().__init__(f"unsupported codec 0x{codec:02X}")
        self.codec = codec


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AvlRecord:
    timestamp: datetime
    priority: int
    latitude: float
    longitude: float
    altitude: int
    angle: int
    satellites: int
    speed: int                      # km/h
    event_io_id: int
    io: dict = field(default_factory=dict)   # io id -> int (bytes for Codec 8E variable-length)

    @property
    def has_fix(self) -> bool:
        """A usable position: satellites in view and not the (0, 0) no-fix marker."""
        return self.satellites > 0 and not (self.latitude == 0 and self.longitude == 0)

    def _flag(self, io_id):
        value = self.io.get(io_id)
        return None if value is None or isinstance(value, bytes) else bool(value)

    @property
    def ignition(self):
        return self._flag(IO_IGNITION)

    @property
    def movement(self):
        return self._flag(IO_MOVEMENT)


@dataclass
class AvlPacket:
    codec: int
    records: list


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

def crc16_ibm(data: bytes) -> int:
    """CRC-16/IBM, also called ARC: polynomial 0xA001 reflected, init 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

LOGIN_FRAME_LEN = 17
_PREAMBLE = b"\x00\x00\x00\x00"
_LOGIN_PREFIX = b"\x00\x0f"


def is_login_frame(frame: bytes) -> bool:
    return len(frame) == LOGIN_FRAME_LEN and frame[:2] == _LOGIN_PREFIX


def extract_frames(buffer: bytes):
    """
    Split a TCP stream buffer into complete Teltonika frames (login frames and
    AVL packets). Returns (frames, remaining_buffer), the same contract as the
    other framers, so tcp_server treats all families identically.

    The two frame kinds are told apart by their first bytes: a login starts
    00 0F, an AVL packet starts 00 00 00 00, so a stateless framer is enough.

    Raises TeltonikaFrameError for a stream that cannot be Teltonika or for a
    packet larger than config.TELTONIKA_MAX_PACKET_BYTES.
    """
    frames = []

    while len(buffer) >= 4:
        if buffer.startswith(_LOGIN_PREFIX):
            if len(buffer) < LOGIN_FRAME_LEN:
                break
            imei_part = buffer[2:LOGIN_FRAME_LEN]
            if not all(48 <= b <= 57 for b in imei_part):
                raise TeltonikaFrameError("login frame with a non-numeric IMEI")
            frames.append(buffer[:LOGIN_FRAME_LEN])
            buffer = buffer[LOGIN_FRAME_LEN:]

        elif buffer.startswith(_PREAMBLE):
            if len(buffer) < 8:
                break
            length = int.from_bytes(buffer[4:8], "big")
            if length == 0 or length > TELTONIKA_MAX_PACKET_BYTES:
                raise TeltonikaFrameError(
                    f"AVL data length {length} outside 1..{TELTONIKA_MAX_PACKET_BYTES}"
                )
            total = 8 + length + 4
            if len(buffer) < total:
                break
            frames.append(buffer[:total])
            buffer = buffer[total:]

        else:
            raise TeltonikaFrameError(f"unframed data, first bytes {buffer[:8].hex()}")

    return frames, buffer


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def parse_login(frame: bytes) -> str:
    """Return the IMEI from a login frame."""
    if not is_login_frame(frame):
        raise TeltonikaParseError("not a Teltonika login frame")
    imei = frame[2:].decode("ascii", errors="strict")
    if not imei.isdigit():
        raise TeltonikaParseError("login IMEI is not numeric")
    return imei


# ---------------------------------------------------------------------------
# AVL packet
# ---------------------------------------------------------------------------

class _Reader:
    """Bounds-checked sequential reader: running off the end is a parse error."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self.data):
            raise TeltonikaParseError("record runs past the end of the data field")
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def uint(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big")

    def sint(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big", signed=True)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


def _read_io(r: _Reader, extended: bool):
    id_len = 2 if extended else 1
    event_io_id = r.uint(id_len)
    r.uint(id_len)                      # total IO count; the per-size counts are authoritative
    io = {}
    for value_len in (1, 2, 4, 8):
        for _ in range(r.uint(id_len)):
            io_id = r.uint(id_len)
            io[io_id] = r.uint(value_len)
    if extended:
        for _ in range(r.uint(2)):
            io_id = r.uint(2)
            length = r.uint(2)
            io[io_id] = r.take(length)
    return event_io_id, io


def _read_record(r: _Reader, extended: bool) -> AvlRecord:
    millis = r.uint(8)
    try:
        timestamp = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise TeltonikaParseError(f"timestamp out of range: {millis}")
    priority = r.uint(1)
    longitude = r.sint(4) / 10_000_000
    latitude = r.sint(4) / 10_000_000
    altitude = r.sint(2)
    angle = r.uint(2)
    satellites = r.uint(1)
    speed = r.uint(2)
    event_io_id, io = _read_io(r, extended)
    return AvlRecord(
        timestamp=timestamp, priority=priority, latitude=latitude,
        longitude=longitude, altitude=altitude, angle=angle,
        satellites=satellites, speed=speed, event_io_id=event_io_id, io=io,
    )


def parse_avl_packet(frame: bytes) -> AvlPacket:
    """
    Decode a complete AVL data packet. Raises:
      TeltonikaCrcError       CRC mismatch (do not ACK)
      UnsupportedCodecError   a codec other than 8 / 8E
      TeltonikaParseError     anything else structurally wrong
    """
    if len(frame) < 8 + 3 + 4 or not frame.startswith(_PREAMBLE):
        raise TeltonikaParseError("not an AVL data packet")

    length = int.from_bytes(frame[4:8], "big")
    if len(frame) != 8 + length + 4:
        raise TeltonikaParseError("data field length does not match the frame size")

    data = frame[8:8 + length]
    received_crc = int.from_bytes(frame[8 + length:], "big")
    if received_crc != crc16_ibm(data):
        raise TeltonikaCrcError("CRC mismatch")

    codec = data[0]
    if codec not in SUPPORTED_CODECS:
        raise UnsupportedCodecError(codec)

    r = _Reader(data)
    r.take(1)                           # codec id, already read
    count = r.uint(1)
    extended = codec == CODEC_8E
    records = [_read_record(r, extended) for _ in range(count)]

    trailing_count = r.uint(1)
    if trailing_count != count:
        raise TeltonikaParseError(f"record count mismatch ({count} vs {trailing_count})")
    if r.remaining:
        raise TeltonikaParseError(f"{r.remaining} unexpected bytes after the last record")

    return AvlPacket(codec=codec, records=records)


def ack(records_accepted: int) -> bytes:
    """The 4-byte reply that tells the device how many records were taken."""
    return struct.pack(">I", records_accepted)
