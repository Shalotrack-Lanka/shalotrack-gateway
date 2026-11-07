"""Wire-protocol families the gateway can speak. See docs/multi-protocol.md."""

from protocols.detect import GT06, GPS103, TELTONIKA, detect_protocol

__all__ = ["GT06", "GPS103", "TELTONIKA", "detect_protocol"]
