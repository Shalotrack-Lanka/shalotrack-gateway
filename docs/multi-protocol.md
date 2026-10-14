# Multi-protocol support (GT06 + GPS103 / TK103A/B)

The gateway serves several device families on **one TCP port**. The family is decided once per connection from the first bytes the device sends, then fixed for that connection's lifetime. No new NLB listener, security-group rule, Docker port or SSM parameter is needed to add a family.

| Family | First bytes | Devices | Framing | Status |
|---|---|---|---|---|
| `GT06` | `78 78` / `79 79` | V5 (2G); V5 (4G) expected to match | binary, length-prefixed, CRC16-X25 | production |
| `GPS103` | `##` or `imei:` | TK103A/B (Coban family) | ASCII, `;`-terminated | inbound only |
| anything else | — | — | — | connection closed |

## Request flow

```
TCP connect → first read (≥5 bytes, 1s probe timeout)
        │
        ▼
protocols/detect.py: detect_protocol(first_bytes)
        │                       │ None → log first 32 bytes, close
        ▼
tcp_server.PROTOCOL_PIPELINES[family] = (framer, handler)
        │
        ├─ GT06   : utils/packet_buffer.extract_packets → services/packet_handler.process_packet
        └─ GPS103 : parsers/gps103_parser.extract_messages → services/gps103_handler.process_gps103_message
                          │
                          ▼
              shared: device_registry, DeviceRepository, tracking_service, event_service
```

Both handlers share one contract: `handler(frame, writer, addr) -> imei | None` (the IMEI once the socket is authenticated).

## GPS103 message set (inbound)

| Device sends | Gateway does | Gateway replies |
|---|---|---|
| `##,imei:<id>,A;` (login) | allowlist check via `DeviceRepository.get_device_by_imei`, register, `DEVICE_ONLINE` event | `LOAD` (rejected IMEI: close, no reply) |
| `<id>;` (heartbeat) | refresh `LastSeen` / `LastHeartbeat` | `ON` |
| `imei:<id>,tracker,…,F,…,A,…;` | validate, save to `GpsTrackings`, upsert `CurrentLocations` | none |
| `imei:<id>,help me,…;` | save position, `SOS` event (severity HIGH) | `**,imei:<id>,E;` |
| other alarm keywords | save position + event | none |

Alarm keyword → event name: `help me`→`SOS`, `low battery`→`LOW_BATTERY`, `stockade`→`GEOFENCE`, `move`→`MOVE`, `speed`→`OVERSPEED`, `ac alarm`→`POWER_CUT`, `door alarm`→`DOOR_OPEN`, `sensor alarm`→`SENSOR`, `acc on`/`acc off`→`ACC_ON`/`ACC_OFF` (also updates ignition). `SOS` and `POWER_CUT` are HIGH severity; the rest MEDIUM. An unrecognised keyword is logged and treated as a plain position report (no event), so a command-acknowledgement message can't spam the events table.

### Field decoding rules

* Lat/lon are `ddmm.mmmm` / `dddmm.mmmm` (degrees + decimal minutes), signed by the N/S/E/W letter.
* Speed is **knots**, converted to km/h to match `GpsTrackings.Speed` for V5. Flip `GPS103_SPEED_IN_KMH=true` if a unit turns out to send km/h.
* Timestamp: the device sends its *local* date/time plus a UTC time-of-day. The gateway rebuilds an aware UTC `datetime` from both (offset wrapped to ±12h), so it is correct for any timezone and across midnight.
* A report is stored as a position only when it is an `F` block flagged `A`, within physical bounds, and not exactly `0,0`. LBS-only (`L`) and `V` reports store nothing. An alarm inside such a report is still recorded, without coordinates.
* Satellites are not reported → stored as `0`.

## Security properties

* Same admin allowlist as V5 (`SetupShalotrackDevices.Status = 'Activated'`).
* A socket is bound to the IMEI it logged in with. Any later message naming a different IMEI drops the connection.
* Data before login drops the connection. No reply is ever sent to a rejected IMEI.
* `GPS103_MAX_UNFRAMED_BYTES` (default 2048): a stream with no `;` terminator beyond that is dropped, so a client cannot make the gateway buffer without bound.
* Log lines containing device input are escaped and length-capped (no log injection through CR/LF).
* The HTTP command API refuses commands for GPS103 devices (`501`) instead of writing a GT06 binary frame into a TK103's socket.

**Known gap (applies to V5 as well):** the IMEI is an identifier, not a secret. Anyone who learns a valid IMEI can log in as that device, and a new login replaces the existing registration. Mitigations worth scheduling: private APN / SIM-provider IP allowlisting for the NLB, per-IP failed-login rate limiting, and logging every login with source IP (already logged).

## Not done yet (deliberately out of scope for this change)

1. **Outbound commands for TK103** (relay cut, interval, SMS-style config). Needs `utils/command_builder` split per protocol; commands already return `501` for these devices.
2. **Battery level.** The GPS103 heartbeat/position carry none. `DeviceStatuses.BatteryLevel` is `NOT NULL`, so first contact inserts `0` as a "not reported" placeholder. The API/app should treat `0` from a GPS103 device as unknown, not as a dead battery (or the schema should allow NULL).
3. **Admin portal:** the device-category dropdown must offer a TK103 option, otherwise `GpsDevices.DeviceModel` falls back to the literal `"TK103"` on auto-registration.
4. **V5 (4G).** Expected to be GT06-compatible, but unverified. Capture its first packets before assuming. If it differs, add a family with one `detect.py` rule and one `PROTOCOL_PIPELINES` row.
5. Hardware verification of speed units and the exact login/heartbeat strings (see below).

## Verifying a real unit

1. `python test/smoke_test_gps103.py <imei> <host> <port>` against staging proves the server side without hardware.
2. Point the unit at the gateway and capture its first connect (the gateway logs `RAW DATA:` hex per message; `Unrecognised protocol … First bytes:` is logged for anything it cannot classify). Decode the hex as ASCII and compare with the table above.
3. If the unit starts with `78 78` it is speaking GT06 despite the TK103 label: it will work through the existing V5 path with no code change. If it logs `Unrecognised protocol`, send the logged bytes to the team before writing a new parser.
4. Drive the unit and compare reported speed with the vehicle speedometer (knots vs km/h).

### Device provisioning notes (verify against the unit's own manual)

TK103A/B units are configured by SMS from the owner's phone; commonly `adminip<password> <host> <port>` and `apn<password> <apn>`. The factory password is `123456`. **Change it and restrict authorised numbers before deployment**: anyone who knows a SIM number and the default password can repoint the unit at their own server. The exact command syntax varies by firmware; the original vendor protocol document is a binary `.doc` (mirrored on traccar.org) that could not be retrieved while building this, so the message formats here follow Traccar's open-source `gps103` decoder and its published fixtures.

## Tests

```bash
python -m unittest discover -s test -p "test_gps103*.py" -v          # parser, framer, detection (pure)
python -m unittest discover -s test -p "test_multiprotocol*.py" -v   # real server on localhost, DB mocked
```

The end-to-end suite also pins V5 behaviour (login ACK, location save, coexistence on one port) and the reconnect race fix below.

## Related fix shipped with this change

`tcp_server.handle_device` used to mark a device offline and unregister it whenever *any* of its sockets closed. TK103-class units on flaky GPRS often reconnect before the server notices the old socket is dead; the old socket's cleanup then knocked the healthy new connection offline. Cleanup now only runs when the registry entry still belongs to the closing socket (`test_stale_connection_closing_does_not_knock_a_reconnected_device_offline` fails without the guard). It also falls back to the registry lookup so a handler that raises after registering cannot leak an entry.
