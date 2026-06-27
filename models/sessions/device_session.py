from dataclasses import dataclass
from datetime import datetime
import socket

@dataclass(slots=True)
class DeviceSession:
    device_id: str
    imei: str
    ip_address: str
    connection: socket.socket
    connected_at: datetime
    last_seen: datetime