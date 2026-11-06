# Performance and cost tuning (no new infrastructure)

Goal: keep the cost of the gateway and database flat while the fleet grows, using only code and
configuration. Nothing here adds an AWS service.

## What a position packet costs

| | Statements per GT06 position packet | Pool borrows | Log lines |
|---|---|---|---|
| Before | 5 (`RawPackets` insert, `GpsTrackings` insert, vehicle `SELECT`, ignition `SELECT`, `CurrentLocations` upsert) | 4 | ~12 |
| After | 1 (`tracking_service.save_location`), plus a sampled `RawPackets` insert | 1 (2 when sampled) | same at `LOG_LEVEL=DEBUG`, 2-3 at `INFO` |

`save_location` is one SQL statement: the `GpsTrackings` insert is a data-modifying CTE, the vehicle
comes from the active `DeviceAssignments` row, and the ignition flag from `DeviceStatuses`, all
inside the same statement. An unassigned device still gets its history row and no live row.
Verified against a real PostgreSQL 16 by `test/test_location_write_sql.py`.

GPS103 reports no longer look up the vehicle separately; only alarms do (the event needs it).

## Settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `RAW_PACKET_MODE` | `selective` | `all` = store every packet (old behaviour); `selective` = always store login, alarms, information/config, command replies and unknown packets, sample the rest; `off` = store nothing |
| `RAW_PACKET_SAMPLE_RATE` | `0.02` | Share of routine packets (position, status, heartbeat) kept in `selective` mode |
| `LOG_LEVEL` | `DEBUG` | Per-packet detail lines (`RAW DATA`, `Latitude`, `ACK sent`, ...) are DEBUG. `INFO` hides them; logins, disconnects, rejections and errors stay |
| `GPS_TRACKING_IDEMPOTENT` | `false` | Ignore a position already stored for the same `(DeviceId, EventTime)`. Needs the unique index first: see `docs/gps-tracking-idempotency.md` |

Defaults keep today's behaviour except `RAW_PACKET_MODE`, which now stores fewer routine packets.
Set `RAW_PACKET_MODE=all` to get the old behaviour back.

## When to change what

| Fleet size | Do |
|---|---|
| now | Deploy this change. Leave `LOG_LEVEL=DEBUG` while you are still verifying units |
| ~100-300 devices | `LOG_LEVEL=INFO`. Check `RawPackets` growth; lower `RAW_PACKET_SAMPLE_RATE` if it is still large |
| before ~1,000 devices | Enable idempotency (after the index). Review `DB_POOL_MAX` against your Supabase tier's real connection limit |
| ~1,000-5,000 devices | Batched writer for `GpsTrackings`/`RawPackets` (not built yet, see the architecture notes) |

## Rollout order

1. Deploy the gateway with defaults (flag off). Safe without any database change.
2. Watch the gateway log and `GpsTrackings` for a day; confirm positions and the live map still update.
3. Optional: idempotency (separate runbook, order matters).

## Not changed on purpose

* `CurrentLocations` is still written for every point, so the live map keeps receiving every point
  (needed for smooth playback). Do not batch it into "latest per device".
* Alarms, SOS and events are written synchronously.
* `DB_POOL_MAX` default is unchanged: the right value depends on the Supabase tier.
