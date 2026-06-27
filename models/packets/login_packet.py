from dataclasses import dataclass

@dataclass(slots=True)
class LoginPacket:
    imei: str
    serial: str