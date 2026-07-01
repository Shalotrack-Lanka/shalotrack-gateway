# Models

The `models/` directory contains Python dataclasses that represent domain objects. These provide typed, self-documenting structures for passing data between layers. They are defined using `@dataclass(slots=True)` for memory efficiency.

Currently, most active code paths pass data as plain dicts rather than dataclass instances — the models represent a planned refactor direction rather than the current runtime behavior. The one exception is `DeviceStatus`, which is used by `StatusRepository` (itself unused) and `state_change._service.py` (also unused).

---

## `models/device_status.py` — `DeviceStatus`

```python
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
```

Used by `repositories/status_repository.py` and `services/state_change._service.py` — both currently dead code.

---

## `models/packets/login_packet.py` — `LoginPacket`

```python
@dataclass(slots=True)
class LoginPacket:
    imei: str
    serial: str
```

Not used in active code — `parse_login_packet()` returns a plain dict with the same keys.

---

## `models/packets/location_packet.py` — `GpsLocation`

```python
@dataclass(slots=True)
class GpsLocation:
    latitude: float
    longitude: float
    speed: int
    heading: int
    satellites: int
    altitude: int | None
    timestamp: datetime
```

Not used in active code — `parse_location_packet()` returns a plain dict.

---

## `models/packets/raw_packet.py` — `RawPacket`

```python
@dataclass(slots=True)
class RawPacket:
    device_id: str
    protocol_number: str
    raw_hex: str
    parsed: bool
    received_at: datetime
```

Not used in active code — `RawPacketRepository.save()` takes positional arguments rather than a `RawPacket` instance.

---

## `models/sessions/device_session.py` — `DeviceSession`

```python
@dataclass(slots=True)
class DeviceSession:
    device_id: str
    imei: str
    ip_address: str
    connection: socket.socket
    connected_at: datetime
    last_seen: datetime
```

Not used in active code — `device_registry.py` stores device session data as a plain dict.

---

## `models/events/device_event.py` — `DeviceEvent`

```python
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
```

Not used in active code — `event_service.py::create_event()` takes individual parameters.

---

## `models/command.py`

Empty file — 0 bytes. Presumably intended for a `Command` dataclass covering outbound command metadata (IMEI, command string, serial number, sent timestamp).

---

## Summary

All models are structural placeholders — they document intended types but are not used in the current active code paths. The practical direction is to refactor the active dict-returning parsers and plain-argument repository functions to use these dataclasses once the data pipeline is more stable.

---

# Decoders

The `decoders/` directory handles sub-field extraction from Protocol `0x94` (Information) packets. Once `parse_information_packet()` extracts the `values` dict of `KEY=VALUE` pairs from the ASCII payload, the decoders extract meaning from individual keys.

The orchestrating function is `decoders/information_decoder.py::decode_information(values)`. It is fully implemented but never called by active code.

---

## `decoders/information_decoder.py` — `decode_information(values: dict) -> dict`

Calls each sub-decoder in turn and assembles a structured configuration dict:

```python
{
    "upload_mode": ...,         # from upload_decoder
    "geofence": ...,            # from geofence_decoder
    "sos_number": ...,          # from phone_decoder
    "center_number": ...,
    "network": { ... },         # from network_decoder
    "device_settings": { ... }, # from device_settings_decoder
    "sim": { ... },             # from sim_decoder
    "gps": { ... },             # from gps_decoder
    "alarm_configuration": { ... } # from alarm_decoder
}
```

---

## `decoders/upload_decoder.py` — `decode_upload_mode(value: str) -> str`

Maps the `DYD` key:

| Code | Mode |
|---|---|
| `"00"` | Always Upload |
| `"01"` | Smart Upload |
| `"02"` | Sleep Upload |
| `"03"` | ACC Trigger Upload |

---

## `decoders/geofence_decoder.py` — `decode_geofence(value: str) -> dict`

Parses the `FENCE` key (comma-separated). Returns `None` if empty, raw string if fewer than 8 parts, otherwise:

```python
{
    "enabled": bool,    # parts[1] == "ON"
    "radius": int,      # parts[2]
    "latitude": float,  # parts[3]
    "longitude": float, # parts[4]
    "trigger": str,     # parts[6]
    "index": int        # parts[7]
}
```

---

## `decoders/phone_decoder.py` — `decode_phone_numbers(values: dict) -> dict`

```python
{
    "sos_number": values.get("SOS"),
    "center_number": values.get("CENTER")
}
```

---

## `decoders/network_decoder.py` — `decode_network_configuration(values: dict) -> dict`

```python
{
    "apn": values.get("APN"),
    "apn_username": values.get("APNUSER"),
    "apn_password": values.get("APNPWD"),
    "server_ip": values.get("IP"),
    "server_domain": values.get("DOMAIN"),
    "server_port": values.get("PORT"),
    "dns": values.get("DNS"),
    "protocol": values.get("PROTOCOL"),
}
```

---

## `decoders/device_settings_decoder.py` — `decode_device_settings(values: dict) -> dict`

```python
{
    "timezone": values.get("GMT"),
    "language": values.get("LANG"),
    "work_mode": values.get("MODE"),
    "acc_mode": values.get("ACC"),
    "sleep_mode": values.get("SLEEP"),
    "led_mode": values.get("LED"),
    "heartbeat_interval": values.get("HEART"),
    "upload_interval": values.get("UPLOAD"),
}
```

---

## `decoders/sim_decoder.py` — `decode_sim_information(values: dict) -> dict`

```python
{
    "iccid": values.get("ICCID"),
    "imsi": values.get("IMSI"),
    "operator": values.get("OP"),
    "phone_number": values.get("PHONE"),
}
```

---

## `decoders/gps_decoder.py` — `decode_gps_configuration(values: dict) -> dict`

```python
{
    "filter": values.get("FILTER"),
    "accuracy": values.get("GPSACC"),
    "interval": values.get("INTERVAL"),
}
```

---

## `decoders/alarm_decoder.py` — `decode_alarm_configuration(values: dict) -> dict`

Decodes three alarm-group bytes (`ALM1`, `ALM2`, `ALM3`) from hex strings into bitfield dicts.

**ALM1 (Group 1) — named bits:**

| Bit | Alarm |
|---|---|
| `0x01` | SOS |
| `0x02` | Power cut |
| `0x04` | Shock |
| `0x08` | Low battery |
| `0x10` | Overspeed |
| `0x20` | Geofence |
| `0x40` | Move |
| `0x80` | ACC |

**ALM2 and ALM3** — decoded as `bit0`–`bit7` (meanings not yet documented in code).