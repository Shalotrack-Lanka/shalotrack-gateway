# Protocol Specification

The gateway speaks the GT06/V5 binary tracker protocol. Packets arrive in one of two frame formats:

| Frame | Start bytes | Protocol byte offset | Notes |
|---|---|---|---|
| Short (`7878`) | `78 78` | byte index 3 | Standard frame, single-byte length field |
| Long (`7979`) | `79 79` | byte index 4 | Used for longer payloads (e.g. Information packets); two-byte length field |

All frames end with a 2-byte CRC16-X25 checksum followed by `0D 0A`.

### CRC16-X25

Implemented in `parsers/v5_parser.py::crc16_x25()`. Initialized to `0xFFFF`, each byte XORed in, 8 shifts per byte using polynomial `0x8408`, finalized with a XOR against `0xFFFF`. This is the checksum algorithm used both to validate incoming packets conceptually and to build outgoing ACK/command packets.

> **Known issue:** `utils/crc.py` is an empty placeholder file. The CRC implementation that is actually used lives in `parsers/v5_parser.py` instead — there is currently no standalone, reusable CRC module despite the file existing for it.

---

## Protocol `01` — Login

**Purpose:** Identifies the device to the gateway by IMEI; triggers device registration and a `DEVICE_ONLINE` event.

**Parser:** `parse_login_packet()` in `v5_parser.py`

| Field | Bytes | Encoding |
|---|---|---|
| IMEI | `raw[4:12]` | 8 bytes, hex-encoded, leading zero stripped |
| Serial | `raw[-6:-4]` | 2 bytes, hex |

**Handling (`packet_handler.py`):**
1. Parse IMEI and serial.
2. Look up `DeviceRepository.get_device_by_imei(imei)`. If not found, the device is logged as unauthorized and **not** registered — but the gateway still sends an ACK (so an unrecognized tracker is acknowledged at the protocol level without being treated as a known device).
3. If found: `register_device()` adds the device to the in-memory registry, the raw packet is saved, the device's assigned vehicle is looked up, and a `DEVICE_ONLINE` event is created.

---

## Protocol `12` / `22` — GPS Location

**Purpose:** Periodic location update.

**Parser:** `parse_location_packet()` in `v5_parser.py`

| Field | Bytes | Encoding |
|---|---|---|
| Year | `raw[4]` | `+ 2000` |
| Month | `raw[5]` | — |
| Day | `raw[6]` | — |
| Hour / Min / Sec | `raw[7:10]` | — |
| GPS info | `raw[10]` | upper nibble = data length, lower nibble = satellite count |
| Latitude | `raw[11:15]` | 4-byte big-endian int ÷ 1,800,000 |
| Longitude | `raw[15:19]` | 4-byte big-endian int ÷ 1,800,000 |
| Speed | `raw[19]` | 1 byte, km/h |
| Course/status word | `raw[20:22]` | bits 0–9 = heading; bit 10 = south latitude; bit 11 = west longitude; bit 12 = GPS fix acquired |

**Known issue — hemisphere correction not applied:** the course/status word is decoded (`is_south`, `is_west`, `gps_fixed`), but the lines that actually flip the sign of latitude/longitude based on `is_south`/`is_west` are present in the source as commented-out code:

```python
#if is_south:
#    latitude = -latitude
#if is_west:
#    longitude = -longitude
```

As a result, coordinates are currently only correct for trackers in the northern/eastern hemisphere. This affects both this packet and the Alarm packet below, which shares the same GPS field layout.

**Handling:** saved to `GpsTrackings` (full history) and upserted into `CurrentLocations` (latest position per device) via `services/tracking_service.py`.

---

## Protocol `13` — Status / Protocol `23` — Heartbeat

Both protocols share an identical field layout and are parsed by the same function, `parse_terminal_status()`, exposed as `parse_status_packet()` and `parse_heartbeat_packet()` respectively.

| Field | Bytes | Encoding |
|---|---|---|
| Terminal info | `raw[4]` | bitfield (see below) |
| Voltage / Battery | `raw[5]` | 1 byte |
| GSM signal | `raw[6]` | 1 byte |

**Terminal info bitfield:**

| Bit | Meaning |
|---|---|
| `0x01` | Device activated |
| `0x02` | Ignition on |
| `0x04` | Charging |
| `0x40` | GPS tracking active |
| `0x80` | Power cut |

**Handling:** both update `update_device_status()` (battery, signal, ignition — used as a proxy for movement since neither packet carries speed). Heartbeat additionally calls `update_heartbeat()` to refresh `LastHeartbeat`/`LastSeen`/`IsOnline` in the database directly.

---

## Protocol `26` — Alarm

**Purpose:** Alarm/event packet (SOS, power cut, shock, geofence, overspeed, low battery, vibration, movement, ACC on/off, tow, GPS antenna, external power).

**Parser:** `parse_alarm_packet()` in `v5_parser.py`. Combines the date/time and GPS fields from the Location packet, plus LBS (cell tower) fields and an alarm type/language byte:

| Field | Bytes | Encoding |
|---|---|---|
| Date/time, GPS, course/status | `raw[4:22]` | same layout as Protocol `22` (same hemisphere-correction issue applies) |
| MCC | `raw[22:24]` | 2-byte big-endian |
| MNC | `raw[24]` | 1 byte |
| LAC | `raw[25:27]` | 2-byte big-endian |
| Cell ID | `raw[27:30]` | 3-byte big-endian |
| Terminal info | `raw[30]` | same bitfield as Status/Heartbeat |
| Battery / GSM signal | `raw[31:33]` | 1 byte each |
| Alarm/language byte | `raw[33]` | upper nibble = alarm type code; bit 0 = language (1 = English, 0 = Chinese) |

**Alarm type codes** (`get_alarm_name()`):

| Code | Name |
|---|---|
| 0 | NORMAL |
| 1 | SOS |
| 2 | POWER_CUT |
| 3 | SHOCK |
| 4 | FENCE_IN |
| 5 | FENCE_OUT |
| 6 | OVERSPEED |
| 7 | LOW_BATTERY |
| 8 | VIBRATION |
| 9 | MOVE |
| 10 | ACC_ON |
| 11 | ACC_OFF |
| 12 | TOW |
| 13 | GPS_ANTENNA |
| 14 | EXTERNAL_POWER |

**Known issue — not persisted as an event:** the handler parses the alarm and prints every field to the console, but does **not** call `create_event()` or write to `DeviceEvents`. Alarms are currently visible only in process logs, not queryable from the database.

---

## Protocol `94` — Information

**Purpose:** Carries device configuration as ASCII `KEY=VALUE;KEY=VALUE;...` pairs (APN, SIM info, geofence config, alarm thresholds, etc.) inside the binary frame.

**Parser:** `parse_information_packet()` strips the frame header/CRC/trailer (using the 7878/7979 offset distinction), then attempts an ASCII decode and splits on `;`/`=` into a `values` dict.

**Decoding pipeline (implemented but unused):** `decoders/information_decoder.py::decode_information()` would take that `values` dict and call out to per-domain decoders:

| Decoder | Extracts |
|---|---|
| `upload_decoder.py` | Upload mode (Always / Smart / Sleep / ACC Trigger) |
| `geofence_decoder.py` | Geofence enabled, radius, lat/lon, trigger, index |
| `phone_decoder.py` | SOS number, center number |
| `network_decoder.py` | APN, APN credentials, server IP/domain/port, DNS, protocol |
| `device_settings_decoder.py` | Timezone, language, work mode, ACC mode, sleep mode, LED mode, heartbeat/upload interval |
| `sim_decoder.py` | ICCID, IMSI, operator, phone number |
| `gps_decoder.py` | GPS filter, accuracy, interval |
| `alarm_decoder.py` | Three alarm-group bitfields (SOS, power cut, shock, low battery, overspeed, geofence, move, ACC, plus undocumented bits in groups 2/3) |

There's also a second, simpler implementation of the same idea in `parsers/information_interpreter.py::interpret_information()`.

**Known issue — neither path is wired in.** `services/packet_handler.py`'s `94` branch only saves the raw hex packet; it never calls `parse_information_packet()`, `decode_information()`, or `interpret_information()`. The configuration data inside Information packets is currently captured as opaque raw hex only.

---

## Protocol `8a` — Command Acknowledgement

Raw packet saved; no field-level parsing implemented. Used by the tracker to acknowledge receipt of a server-sent command at the protocol level (distinct from the human-readable text response sent via Protocol `21`).

---

## Protocol `21` — Command Text Response

**Purpose:** Carries the ASCII text response to a command sent via Protocol `80` (e.g. the tracker's reply to `WHERE#`).

**Handling:** payload is extracted by slicing off the frame header and trailer — `raw[10:-6]` for `7979` frames, `raw[9:-6]` for `7878` frames — then decoded as ASCII (`errors="ignore"`) and printed. Not currently persisted, and not correlated back to the specific command/serial number that triggered it.

---

## Protocol `6e` — Configuration

Raw packet saved; no field-level parsing implemented.

---

## Outbound: Protocol `80` — Server Command

Built by `utils/command_builder.py::build_command()`, used for every command the gateway sends to a tracker:

| Field | Encoding |
|---|---|
| Packet type | `0x80` |
| Command length | 1 byte, `len(SERVER_FLAG) + len(command_bytes)` |
| Server flag | `00 00 00 01` (fixed) |
| Command text | ASCII (e.g. `WHERE#`, `RESET#`, `RELAY,1#`) |
| Language | `00 02` (fixed) |
| Serial | 2 bytes, incrementing in-process counter (`next_serial()`) |
| CRC16-X25 | over packet length byte + body |

Wrapped in `78 78 ... 0D 0A` framing. Available commands today: `WHERE#`, `STATUS#`, `VERSION#`, `IMEI#`, `PARAM#`, `RESET#`, `RELAY,1#`, `RELAY,0#` (all defined in `command_builder.py`; only `WHERE#`, `RESET#`, `RELAY,1#`, `RELAY,0#` are currently exposed through `command_service.py` and the console).

> **Note on serial numbers:** `next_serial()` is a module-level counter starting at 1, reset to 1 every time the gateway process restarts. It is not persisted and not validated against tracker-side expectations.

---

## ACK Packet (Outbound)

Built by `build_ack()` for every received packet, regardless of protocol or parse success:

| Field | Encoding |
|---|---|
| Packet type | `0x05` |
| Protocol number | echoed from the byte that was parsed out of the incoming frame |
| Serial | echoed from `packet[-6:-4]` of the incoming frame |
| CRC16-X25 | over ack type + protocol + serial |

Wrapped in `78 78 ... 0D 0A` framing (the ACK is always sent in short-frame format, even if the original packet arrived as a `7979` long frame).

---

## Known Issues Summary

| Issue | Affected protocols | Status |
|---|---|---|
| 7878 vs 7979 protocol-byte offset | All | **Fixed** — explicit branch on frame start bytes |
| ACK skipped by early `return` | All | **Fixed** — ACK now sent unconditionally at function end |
| Hardcoded device identity for non-login packets | `12`/`22`, `13`, `23`, `26`, `94`, `8a`, `21`, `6e` | **Open** — `TEST_IMEI` fallback |
| Hemisphere sign correction not applied | `12`/`22`, `26` | **Open** — logic present, commented out |
| Alarm events not persisted | `26` | **Open** |
| Information sub-field decoding not wired in | `94` | **Open** |
