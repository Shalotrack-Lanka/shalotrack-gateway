from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class DeviceStatus:
    device_id: str
    is_online: bool
    battery_level: int
    gps_signal: int
    ignition_status: bool
    movement_status: bool
    power_status: int
    last_heartbeat: datetime | None = None
    last_seen: datetime | None = None