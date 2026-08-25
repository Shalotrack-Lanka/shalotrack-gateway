# ShaloTrack Gateway

*Lead Architect & Platform Engineer: Suwen Jayathunga — ShaloTrack Lanka*

A production-hardened Python TCP gateway that receives, parses, and acknowledges packets from GT06/V5-protocol GPS trackers, persists them to PostgreSQL via a thread-safe connection pool, and exposes an HTTP command API for sending remote commands to connected devices.

This service sits between physical GPS trackers (installed in vehicles) and the rest of the ShaloTrack platform (API, database, mobile app):

```
GPS Tracker (V5 / GT06)
        │  TCP, port 9000
        ▼
ShaloTrack Gateway  ◄── this repository
        │  psycopg2 (connection pool)
        ▼
PostgreSQL (Supabase)
        ▲
        │
ShaloTrack API (ASP.NET Core)  ←── HTTP command API (port 9001)
```

---

## Features

- TCP server accepting concurrent device connections, one thread per connection
- Parses GT06/V5 binary packets in both `7878` (short frame) and `7979` (long frame) formats
- Full protocol support: Login, GPS Location, Status, Heartbeat, Alarm, Information, Command ACK, Command Text Response, and Configuration packets — all parsed, persisted, and ACK'd
- Thread-safe PostgreSQL connection pool (psycopg2 `ThreadedConnectionPool`) with retry-on-exhaustion — no dropped logins under burst reconnects
- Admin-allowlist device security — only devices pre-registered in `SetupShalotrackDevices` with `Status = 'Activated'` are accepted; unknown or non-activated IMEIs are rejected and logged
- Per-connection IMEI routing via an O(1) reverse socket lookup registry — every packet is correctly attributed to its device regardless of fleet size
- Force-close on unregistered sockets — devices that reconnect without sending a login packet are immediately disconnected, forcing a clean re-login
- Auto-registration of newly activated devices into `GpsDevices` on first login, with race-condition safety (`ON CONFLICT DO NOTHING`)
- Alarm events fully persisted to `DeviceEvents` with severity classification and structured metadata (alarm type, coordinates, battery, signal, ignition state)
- Protocol 94 (Information) and 6e (Configuration) fully decoded through the sub-field decoder pipeline and persisted as `DEVICE_INFORMATION` / `DEVICE_CONFIGURATION` events
- HTTP Command API on port 9001 — internal VPC-only endpoint for the C# API to send commands to connected devices without SSH or console access
- OpenTelemetry instrumentation (traces, metrics, logs) exported to the SRE observability stack via OTLP gRPC
- Dockerized for deployment on AWS EC2 with ECR image management and SSM-parameter-driven configuration

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Networking | Raw TCP sockets (`socket`, `threading`) |
| Database | PostgreSQL via `psycopg2-binary` (ThreadedConnectionPool) |
| Config | Environment variables via `python-dotenv` + AWS SSM Parameter Store |
| Observability | OpenTelemetry SDK, OTLP gRPC exporter |
| Deployment | Docker, AWS ECR, AWS EC2, GitHub Actions CI/CD |

---

## Installation

**Requirements:** Python 3.11+, PostgreSQL, Docker (optional).

```bash
git clone https://github.com/Shalotrack-Lanka/shalotrack-gateway.git
cd shalotrack-gateway
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
PORT=9000
CONNECTION_TIMEOUT=300
MAX_CONNECTIONS=500
DB_POOL_MIN=5
DB_POOL_MAX=60
LOW_BATTERY_THRESHOLD=2
OVERSPEED_THRESHOLD=80
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel.shalotrack.internal:4317
```

---

## Running the Gateway

```bash
python tcp_server.py
```

This starts:
- The TCP server on `0.0.0.0:9000` (or `$PORT`)
- The HTTP Command API on `0.0.0.0:9001` (or `$COMMAND_API_PORT`)
- A background console thread (disabled automatically in headless/Docker environments)

### Docker

```bash
docker build -t shalotrack-gateway .
docker run \
  -p 8000:9000 \
  -p 8001:9001 \
  -e DATABASE_URL=... \
  -e CONNECTION_TIMEOUT=300 \
  -e MAX_CONNECTIONS=500 \
  -e DB_POOL_MIN=5 \
  -e DB_POOL_MAX=60 \
  shalotrack-gateway
```

---

## HTTP Command API

The gateway exposes a lightweight internal HTTP API on port 9001. This allows the C# API to send commands to connected devices without requiring direct socket access.

**Base URL (internal VPC only):** `http://10.0.4.175:8001`

### Endpoints

#### `GET /health`
Returns `200 OK` if the API is running.

#### `GET /devices`
Returns the list of currently connected and authenticated devices.

```json
{
  "connected_devices": [
    {
      "imei": "869925074406321",
      "device_id": "564d7347-...",
      "ip": "212.104.228.97",
      "connected_at": "2026-08-24T13:09:54.968342+00:00",
      "last_seen": "2026-08-24T13:09:57.478757+00:00"
    }
  ],
  "count": 1
}
```

#### `GET /commands`
Returns the full list of supported command names.

#### `POST /command`
Sends a command to a connected device.

**Simple commands (no params):**
```json
{ "imei": "869925074406321", "command": "where" }
```

**Parametrised commands:**
```json
{
  "imei": "869925074406321",
  "command": "timer",
  "params": { "t1": 20, "t2": 300 }
}
```

**Supported commands:**

| Command | Description | Params |
|---|---|---|
| `where` | Request current GPS location | — |
| `status` | Request device status | — |
| `version` | Request firmware version | — |
| `imei` | Request IMEI | — |
| `params` | Request all parameters | — |
| `gprsset` | Query GPRS settings | — |
| `reset` | Reboot the device | — |
| `relay_on` | Restore engine relay | — |
| `relay_off` | Cut engine relay | — |
| `sos_delete` | Delete SOS numbers | — |
| `timer` | Set upload interval | `t1` (moving, seconds), `t2` (stopped, seconds) |
| `distance` | Set distance interval | `meters` |
| `speed_alarm` | Configure overspeed alarm | `enabled`, `interval`, `limit_kmh`, `sms` |
| `moving_alarm` | Configure movement alarm | `enabled`, `radius_m`, `sms` |
| `fence_circle` | Set circular geofence | `enabled`, `lat`, `lon`, `radius_100m`, `trigger`, `sms` |
| `sos_add` | Set SOS phone numbers | `phone1`, `phone2`, `phone3` |
| `apn` | Set APN | `apn_name`, `user`, `pwd` |
| `server` | Set server address | `domain_or_ip`, `port`, `use_domain`, `udp` |
| `batalm` | Low battery alarm | `enabled`, `sms` |
| `poweralm` | Power cut alarm | `enabled`, `sms` |

---

## Device Security

Only devices pre-registered by an admin are accepted. The login flow:

```
Device connects → sends Login packet (IMEI)
        │
        ▼
Check GpsDevices (already registered?) → YES → accept immediately
        │ NO
        ▼
Check SetupShalotrackDevices (Status = 'Activated'?) → NO → reject, close connection
        │ YES
        ▼
Auto-create GpsDevices record → accept
        │
        ▼
Device sends packets → gateway saves GPS, status, alarms to DB
```

Devices with `Status != 'Activated'` (Not Activated, Temporarily Stopped, Cancelled) are rejected regardless of IMEI existence.

---

## Folder Structure

```
shalotrack-gateway/
├── tcp_server.py              # Entry point: socket accept loop, connection cap, per-connection thread
├── command_api.py             # HTTP Command API (port 9001) — internal VPC only
├── console.py                 # Interactive console (disabled in headless/Docker environments)
├── config.py                  # Environment-driven configuration
├── database.py                # Thread-safe PostgreSQL connection pool with retry-on-exhaustion
│
├── parsers/
│   └── v5_parser.py           # All GT06/V5 packet parsing + CRC16-X25 + ACK building
│
├── decoders/                  # Protocol 0x94 / 0x6e Information packet sub-field decoders
│   ├── information_decoder.py     (orchestrator)
│   ├── alarm_decoder.py
│   ├── device_settings_decoder.py
│   ├── geofence_decoder.py
│   ├── gps_decoder.py
│   ├── network_decoder.py
│   ├── phone_decoder.py
│   └── sim_decoder.py
│
├── services/
│   ├── packet_handler.py      # Central dispatcher: protocol detection → parse → save → ACK
│   ├── device_registry.py     # Thread-safe in-memory registry (IMEI → socket, O(1) reverse lookup)
│   ├── command_service.py     # Sends remote commands to connected device sockets
│   ├── tracking_service.py    # Writes GPS/status/heartbeat data to PostgreSQL
│   ├── event_service.py       # Writes device events (online, alarms, config) to PostgreSQL
│   └── command_response_service.py  # Handles device text responses to commands
│
├── repositories/
│   ├── device_repository.py       # IMEI allowlist check, auto-registration, vehicle lookup
│   ├── raw_packet_repository.py   # Persists raw hex packets
│   └── command_response_repository.py  # Persists command responses
│
├── constants/
│   ├── protocol_numbers.py    # Protocol byte → name enum
│   ├── event_types.py         # Device event type enum
│   └── severity.py            # Event severity enum
│
├── models/
│   └── command_response.py    # Command response dataclass
│
├── utils/
│   ├── command_builder.py     # Builds all GT06 command packets (WHERE, RESET, RELAY, TIMER, FENCE, ...)
│   ├── packet_buffer.py       # TCP fragmentation handler — assembles complete GT06 frames from stream
│   └── logger.py              # Timestamped console logger
│
├── test/                      # Manual test scripts
└── docs/                      # Protocol and architecture documentation
```

---

## Packet Flow

```
Tracker connects
      │
      ▼
tcp_server.py: handle_device()
      │  Connection cap check (semaphore, max 500)
      │  conn.recv(4096) + packet_buffer.extract_packets()
      ▼
services/packet_handler.py: process_packet()
      │
      ├─► Detect frame type (7878 / 7979) and protocol byte
      ├─► Dispatch by protocol:
      │       ├─ 01      → parse_login_packet() → allowlist check → register_device() → DEVICE_ONLINE event
      │       ├─ 12 / 22 → parse_location_packet() → save_tracking() → update_current_location()
      │       ├─ 13      → parse_status_packet() → update_device_status()
      │       ├─ 23      → parse_heartbeat_packet() → update_device_status() → update_heartbeat()
      │       ├─ 26      → parse_alarm_packet() → create_event() with severity + metadata
      │       ├─ 94      → parse_information_packet() → decode_information() → create_event()
      │       ├─ 8a      → command ACK logged with echoed protocol + serial
      │       ├─ 21      → parse_command_response() → handle_command_response()
      │       └─ 6e      → parse_information_packet() → decode_information() → DEVICE_CONFIGURATION event
      ├─► Unknown socket (no prior login) → force-close connection → device must reconnect and login
      ├─► Every branch saves raw hex packet via RawPacketRepository
      └─► build_ack() + conn.send() — ALWAYS executes regardless of branch outcome
```

---

## Supported Protocols

| Code | Name | Status |
|---|---|---|
| `01` | Login | ✅ Parsed, allowlist-checked, device registered, DEVICE_ONLINE event |
| `12` / `22` | GPS Location | ✅ Parsed, saved to GpsTrackings + CurrentLocations |
| `13` | Status | ✅ Parsed, DeviceStatuses updated |
| `23` | Heartbeat | ✅ Parsed, DeviceStatuses + LastHeartbeat updated |
| `26` | Alarm | ✅ Parsed, persisted to DeviceEvents with severity + full metadata |
| `94` | Information | ✅ Parsed, fully decoded via sub-field pipeline, persisted to DeviceEvents |
| `8a` | Command ACK | ✅ Parsed, echoed protocol + serial logged |
| `21` | Command Text Response | ✅ Parsed, saved to CommandResponses |
| `6e` | Configuration | ✅ Parsed, fully decoded via sub-field pipeline, persisted as DEVICE_CONFIGURATION event |

---

## Production Architecture

```
Internet
    │
    ▼
AWS NLB (port 8000) ──► Gateway EC2 (t3.small)
                              │ port 8000 → container:9000 (TCP devices)
                              │ port 8001 → container:9001 (HTTP command API, VPC-internal only)
                              │
                         Docker container
                              │
                    ┌─────────┴──────────┐
                    │                    │
             Supabase DB1           OTel Collector
           (GPS telemetry)        (traces/metrics/logs)
                                        │
                                   Grafana/Prometheus
```

**Configuration via AWS SSM Parameter Store:**

| Parameter | Value |
|---|---|
| `/shalotrack/prod/gateway/database_url` | Supabase session pooler URL (port 5432) |
| `/shalotrack/prod/gateway/connection_timeout` | `300` |
| `/shalotrack/prod/gateway/max_connections` | `500` |
| `/shalotrack/prod/gateway/db_pool_min` | `5` |
| `/shalotrack/prod/gateway/db_pool_max` | `60` |

---

## Known Limitations

- **Thread-per-connection model**: The current architecture spawns one OS thread per connected device. This is production-appropriate for the current fleet size (up to ~500 devices) but will need to be replaced with an `asyncio`-based event loop for the 25,000-device target. Planned post-launch.
- **No packet buffering during downtime**: If the gateway restarts, packets sent by devices during the restart window (~10 seconds for container restart, ~2 minutes for instance replacement) are permanently lost — the V5 device does not buffer and retransmit missed packets. Mitigated by using container restart (not instance termination) for deployments.
- **Some V5 firmware skips login on reconnect**: Certain device batches send GPS/status packets without re-sending a login packet after a TCP drop. The gateway handles this by force-closing the connection, which causes the device to reconnect and send login correctly. Adds ~5-10 seconds of delay on reconnect.
- **No duplicate packet detection**: If a device retransmits a packet, it will be saved twice in `GpsTrackings`. Minor data quality issue with no operational impact.
- **Southern/Western hemisphere coordinates**: `v5_parser.py` decodes hemisphere bits but the sign correction is not applied. ShaloTrack currently operates in Sri Lanka (North/East) so this has no operational impact.

---

## Contributors

ShaloTrack Group 05 — Lanka Nippon BizTech Institute (LNBTI)

| Role | Contributor |
|---|---|
| TCP Gateway, C# API, Android Backend | Suwen Jayathunga (UGC0323020) |
| Android UI/UX | Nethmi Wijekoon (UGC0323022) |
| Admin Portal, QA | Nuwan Akalanka (UGC0323027) |
| Cloud Infrastructure, SRE | Amoda Rashmika (UGC0323017) |
| Supervisor | Chandana Deshapriya |

**Investor & Owner:** Polwatte Gedara Nuwan Aloka

**Legal Entity:** ShaloTrack (Pvt) Ltd.