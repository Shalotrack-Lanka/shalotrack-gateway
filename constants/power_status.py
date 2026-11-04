"""
DeviceStatuses.PowerStatus values. They MUST match the API's PowerStatus enum
(ShaloTrack_API.Enums.PowerStatus: Connected = 0, Disconnected = 1), because the API, the Fleet
portal and the Android app all read this column with that enum.

(Until Nov 2026 the gateway wrote 1 for "normal" and 2 for "power cut". The API read 1 as Disconnected,
so every healthy device showed "Vehicle power: Disconnected" and raised false power-cut alerts.)
"""

POWER_CONNECTED = 0
POWER_DISCONNECTED = 1