# Parsers

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

The `parsers/` directory is intended to hold one parser module per protocol. In the current codebase, all active parsing logic lives in a single file: `parsers/v5_parser.py`. The other files in the directory are empty stubs.

---

## `parsers/v5_parser.py` — Active Parser

This file contains everything needed to parse incoming GT06/V5 packets and build outgoing ACK/command packets. It has no dependencies outside the Python standard library.

### `crc16_x25(data: bytes) -> int`

Calculates CRC-16/X-25 for a byte sequence. Used both to validate incoming packet structure conceptually and to build outgoing ACKs and command packets.

Algorithm: initialized to `0xFFFF`, each byte XORed into the running CRC, 8-bit shifts with polynomial `0x8408`, finalized with `crc ^= 0xFFFF`.

This implementation is also imported and used by `utils/command_builder.py`. Despite `utils/crc.py` existing as a file for this purpose, it is empty — this function is the actual CRC implementation used across the codebase.

---

### `parse_login_packet(raw: bytes) -> dict`

Extracts IMEI and packet serial from a Login packet (Protocol `01`).

| Return key | Source | Notes |
|---|---|---|
| `imei` | `raw[4:12]` each byte formatted as 2-char hex, leading zeros stripped | 8 bytes × 2 hex chars = 16 chars before strip |
| `serial` | `raw[-6:-4].hex()` | 2-byte serial from packet trailer |

---

### `parse_location_packet(raw: bytes) -> dict`

Parses a GPS Location packet (Protocol `12` or `22`).

| Return key | Source | Notes |
|---|---|---|
| `timestamp` | `raw[4:10]` | `datetime(raw[4]+2000, raw[5], raw[6], raw[7], raw[8], raw[9])` |
| `gps_length` | `raw[10] >> 4` | Upper nibble of GPS info byte |
| `satellites` | `raw[10] & 0x0F` | Lower nibble of GPS info byte |
| `latitude` | `int.from_bytes(raw[11:15], "big") / 1_800_000` | Degrees |
| `longitude` | `int.from_bytes(raw[15:19], "big") / 1_800_000` | Degrees |
| `speed` | `raw[19]` | km/h, single byte |
| `heading` | `int.from_bytes(raw[20:22], "big") & 0x03FF` | Bits 0–9 of course/status word |
| `gps_fixed` | `bool(course_status & 0x1000)` | Bit 12 |
| `is_south` / `is_west` | bits 10/11 of course/status | **Decoded but the sign-flip is commented out** |

**Hemisphere correction (pending fix):**
```python
# These lines exist in the source but are commented out:
# if is_south:
#     latitude = -latitude
# if is_west:
#     longitude = -longitude
```
Until uncommented, coordinates in the Southern or Western hemispheres are returned with the wrong sign.

---

### `parse_terminal_status(packet: bytes) -> dict` / `parse_status_packet` / `parse_heartbeat_packet`

Shared decoder for Status (Protocol `13`) and Heartbeat (Protocol `23`) — both carry identical fields.

| Return key | Source |
|---|---|
| `battery_level` | `packet[5]` |
| `gsm_signal` | `packet[6]` |
| `ignition_status` | `bool(packet[4] & 0x02)` |
| `power_cut` | `bool(packet[4] & 0x80)` |
| `gps_tracking` | `bool(packet[4] & 0x40)` |
| `charging` | `bool(packet[4] & 0x04)` |
| `activated` | `bool(packet[4] & 0x01)` |

`parse_status_packet` and `parse_heartbeat_packet` are both thin wrappers that call `parse_terminal_status` and return its result unchanged.

---

### `parse_alarm_packet(raw: bytes) -> dict`

Parses an Alarm packet (Protocol `26`). The GPS section uses the same layout as `parse_location_packet` (bytes 4–22), including the same hemisphere-correction gap. Additionally extracts:

| Return key | Source |
|---|---|
| LBS fields | `mcc`, `mnc`, `lac`, `cell_id` from `raw[22:30]` |
| `terminal_info` | `raw[30]` (same bitfield as Status/Heartbeat) |
| `battery_level` / `gsm_signal` | `raw[31]`, `raw[32]` |
| `alarm_type` | upper nibble of `raw[33]` |
| `language` | `"English"` if `raw[33] & 0x01`, else `"Chinese"` |
| `ignition_status`, `power_cut`, `gps_tracking`, `charging`, `activated` | derived from `terminal_info` bitfield |

---

### `parse_information_packet(raw: bytes) -> dict`

Extracts the payload from a Protocol `94` Information packet and attempts to decode it as ASCII `KEY=VALUE;...` pairs.

Uses the 7878/7979 frame distinction to find the right payload start:
- `7979` frame: `payload = packet[5:-6]`
- `7878` frame: `payload = packet[4:-6]`

Returns:
```python
{
    "is_ascii": bool,          # True if payload decoded cleanly
    "raw_hex": str,            # full payload as hex regardless
    "values": dict,            # {"KEY": "VALUE", ...} if is_ascii
    "text": str                # full decoded string if is_ascii
}
```

**Currently never called.** Despite being fully implemented, `packet_handler.py`'s Protocol `94` branch only calls `_save_raw()` — it does not call `parse_information_packet()`.

---

### `build_ack(packet: bytes) -> bytes`

Builds a protocol-correct acknowledgement packet for any received frame.

Reads the protocol byte at the correct offset for the frame type:
- `7878` short frame: `protocol_number = packet[3]`
- `7979` long frame: `protocol_number = packet[4]`

Echoes back the serial number from `packet[-6:-4]`.

Returns a `7878`-format response (ACKs are always sent in short-frame format):
```
78 78 | 05 | <protocol_byte> | <serial 2 bytes> | <CRC16 2 bytes> | 0D 0A
```

---

### `get_alarm_name(alarm_type: int) -> str`

Maps an alarm type code (0–14) to a human-readable string. Returns `"UNKNOWN (0xNN)"` for unrecognised codes. See `docs/protocols.md` for the full table.

---

## Empty Stub Files

The following files exist in `parsers/` but contain no code:

| File | Intended purpose |
|---|---|
| `parsers/login_parser.py` | — |
| `parsers/location_parser.py` | — |
| `parsers/status_parser.py` | — |
| `parsers/alarm_parser.py` | — |

The corresponding functionality is currently implemented in `v5_parser.py` instead. These stubs represent a planned refactor that would give each packet type its own dedicated module.

---

## `parsers/information_interpreter.py`

A simpler, older interpretation of the Information packet key/value extraction, independently of `decoders/information_decoder.py`:

```python
def interpret_information(values: dict) -> dict:
    return {
        "alarm_1": values.get("ALM1"),
        "alarm_2": values.get("ALM2"),
        "alarm_3": values.get("ALM3"),
        "status": values.get("STA1"),
        "upload_mode": values.get("DYD"),
        "sos_number": values.get("SOS"),
        "center_number": values.get("CENTER"),
        "geofence": values.get("FENCE"),
    }
```

Not currently called. The more complete version is `decoders/information_decoder.py::decode_information()`.
