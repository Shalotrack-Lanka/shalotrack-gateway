# Services

The `services/` directory contains the business logic layer — everything above raw database queries and below the TCP server. Each service is a plain Python module (no classes, except where noted). This document covers every file in the directory.

---

## `services/packet_handler.py`

The single most important file in the gateway. Every byte received from a GPS tracker passes through this module.

**Purpose:** Detect the frame type and protocol number, dispatch to the appropriate parser, persist results to the database, and unconditionally return an ACK.

**Entry point:** `process_packet(data, conn, addr) -> str | None`

Called once per received chunk from `tcp_server.py::handle_device()`. Returns the IMEI string if a device is registered in this call chain, or `None` if not (e.g. the packet was unknown or the device was unauthorized).

**Protocol detection:**
```python
if raw[0:2] == b"\x78\x78":
    protocol = f"{raw[3]:02x}"   # short frame: protocol at byte 3
elif raw[0:2] == b"\x79\x79":
    protocol = f"{raw[4]:02x}"   # long frame: protocol at byte 4
```

This distinction is critical — getting it wrong causes correct protocol bytes to be misread. This was an actual bug (see `docs/protocols.md → Known Issues Summary`).

**ACK guarantee:**
```python
# Always executes at end of function, regardless of which branch ran or what it raised:
try:
    ack = build_ack(data)
    conn.send(ack)
except Exception as ex:
    print(f"❌ ACK ERROR: {ex}")
```
This was another actual bug — early versions had `return` statements inside each protocol branch that silently skipped this block.

**Current device tracking limitation:** a module-level `TEST_IMEI = "355172106043787"` is used as a fallback device identity for non-login packets. This means all location, status, heartbeat, alarm, and other packets are attributed to this hardcoded IMEI rather than the IMEI of the TCP connection they arrived on. This is tracked in code comments as Bug 4.

**Private helper functions:**

| Function | Purpose |
|---|---|
| `_get_device(imei=None)` | Looks up a device in the in-memory registry; defaults to `TEST_IMEI` if no IMEI passed |
| `_save_raw(device_id, protocol, hex_data)` | Saves raw packet to `RawPacketRepository` |
| `_log_unknown(protocol, raw, hex_data)` | Prints structured debug output for unrecognized protocols |

---

## `services/device_registry.py`

**Purpose:** In-memory registry of currently-connected devices.

This is a plain module wrapping a single module-level dict:

```python
connected_devices = {}   # keyed by IMEI string
```

Each value is:
```python
{
    "device_id": str,      # DB device UUID
    "ip": str,             # client IP from TCP accept
    "socket": socket,      # live socket — used by command_service to send commands
    "connected_at": datetime,
    "last_seen": datetime
}
```

**Functions:**

| Function | Purpose |
|---|---|
| `register_device(imei, ip, device_id, conn)` | Adds or overwrites a device entry |
| `get_device(imei)` | Returns the full entry dict, or `None` |
| `unregister_device(imei)` | Removes an entry on disconnect |
| `update_last_seen(imei)` | Updates `last_seen` timestamp |
| `get_socket(imei)` | Returns the raw socket for command delivery |
| `is_online(imei)` | Returns `True` if the IMEI is currently in the dict |
| `get_all_devices()` | Returns the entire dict — used by `command_service` but not currently used by any callers |

**Thread safety:** no locks. The dict is shared across all device threads. Under CPython the GIL prevents data corruption at the bytecode level for individual dict operations, but compound operations (e.g. check-then-write) are not atomic. This is sufficient for the current single-tracker use case but would need a `threading.Lock` for concurrent multi-tracker scenarios.

---

## `services/command_service.py`

**Purpose:** Delivers binary command packets to a connected device over its live TCP socket.

**Core function:** `send_command(imei, command: bytes) -> True`

Looks up the socket via `device_registry.get_socket(imei)`, raises `Exception("Device Offline")` if not found, then calls `sock.sendall(command)`. Prints a formatted log block showing IMEI, byte length, and hex before sending.

**High-level commands** (each builds a packet via `utils/command_builder.py` and calls `send_command`):

| Function | Command string | Effect |
|---|---|---|
| `send_where(imei)` | `WHERE#` | Requests tracker's current GPS location |
| `reboot(imei)` | `RESET#` | Reboots the tracker |
| `cut_engine(imei)` | `RELAY,0#` | Activates engine relay cut (immobilizes vehicle) |
| `resume_engine(imei)` | `RELAY,1#` | Deactivates relay cut (restores ignition) |

`command_builder.py` also defines `build_status()`, `build_version()`, `build_imei()`, and `build_params()` — these are not yet exposed through `command_service.py` or the console but are ready to add.

---

## `services/tracking_service.py`

**Purpose:** All direct PostgreSQL writes related to device state — GPS history, current position, status, heartbeat, and online/offline transitions.

Every function opens its own connection, executes one statement, commits, and closes. There is no connection pooling.

| Function | Table | Operation |
|---|---|---|
| `save_tracking(device_id, lat, lon, speed, event_time)` | `GpsTrackings` | INSERT |
| `update_current_location(device_id, vehicle_id, lat, lon, speed, event_time)` | `CurrentLocations` | UPSERT (ON CONFLICT DeviceId) |
| `update_device_status(device_id, battery, gps_signal, ignition, movement, power)` | `DeviceStatuses` | UPSERT (ON CONFLICT DeviceId) |
| `update_heartbeat(device_id)` | `DeviceStatuses` | UPDATE `IsOnline=TRUE`, `LastHeartbeat`, `LastSeen`, `UpdatedAt` |
| `set_device_offline(device_id)` | `DeviceStatuses` | UPDATE `IsOnline=FALSE` |
| `update_last_seen(device_id)` | `DeviceStatuses` | UPDATE `LastSeen`, `UpdatedAt` |
| `set_device_online(device_id)` | `DeviceStatuses` | UPDATE `IsOnline=TRUE`, `LastSeen`, `UpdatedAt` |

> `get_device_status()` and `get_vehicle_by_device()` are also imported from this module in `services/status_service.py`, but those functions are actually defined in `repositories/device_repository.py` — the import in `status_service.py` is incorrect and would raise an `ImportError` at runtime. This does not currently cause issues because `status_service.py` is never imported anywhere.

**Gaps in data passed through:** the location parser extracts `heading` and `satellites` correctly, but `save_tracking()` and `update_current_location()` always write `0` for both. These would need to be added to the function signatures to be preserved.

---

## `services/event_service.py`

**Purpose:** Creates entries in the `DeviceEvents` table.

Single function: `create_event(device_id, event_type, severity, vehicle_id=None, latitude=None, longitude=None, raw_packet_id=None, description=None, metadata=None)`

All optional parameters are currently always passed as `None` by callers — `vehicle_id` is available at call sites but is passed; `latitude`/`longitude`/`raw_packet_id`/`metadata` are structural placeholders for future use.

In the current codebase, `create_event()` is called in exactly one place: the Login handler in `packet_handler.py`, creating a `DEVICE_ONLINE` event when an authorized device connects. The full `EventType` enum has 22 defined event types; only `DEVICE_ONLINE` currently fires.

---

## `services/status_service.py`

**Purpose (intended):** State-change detection — compare current parsed status against the previous status stored in the database, and emit an event only when something actually changes (ignition on/off, movement start/stop, power cut, low battery, online/offline transitions).

**Current state: dead code.** This module is never imported or called by anything else in the codebase. Additionally, it contains references to undefined names (`IGNITION_ON`, `LOW`, `MOVEMENT_STARTED`, etc. — the `.value` suffix is missing from the enum accesses, and some names are imported from a non-existent location). If called, it would raise a `NameError` at runtime.

The logic it describes is well-structured and largely correct in intent — it represents the right pattern for event generation (compare-then-emit). It would need import fixes and then wiring into `packet_handler.py`'s status/heartbeat branches to become active.

---

## `services/state_change._service.py`

> Note: the filename contains a literal dot before `_service` (`state_change._service.py`), which is unusual and may be a typo.

**Current state: dead code.** Contains a `StatusRepository` class that duplicates the one in `repositories/status_repository.py`. Neither is imported anywhere — `services/tracking_service.py`'s plain functions handle all status reads and writes.