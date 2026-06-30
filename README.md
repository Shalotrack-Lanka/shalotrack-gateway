# ShaloTrack Gateway

A Python TCP gateway that receives, parses, and acknowledges packets from GT06/V5-protocol GPS trackers, persists them to PostgreSQL, and exposes a console for sending remote commands back to connected devices.

This service sits between physical GPS trackers (installed in vehicles) and the rest of the ShaloTrack platform (API, database, mobile app):

```
GPS Tracker (V5 / GT06)
        │  TCP, port 9000
        ▼
ShaloTrack Gateway  ◄── this repository
        │  psycopg2
        ▼
PostgreSQL
```

## Features

- TCP server accepting concurrent device connections, one thread per connection
- Parses GT06/V5 binary packets in both `7878` (short frame) and `7979` (long frame) formats
- Supports Login, GPS Location, Status, Heartbeat, Alarm, Information, Command Response, and Configuration packets
- Always sends a protocol-correct ACK back to the tracker, even when downstream processing fails
- Persists raw packets, GPS tracking history, current location, device status, and device events to PostgreSQL
- In-memory device registry for tracking which devices are currently connected (online/offline state, socket handle)
- Interactive console for sending remote commands (locate, reboot, cut/restore engine relay) to a connected tracker by IMEI
- Dockerized for deployment

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Networking | Raw TCP sockets (`socket`, `threading`) — no async framework |
| Database | PostgreSQL via `psycopg2-binary` |
| Config | Environment variables via `python-dotenv` |
| Deployment | Docker |

## Installation

**Requirements:** Python 3.11+, a PostgreSQL database, Docker (optional).

```bash
git clone https://github.com/Shalotrack-Lanka/shalotrack-gateway.git
cd shalotrack-gateway
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://user:password@host:5432/dbname
PORT=9000
CONNECTION_TIMEOUT=60
LOW_BATTERY_THRESHOLD=2
OVERSPEED_THRESHOLD=80
```

`DATABASE_URL` is required (`database.py` loads it via `python-dotenv`). The other variables have defaults baked into `config.py` and `tcp_server.py` if omitted, though not every module currently reads from `config.py` — see [Known Limitations](#known-limitations).

## Running the Gateway

```bash
python tcp_server.py
```

This starts:
- The TCP server, listening on `0.0.0.0:9000` (or `$PORT`)
- A background console thread (see [Command Console](#command-console)) for sending commands to connected devices

### Docker

```bash
docker build -t shalotrack-gateway .
docker run -p 9000:9000 --env-file .env shalotrack-gateway
```

## Command Console

While the server is running, the console accepts commands typed into stdin:

```
where <imei>       # request current GPS location from the device
reset <imei>       # reboot the device
relay_on <imei>    # restore engine relay (re-enable ignition)
relay_off <imei>   # cut engine relay (immobilize)
exit
```

Commands are only deliverable while the target device has an open TCP socket (i.e. is currently registered as online in the in-memory device registry).

## Folder Structure

```
shalotrack-gateway/
├── tcp_server.py              # Entry point: socket accept loop, per-connection thread
├── console.py                 # Interactive command console (runs in its own thread)
├── config.py                  # Environment-driven configuration
├── database.py                # PostgreSQL connection helper
│
├── parsers/
│   └── v5_parser.py           # All GT06/V5 packet parsing + CRC16 + ACK building
│
├── decoders/                  # Protocol 0x94 "Information" packet sub-field decoders
│   ├── information_decoder.py     (orchestrator — not currently invoked, see below)
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
│   ├── device_registry.py     # In-memory map of connected devices (IMEI → socket/metadata)
│   ├── command_service.py     # Sends remote commands to a connected device's socket
│   ├── tracking_service.py    # Writes GPS/status data to PostgreSQL
│   └── event_service.py       # Writes device events (online, alarms, etc.) to PostgreSQL
│
├── repositories/
│   ├── device_repository.py       # IMEI → DeviceId, DeviceId → VehicleId lookups
│   └── raw_packet_repository.py   # Persists raw hex packets
│
├── constants/
│   ├── protocol_numbers.py    # Protocol byte → name enum
│   ├── event_types.py         # Device event type enum
│   └── severity.py            # Event severity enum
│
├── models/                    # Dataclasses describing domain objects
├── utils/
│   ├── command_builder.py     # Builds Protocol 0x80 command packets (WHERE, RESET, RELAY, ...)
│   └── logger.py              # Timestamped console logger
│
├── test/                      # Ad hoc manual test scripts (not an automated test suite)
└── docs/                      # Subsystem documentation (this folder)
```

## Packet Flow

```
Tracker connects
      │
      ▼
tcp_server.py: handle_device()
      │  conn.recv(1024)
      ▼
services/packet_handler.py: process_packet()
      │
      ├─► Detect frame type (7878 short / 7979 long) and read protocol byte
      ├─► Dispatch to the matching branch by protocol number
      │       ├─ 01      → parsers.v5_parser.parse_login_packet()      → register_device(), DEVICE_ONLINE event
      │       ├─ 12 / 22 → parsers.v5_parser.parse_location_packet()   → save_tracking(), update_current_location()
      │       ├─ 13      → parsers.v5_parser.parse_status_packet()     → update_device_status()
      │       ├─ 23      → parsers.v5_parser.parse_heartbeat_packet()  → update_device_status(), update_heartbeat()
      │       ├─ 26      → parsers.v5_parser.parse_alarm_packet()      → logged (not yet persisted as an event)
      │       ├─ 94      → raw packet saved only (sub-field decoding not wired in)
      │       ├─ 8a      → raw packet saved only (command ack acknowledged at TCP layer)
      │       ├─ 21      → ASCII command response decoded and printed
      │       └─ 6e      → raw packet saved only
      ├─► Every branch saves the raw hex packet via RawPacketRepository
      └─► build_ack() + conn.send() — ALWAYS executes, regardless of which branch ran or whether it raised
```

## Supported Protocols

| Code | Name | Status |
|---|---|---|
| `01` | Login | Parsed, device registered, event logged |
| `12` / `22` | GPS Location | Parsed, saved to tracking history + current location |
| `13` | Status | Parsed, device status updated |
| `23` | Heartbeat | Parsed, device status + heartbeat timestamp updated |
| `26` | Alarm | Parsed and logged to console; not yet persisted as a `DeviceEvent` |
| `94` | Information | Raw packet saved only; key/value sub-field decoding exists but is not called |
| `8a` | Command Acknowledgement | Raw packet saved, acknowledged |
| `21` | Command Text Response | ASCII payload extracted and printed (e.g. response to `WHERE#`) |
| `6e` | Configuration | Raw packet saved only |

See [`docs/protocols.md`](docs/protocols.md) for full packet structure and byte-level detail.

## Current Progress

The gateway currently:
- Accepts and maintains concurrent tracker connections over raw TCP
- Correctly distinguishes `7878`/`7979` frame types when identifying the protocol byte (this was previously a bug — see [Known Limitations](#known-limitations) for what's still outstanding)
- Parses and persists Login, Location, Status, and Heartbeat packets end-to-end
- Always responds with a valid ACK regardless of downstream errors
- Supports sending remote commands (`WHERE#`, `RESET#`, `RELAY,1#`/`RELAY,0#`) to a live device and decoding the device's text response

## Known Limitations

These are real, current gaps — not roadmap items framed as problems:

- **Single hardcoded device**: non-login packets (location, status, heartbeat, alarm) are currently associated with a hardcoded `TEST_IMEI` rather than the IMEI of the connection that sent them. Multi-device routing through `process_packet` is not yet wired end-to-end (tracked inline in `packet_handler.py` as Bug 4).
- **Southern/Western hemisphere coordinates are wrong**: `v5_parser.py` decodes the course/status bits needed for hemisphere correction, but the lines that apply the sign flip to latitude/longitude are commented out.
- **Alarm events aren't persisted**: Protocol `26` packets are parsed and printed but never written to `DeviceEvents`.
- **Protocol 94 (Information) sub-decoding is unused**: `decoders/information_decoder.py`, `parsers/v5_parser.py::parse_information_packet`, and `parsers/information_interpreter.py` implement key/value extraction (APN, SIM info, geofence config, alarm config, etc.) but nothing in `packet_handler.py` calls them — `94` packets are saved as raw hex only.
- **Several files are empty placeholders**: `parsers/login_parser.py`, `parsers/location_parser.py`, `parsers/status_parser.py`, `parsers/alarm_parser.py`, `utils/crc.py`, `utils/helper.py`, `repositories/tracking_repositroy.py`, `repositories/current_location_repository.py`, `repositories/event_repository.py`, `models/command.py`, `services/alarm_service.py`, `services/session_service.py`. The equivalent logic that exists currently lives in `parsers/v5_parser.py`, `utils/command_builder.py`, `services/tracking_service.py`, and `repositories/device_repository.py` instead.
- **Dead code present**: `services/status_service.py` references undefined names and is not imported anywhere; `services/state_change._service.py` and `repositories/status_repository.py` both define a `StatusRepository` class, and neither is imported anywhere either.
- **No automated test suite**: `test/` contains ad hoc manual scripts (one of which, `test_device_lookup.py`, imports a function that doesn't exist in the current codebase) rather than a runnable test framework.

## Roadmap

- Wire per-connection IMEI through the full packet-handling pipeline (replace `TEST_IMEI`)
- Enable hemisphere correction for worldwide coordinate support
- Persist alarm events to `DeviceEvents`
- Wire up Protocol 94 sub-field decoding
- Replace the manual `test/` scripts with an automated test suite
- API integration (ASP.NET Core) and Android app integration

## Documentation

| Doc | Covers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System architecture, threading model, data flow |
| [`docs/protocols.md`](docs/protocols.md) | Byte-level packet structure for every supported protocol |
| [`docs/database.md`](docs/database.md) | Tables written to and their schema, as inferred from the queries in this codebase |
| [`docs/deployment.md`](docs/deployment.md) | Docker build/run and required environment variables |

## Contributors

ShaloTrack-Lanka
