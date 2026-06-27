from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class GpsLocation:
    latitude: float
    longitude: float
    speed: int
    heading: int
    satellites: int
    altitude: int | None
    timestamp: datetime