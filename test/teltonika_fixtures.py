"""
Test-only builders for Teltonika AVL packets (Codec 8 and 8E).

Used to construct inputs for test_teltonika_parser.py and
test_teltonika_server.py. Production code only DECODES; this encodes, and is
deliberately written independently of the parser so that a bug has to be made
twice to go unnoticed. The published Teltonika example in the parser tests
anchors both to the real format.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parsers.teltonika_parser import crc16_ibm  # noqa: E402


def _signed(value: int, size: int) -> bytes:
    return value.to_bytes(size, "big", signed=True)


def encode_record(
    timestamp_ms: int,
    lat: float = 0.0,
    lon: float = 0.0,
    altitude: int = 0,
    angle: int = 0,
    satellites: int = 0,
    speed: int = 0,
    priority: int = 0,
    event_io: int = 0,
    io1=None, io2=None, io4=None, io8=None, iox=None,
    extended: bool = False,
) -> bytes:
    id_len = 2 if extended else 1

    def n(value):
        return value.to_bytes(id_len, "big")

    groups = [(1, io1 or {}), (2, io2 or {}), (4, io4 or {}), (8, io8 or {})]
    total = sum(len(g) for _, g in groups) + (len(iox or {}) if extended else 0)

    out = timestamp_ms.to_bytes(8, "big")
    out += bytes([priority])
    out += _signed(round(lon * 10_000_000), 4)
    out += _signed(round(lat * 10_000_000), 4)
    out += _signed(altitude, 2)
    out += angle.to_bytes(2, "big")
    out += bytes([satellites])
    out += speed.to_bytes(2, "big")
    out += n(event_io) + n(total)
    for size, group in groups:
        out += n(len(group))
        for io_id, value in group.items():
            out += n(io_id) + value.to_bytes(size, "big")
    if extended:
        out += n(len(iox or {}))
        for io_id, value in (iox or {}).items():
            out += n(io_id) + len(value).to_bytes(2, "big") + value
    return out


def build_avl_packet(records: list, codec: int = 0x08, count_trailer=None) -> bytes:
    """Wrap encoded records into a complete, correctly CRC'd AVL packet."""
    body = bytes([codec, len(records)]) + b"".join(records)
    body += bytes([len(records) if count_trailer is None else count_trailer])
    return (
        b"\x00\x00\x00\x00"
        + len(body).to_bytes(4, "big")
        + body
        + crc16_ibm(body).to_bytes(4, "big")
    )


def login_frame(imei: str) -> bytes:
    return b"\x00\x0f" + imei.encode("ascii")
