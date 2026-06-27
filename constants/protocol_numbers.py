from enum import Enum

class Protocol(Enum):
    LOGIN = "01"
    GPS_LBS = "12"
    GPS = "22"
    STATUS = "13"
    HEARTBEAT = "23"
    ALARM = "26"
    COMMAND = "80"
    TIME_SYNC = "8A"
    INFORMATION = "94"
    STRING = "6E"
    WIFI = "2C"
    UNKNOWN = "FF"