# Architecture

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

## Overview

The gateway is a single-process, multi-threaded Python TCP server. There is no async framework, message queue, or external broker — concurrency is handled with one OS thread per device connection, plus one background thread for the interactive command console.

```
                       ┌─────────────────────────┐
                       │   GPS Tracker (V5/GT06)  │
                       └────────────┬─────────────┘
                                    │ TCP, port 9000 (configurable via $PORT)
                                    ▼
                       ┌─────────────────────────┐
                       │   tcp_server.py          │
                       │   socket.accept() loop   │
                       └────────────┬─────────────┘
                                    │ one thread per connection
                                    ▼
                       ┌─────────────────────────┐
                       │   handle_device()         │
                       │   conn.recv() loop        │
                       └────────────┬─────────────┘
                                    │ raw bytes
                                    ▼
                       ┌─────────────────────────┐
                       │ services/packet_handler.py│
                       │ process_packet()          │
                       └──┬─────────┬─────────┬───┘
                          │         │         │
                 ┌────────▼──┐ ┌────▼────┐ ┌──▼───────────┐
                 │ parsers/   │ │services/│ │ repositories/│
                 │ v5_parser  │ │ *.py    │ │ *.py         │
                 └────────────┘ └─────────┘ └──────┬───────┘
                                                    │ psycopg2
                                                    ▼
                                            ┌───────────────┐
                                            │  PostgreSQL   │
                                            └───────────────┘
```

## Threading Model

- `tcp_server.start_server()` binds the listening socket and starts an infinite `accept()` loop.
- For every accepted connection, a new daemon thread runs `handle_device(conn, addr)`. There is no thread pool or connection limit — one thread is spawned per tracker, for the lifetime of that tracker's connection.
- A single additional daemon thread runs `console.start_console()`, reading commands from stdin for the lifetime of the process.
- There is no locking around the shared `connected_devices` dict in `device_registry.py`. Reads/writes are simple dict operations; under CPython this is generally safe for individual operations due to the GIL, but there is no explicit thread-safety mechanism for compound operations.

## Connection Lifecycle

1. `server.accept()` returns a new socket and address.
2. A thread is spawned running `handle_device`.
3. `conn.settimeout(60)` is set — if no data is received within 60 seconds, the connection is treated as timed out (caught as `socket.timeout`).
4. The thread loops on `conn.recv(1024)`. An empty `data` (`b''`) means the peer closed the connection, breaking the loop.
5. Each received chunk is passed whole to `process_packet()`. Note: there is no buffering/reassembly across `recv()` calls — the code assumes one `recv()` call returns exactly one complete packet, which holds for the GT06/V5 frame sizes seen so far but is not a general TCP-framing guarantee.
6. If `process_packet()` returns an IMEI, `update_last_seen(imei)` refreshes that device's in-memory last-seen timestamp.
7. On disconnect (loop break), timeout, or any unhandled exception, the `finally` block looks up the device by the last known IMEI, marks it offline in the database (`set_device_offline`), and removes it from the in-memory registry (`unregister_device`).

## Packet Dispatch

All protocol-specific logic is centralized in `services/packet_handler.py::process_packet()`. It performs, in order:

1. **Frame type / protocol detection** — distinguishes `7878` (short frame, protocol byte at offset 3) from `7979` (long frame, protocol byte at offset 4). This offset distinction was the subject of a real bug (see `docs/protocols.md` → Known Issues): using the 7878 offset unconditionally on a 7979 frame previously misread Protocol `94` (Information) packets as protocol `08`, routing them into the "Unknown Packet" branch.
2. **Protocol-specific branch** — each supported protocol number has its own `if`/`elif` branch that calls the appropriate parser in `parsers/v5_parser.py`, then calls into `services/` and `repositories/` to persist the result.
3. **Unconditional ACK** — regardless of which branch executed, or whether it raised an exception, `build_ack(data)` is called and the response is sent back over the same socket in a `try/except` at the very end of the function. This was also the subject of a real bug: earlier versions had `return` statements inside individual branches that skipped the ACK entirely, causing some trackers to never receive acknowledgment and reconnect in a loop.

## In-Memory State

`services/device_registry.py` holds a single process-local dict, `connected_devices`, keyed by IMEI:

```python
{
  "<imei>": {
    "device_id": <DB device id>,
    "ip": <client ip>,
    "socket": <live socket object>,
    "connected_at": <datetime>,
    "last_seen": <datetime>
  }
}
```

This is the only source of truth for "is this device currently reachable" and "which socket do I send a command to." It is **not persisted** — if the gateway process restarts, all devices appear offline until they reconnect and re-login, even if their TCP connection from the tracker's side is still technically alive (the tracker would need to detect the drop and reconnect).

## Command Path (Server → Tracker)

```
console.py (operator types "where <imei>")
        │
        ▼
services/command_service.py: send_where()
        │
        ▼
utils/command_builder.py: build_where() → build_command("WHERE#")
        │  wraps ASCII command in a Protocol 0x80 packet, CRC16-X25 checksum
        ▼
services/device_registry.py: get_socket(imei)
        │  looks up the live socket for that IMEI
        ▼
sock.sendall(command)
        │
        ▼
Tracker executes command, replies with Protocol 0x21 (text response)
        │
        ▼
services/packet_handler.py: protocol "21" branch
        │  strips frame headers/CRC/trailer, decodes ASCII payload
        ▼
Printed to console
```

There is currently no persistence of command history or pairing of a sent command with its eventual `21` response (i.e. no request/response correlation by serial number) — responses are simply printed.

## Database Access Pattern

Every repository/service function that touches the database opens its own connection via `database.get_db_connection()`, executes one query, commits, and closes the connection — there is no connection pooling. See `docs/database.md` for the tables involved.

## Configuration

`config.py` defines `HOST`, `PORT`, `CONNECTION_TIMEOUT`, `LOW_BATTERY_THRESHOLD`, and `OVERSPEED_THRESHOLD`, all sourced from environment variables with defaults. Note that `tcp_server.py` currently reads `PORT` directly from `os.environ` rather than importing `config.py`, and the connection timeout (`conn.settimeout(60)`) is hardcoded rather than reading `config.CONNECTION_TIMEOUT`. `LOW_BATTERY_THRESHOLD` and `OVERSPEED_THRESHOLD` are defined but not currently referenced anywhere in the codebase (the low-battery comparison in the unused `status_service.py` uses a hardcoded `2` instead).
