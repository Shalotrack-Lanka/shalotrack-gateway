# Adding a device family

All device families share one TCP port. The family is decided once per
connection from its first bytes, then fixed for that connection. Everything
after the protocol-specific parsing is shared, so a new family is a small,
self-contained change.

```
first bytes ──► protocols/detect.py ──► tcp_server.PROTOCOL_PIPELINES[family]
                                          (framer, handler)
frame ──► handler ──► DeviceRepository / device_registry / save_location /
                      create_event / RawPacketRepository      (shared, unchanged)
```

## Families today

| Family | Detected by | Framer | Handler | Enabled by default |
|---|---|---|---|---|
| `GT06` (V5, Concox/Jimi 2G) | `78 78` / `79 79` | `utils/packet_buffer.extract_packets` | `services/packet_handler.py` | yes |
| `GPS103` (TK103A/B) | `##` / `imei:` | `parsers/gps103_parser.extract_messages` | `services/gps103_handler.py` | yes |
| `TELTONIKA` (FMB, Codec 8/8E) | `00 0F` + 15 digits | `parsers/teltonika_parser.extract_frames` | `services/teltonika_handler.py` | **no** |

Switch families with `ENABLED_PROTOCOLS` (comma list, e.g.
`GT06,GPS103,TELTONIKA`). A family that is not listed behaves exactly like
unknown traffic: the connection is closed. A new release never turns a new
family on by itself.

## Checklist

Do these in order. Nothing here touches the existing families.

1. **Get real packets first.** Never write a parser from a datasheet alone.
   Capture raw bytes from the device (point it at the gateway with
   `RAW_PACKET_MODE=all`; unrecognised traffic is logged with its first bytes).
   A wrong parser writes wrong coordinates into production data, which is worse
   than refusing the device.
2. **Parser** `parsers/<family>_parser.py`: pure functions, no sockets, no
   database. Provide a framer `extract_frames(buffer) -> (frames, rest)` that
   raises on a stream that cannot be this family or exceeds a size limit
   (never buffer unboundedly), plus decoders returning plain objects.
3. **Detection rule** in `protocols/detect.py` (`_sniff`): a constant and one
   signature check. It must not match the other families' first bytes.
4. **Handler** `services/<family>_handler.py` with the standard contract:
   `async def handler(frame, writer, addr) -> imei | None`, returning the IMEI
   once authenticated. Follow the rules the existing handlers share:
   - strict login: unknown IMEI means close, no reply;
   - bind the socket to the IMEI at login (`register_device`), and drop data
     that arrives before login or names a different IMEI;
   - store positions only through `tracking_service.save_location`
     (one statement, correct numeric handling);
   - independent writes: a failed position write must not stop an alarm;
   - never trust device input in log lines (cap and escape it).
5. **Register**: add the name to `config._ALL_PROTOCOLS`, a row in
   `tcp_server.PROTOCOL_PIPELINES`, and the export in `protocols/__init__.py`.
6. **Tests** (copy the Teltonika pair as the template):
   - parser unit tests anchored to a REAL captured or vendor-published packet,
     including truncated, corrupted and oversized input;
   - an end-to-end test over real sockets with only the database mocked;
   - run a mutation check: break the decoder on purpose and confirm a test fails.
7. **Document** the family in this table and give the device-side
   configuration steps (server address, port, protocol/codec, interval).
8. **Roll out**: deploy with the family disabled, enable it with
   `ENABLED_PROTOCOLS`, connect ONE device, verify the log and
   `CurrentLocations`/`GpsTrackings`, then add more.

## Decisions to make per family, and why

- **Acknowledgement semantics.** Devices that buffer records (Teltonika) keep
  them until the server confirms. Confirm only what was stored, so a database
  failure loses nothing. Devices that fire and forget (GPS103) need no such care.
- **Battery / signal.** Units differ per protocol (GT06 reports a 0-6 level,
  Teltonika reports percent and millivolts). Do not write them into
  `DeviceStatuses` until the portal defines one scale.
- **Commands.** Outbound commands are protocol-specific
  (`utils/command_builder.py` is V5-only today). Unsupported families return
  501 rather than send something wrong to a vehicle.
- **Authentication.** Every family here authenticates by IMEI only, which is an
  identifier, not a secret. See the known gap in `docs/multi-protocol.md`.

## Not covered by this structure yet

- **Queclink and Meitrack**: not implemented. Both need real sample packets
  from the actual models (their formats vary by model and firmware).
- **Concox/Jimi 4G**: the 4G models extend the GT06 protocol with new message
  types. An unknown message type from a known device is already stored raw
  (`RawPackets`, always) and acknowledged without parsing, so a capture will
  show exactly what to add to the GT06 parser.
