from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class DeviceStatus:
    device_id: str
    battery_level: int
    gps_signal: int
    ignition_status: bool
    movement_status: bool
    power_status: int
    is_online: bool = True
    last_seen: datetime | None = None
    last_heartbeat: datetime | None = None