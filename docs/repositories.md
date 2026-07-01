# Repositories

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

The `repositories/` directory contains the data access layer — modules responsible solely for reading from and writing to PostgreSQL. Each repository is a class with only `@staticmethod` methods; no instance state.

---

## Active Repositories

### `repositories/device_repository.py` — `DeviceRepository`

Handles device and vehicle identity lookups. This is the only repository that reads from the database (all others only write).

**`get_device_by_imei(imei: str) -> str | None`**

```sql
SELECT "DeviceId" FROM "GpsDevices" WHERE "ImeiNumber" = %s LIMIT 1
```

Used by `packet_handler.py` during login to verify the device is registered in the platform before admitting it.

**`get_vehicle_by_device(device_id: str) -> str | None`**

```sql
SELECT "VehicleId" FROM "DeviceAssignments"
WHERE "DeviceId" = %s AND "Status" = 1 LIMIT 1
```

Used after login and after each location update to find the vehicle associated with this device, so `CurrentLocations` and `DeviceEvents` can carry the `VehicleId`.

**`get_device_status(device_id: str) -> dict | None`**

```sql
SELECT "IsOnline", "BatteryLevel", "GpsSignal",
       "IgnitionStatus", "MovementStatus", "PowerStatus"
FROM "DeviceStatuses" WHERE "DeviceId" = %s LIMIT 1
```

Returns a plain dict, or `None` if no status row exists yet. Defined here, but currently only referenced in `services/status_service.py` (which is dead code). Not called from anywhere active.

---

### `repositories/raw_packet_repository.py` — `RawPacketRepository`

Persists the raw hex of every received packet to `RawPackets`.

**`save(device_id, protocol_number, raw_hex, parsed) -> None`**

```sql
INSERT INTO "RawPackets" ("DeviceId", "ProtocolNumber", "RawHex",
  "PacketLength", "ReceivedAt", "Parsed") VALUES (...)
```

`PacketLength` is computed as `len(raw_hex) // 2` (byte length, derived from the hex string). `Parsed` is always passed as `True` at current call sites regardless of whether sub-field parsing actually ran.

Called from `_save_raw()` in `packet_handler.py` across every supported protocol branch.

---

## Stub / Dead Repositories

### `repositories/status_repository.py` — `StatusRepository`

Fully implemented. Contains `find_by_device()`, `save()`, `set_online()`, and `set_offline()` using the `DeviceStatus` dataclass from `models/device_status.py`. Duplicates the functionality of the plain functions in `services/tracking_service.py`.

**Never imported or called anywhere.** Appears to represent a planned refactor toward a more structured repository pattern (using typed dataclasses instead of raw SQL with positional tuples), but the transition was not completed.

---

### `repositories/tracking_repositroy.py` *(note typo in filename)*

Empty file — 0 bytes. Presumably intended for GPS tracking writes, which currently live in `services/tracking_service.py::save_tracking()`.

---

### `repositories/current_location_repository.py`

Empty file — 0 bytes. Intended for current-location upsert, which currently lives in `services/tracking_service.py::update_current_location()`.

---

### `repositories/event_repository.py`

Empty file — 0 bytes. Event writes currently go directly through `services/event_service.py` using an inline `get_db_connection()` call rather than a dedicated repository class.

---

## Notes on the Pattern

The codebase is mid-refactor between two styles:

1. **Plain service functions** (`tracking_service.py`, `event_service.py`) — earlier style, each function opens/closes its own connection and runs one query inline.
2. **Static repository classes** (`DeviceRepository`, `RawPacketRepository`, `StatusRepository`) — later style, similar connection-per-call pattern but organized as a class with typed method signatures.

Both patterns exist simultaneously. The empty stub files and duplicate `StatusRepository` suggest the intent was to fully migrate to static repository classes for each table, but that work was not completed. Either pattern is workable; the main practical difference is that the class pattern makes it easier to swap the connection strategy (e.g. add pooling) in one place.
