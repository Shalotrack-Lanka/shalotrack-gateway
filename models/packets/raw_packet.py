from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RawPacket:
    device_id: str
    protocol_number: str
    raw_hex: str
    parsed: bool
    received_at: datetime

