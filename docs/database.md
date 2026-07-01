# Database

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

The gateway does not own a schema migration system — it connects to an existing PostgreSQL database (presumably managed by the ASP.NET Core API project) via `psycopg2`. This document describes every table the gateway reads from or writes to, inferred directly from the SQL embedded in the codebase. It is not a full ER diagram of the platform database, only the gateway's view of it.

Connection is established per-call via `database.get_db_connection()`, which reads `DATABASE_URL` from the environment (loaded via `python-dotenv`). There is no connection pooling — every repository/service function opens, uses, and closes its own connection.

---

## `GpsDevices`

**Read by:** `repositories/device_repository.py::get_device_by_imei()`

| Column used | Purpose |
|---|---|
| `DeviceId` | Returned as the gateway's internal device identifier |
| `ImeiNumber` | Looked up against the IMEI parsed from a Login packet |

---

## `DeviceAssignments`

**Read by:** `repositories/device_repository.py::get_vehicle_by_device()`

| Column used | Purpose |
|---|---|
| `VehicleId` | Returned for a given device |
| `DeviceId` | Filter |
| `Status` | Filtered to `= 1` (presumably "active assignment") |

---

## `GpsTrackings`

**Written by:** `services/tracking_service.py::save_tracking()` — insert-only, one row per location packet.

| Column | Source |
|---|---|
| `DeviceId` | — |
| `Latitude` / `Longitude` | Parsed location |
| `Altitude` | Always `NULL` (not parsed from current packets) |
| `Speed` | Parsed location |
| `Heading` | Always `0` (parsed heading from the course/status word is computed in `v5_parser.py` but not passed through to this insert) |
| `Satellites` | Always `0` (same — parsed but not passed through) |
| `GpsAccuracy` | Always `NULL` |
| `EventTime` | Packet timestamp |
| `CreatedAt` | `NOW()` |

> Note: `Heading` and `Satellites` are computed correctly by the parser but currently dropped before reaching the database — `save_tracking()`'s signature doesn't accept them.

---

## `CurrentLocations`

**Written by:** `services/tracking_service.py::update_current_location()` — upsert keyed on `DeviceId`.

| Column | Source |
|---|---|
| `DeviceId` / `VehicleId` | — |
| `Latitude` / `Longitude` / `Speed` | Parsed location |
| `Heading` | Always `0` (same gap as above) |
| `IgnitionStatus` | Always `False` (not passed in from the status/heartbeat path — location updates and status updates are separate calls, and ignition state isn't carried over) |
| `MovementStatus` | `speed > 0` |
| `LastUpdate` | Packet timestamp |

`ON CONFLICT ("DeviceId") DO UPDATE` — only one current-location row exists per device.

---

## `DeviceStatuses`

**Written by:** `services/tracking_service.py::update_device_status()`, `update_heartbeat()`, `set_device_offline()`, `update_last_seen()`, `set_device_online()` — all upsert or update keyed on `DeviceId`.

| Column | Source |
|---|---|
| `DeviceId` | — |
| `IsOnline` | `True` on any status/heartbeat update; `False` on disconnect (`tcp_server.py`'s `finally` block) |
| `LastHeartbeat` | Set on heartbeat packets only |
| `LastSeen` | Set on status, heartbeat, and `update_last_seen()` calls |
| `GpsSignal` | From status/heartbeat terminal info |
| `BatteryLevel` | From status/heartbeat terminal info |
| `IgnitionStatus` | From status/heartbeat terminal info bit `0x02` |
| `MovementStatus` | Set to ignition status as a proxy — status/heartbeat packets carry no speed field |
| `PowerStatus` | `1` if not power-cut, `2` if power-cut (derived from terminal info bit `0x80`) |
| `UpdatedAt` | `NOW()` |

> Two unused duplicate read/write implementations for this table exist in the codebase: `repositories/status_repository.py::StatusRepository` and `services/state_change._service.py::StatusRepository`. Both are fully-formed but never imported by anything else — `tracking_service.py`'s plain functions are the ones actually used.

---

## `DeviceEvents`

**Written by:** `services/event_service.py::create_event()`

| Column | Source |
|---|---|
| `DeviceId` / `VehicleId` | — |
| `EventType` | One of `constants/event_types.py::EventType` (e.g. `DEVICE_ONLINE`) |
| `Severity` | One of `constants/severity.py::Severity` |
| `Latitude` / `Longitude` | Optional, currently unused (always `None` at call sites) |
| `RawPacketId` | Optional, currently unused (always `None` at call sites — no linkage from event back to the raw packet row that triggered it) |
| `Description` | Free text |
| `Metadata` | Optional JSON blob, currently unused |
| `CreatedAt` | `NOW()` (set in Python, not SQL `NOW()`) |

**Currently the only event type actually created in the codebase is `DEVICE_ONLINE`**, fired once per successful Login packet from an authorized device (`packet_handler.py`). The full `EventType` enum defines `DEVICE_OFFLINE`, `IGNITION_ON`/`OFF`, `MOVEMENT_STARTED`/`STOPPED`, `LOW_BATTERY`, `POWER_CONNECTED`/`DISCONNECTED`, `GPS_FIX_LOST`/`RESTORED`, `OVERSPEED`, `GEOFENCE_ENTER`/`EXIT`, `SOS`, `ENGINE_CUT`/`RESTORED`, and `COMMAND_SUCCESS`/`FAILED` — none of these are currently triggered anywhere. The logic to generate most of them exists in `services/status_service.py`, but that file is dead code (see Known Limitations in the README) and is never called.

---

## `RawPackets`

**Written by:** `repositories/raw_packet_repository.py::RawPacketRepository.save()` — insert-only, one row per received packet across every protocol branch.

| Column | Source |
|---|---|
| `DeviceId` | — |
| `ProtocolNumber` | Two-character hex string, e.g. `"01"`, `"22"`, `"94"` |
| `RawHex` | Full packet as a hex string |
| `PacketLength` | `len(raw_hex) // 2` (byte length, computed from the hex string) |
| `ReceivedAt` | `NOW()` (Python-side) |
| `Parsed` | Boolean flag, always passed as `True` by current call sites regardless of whether the packet's protocol actually has parsing logic wired in (e.g. Protocol `94` is saved with `Parsed=True` despite its sub-field decoding never running) |

This is effectively the audit trail / replay log — every packet the gateway has seen, in full, regardless of whether it was otherwise processed.

---

## Tables Referenced in Code But Not Confirmed Elsewhere

None — every table referenced above appears in actual executed SQL within the current codebase. No ER diagram or migration files were included in this repository, so column types, constraints, foreign keys, and indexes are not independently verifiable from this codebase alone; the column names above are exactly as they appear in the SQL strings.
