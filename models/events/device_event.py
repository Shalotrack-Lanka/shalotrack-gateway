from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class DeviceEvent:
    device_id: str
    vehicle_id: str | None
    event_type: str
    severity: int
    description: str
    latitude: float | None = None
    longitude: float | None = None
    raw_packet_id: int | None = None
