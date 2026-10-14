"""
Per-connection protocol detection.

All device families share one TCP port (so the AWS NLB listener, security
group and Docker port mapping stay exactly as they are). The family is decided
once, from the first bytes a device sends, and then fixed for the lifetime of
that connection.

    78 78 / 79 79     -> GT06 binary   (V5 2G, and anything GT06-compatible)
    "##" or "imei:"   -> GPS103 text   (Coban TK103A/B family, "gps103" in Traccar)

Anything else is NOT guessed at: the caller closes the connection. Holding a
socket open for traffic we cannot parse only burns one of the MAX_CONNECTIONS
slots.

Adding a family later (e.g. a V5 4G unit that turns out to differ) means: add a
constant here, add one detection rule, add a framer + handler, register them in
tcp_server.PROTOCOL_PIPELINES.
"""

GT06 = "GT06"
GPS103 = "GPS103"


def detect_protocol(first_bytes: bytes) -> str | None:
    """Return the protocol family for the first bytes of a connection, or None."""
    if not first_bytes:
        return None

    if first_bytes[:2] in (b"\x78\x78", b"\x79\x79"):
        return GT06

    # GPS103 login is "##,imei:<id>,A;". Some firmware skips the login frame
    # after a reconnect and goes straight to "imei:<id>,..." data.
    if first_bytes.startswith(b"##") or first_bytes.startswith(b"imei:"):
        return GPS103

    return None