"""
Per-connection protocol detection.

All device families share one TCP port (so the AWS NLB listener, security
group and Docker port mapping stay exactly as they are). The family is decided
once, from the first bytes a device sends, and then fixed for the lifetime of
that connection.

    78 78 / 79 79     -> GT06 binary   (V5 2G, and anything GT06-compatible)
    "##" or "imei:"   -> GPS103 text   (Coban TK103A/B family, "gps103" in Traccar)
    00 0F + 15 digits -> TELTONIKA     (FMB series, Codec 8 / 8E; the IMEI login frame)

Only families listed in config.ENABLED_PROTOCOLS are returned; a disabled
family looks exactly like unknown traffic to the caller.

Anything else is NOT guessed at: the caller closes the connection. Holding a
socket open for traffic we cannot parse only burns one of the MAX_CONNECTIONS
slots.

Adding a family: see docs/adding-a-protocol.md (constant + one detection rule
here, a framer + handler, one row in tcp_server.PROTOCOL_PIPELINES, and the
name in config._ALL_PROTOCOLS).
"""

import config

GT06 = "GT06"
GPS103 = "GPS103"
TELTONIKA = "TELTONIKA"


def _sniff(first_bytes: bytes) -> str | None:
    """Pure signature match, ignoring whether the family is enabled."""
    if not first_bytes:
        return None

    if first_bytes[:2] in (b"\x78\x78", b"\x79\x79"):
        return GT06

    # GPS103 login is "##,imei:<id>,A;". Some firmware skips the login frame
    # after a reconnect and goes straight to "imei:<id>,..." data.
    if first_bytes.startswith(b"##") or first_bytes.startswith(b"imei:"):
        return GPS103

    # Teltonika opens every connection with a 2-byte length (always 0x000F =
    # 15) followed by the IMEI as ASCII digits. Check whatever digits have
    # arrived so far; the framer validates the full 17 bytes.
    if first_bytes[:2] == b"\x00\x0f" and all(48 <= b <= 57 for b in first_bytes[2:17]):
        return TELTONIKA

    return None


def detect_protocol(first_bytes: bytes, enabled=None) -> str | None:
    """
    Return the protocol family for the first bytes of a connection, or None
    (unknown, or a known family that is not enabled).
    """
    family = _sniff(first_bytes)
    if family is None:
        return None
    allowed = config.ENABLED_PROTOCOLS if enabled is None else enabled
    return family if family in allowed else None
