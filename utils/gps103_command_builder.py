"""
Outbound commands for GPS103 (Coban TK103A/B family) trackers.

The GT06/V5 builders in utils/command_builder.py produce binary frames and must
never be written to a GPS103 socket. A GPS103 tracker takes plain ASCII over
the same TCP connection:

    **,imei:<15 digits>,<code>[,<argument>];

Only the codes below are implemented. They come from Traccar's open-source
gps103 command encoder; they have NOT yet been verified on a physical unit
(clones differ), so the first real send must be watched in the gateway log.

    B   report the current position once   -> tracker answers with a normal
                                              "imei:...,tracker,..." report
    K   resume the engine (restore the relay)

Deliberately NOT implemented:

    J   stop the engine (immobilise). Cutting power to a vehicle that may be
        moving is a safety risk, and neither the clone's behaviour nor its
        wiring can be checked from here. See docs/gps103-commands.md.
    C   periodic reporting interval. The argument format varies between
        firmwares; change the interval by SMS instead.
"""

import re

# ASCII digits only: \d would also match other Unicode digits, and this value
# is interpolated into bytes sent to a vehicle.
_IMEI_RE = re.compile(r"[0-9]{10,17}")


def _frame(imei: str, code: str) -> bytes:
    if not isinstance(imei, str) or not _IMEI_RE.fullmatch(imei):
        raise ValueError("invalid IMEI for a GPS103 command")
    return f"**,imei:{imei},{code};".encode("ascii")


def build_single_position(imei: str) -> bytes:
    """Ask the tracker for its current position once."""
    return _frame(imei, "B")


def build_engine_resume(imei: str) -> bytes:
    """Restore the engine relay (the portal's 'Relay On')."""
    return _frame(imei, "K")
