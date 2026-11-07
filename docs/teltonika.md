# Teltonika FMB (Codec 8 / 8E)

**Status: implemented and unit/integration tested; NOT yet verified on a
physical FMB unit.** Verify the first device end to end before adding more.

## What is supported

- TCP, Codec 8 and Codec 8 Extended, IMEI login, CRC-16 verification.
- Position records into `GpsTrackings` and the live `CurrentLocations` row, via
  the same single-statement write as every other family.
- Ignition from AVL IO 239, written only when it changes.
- Online status (login, and a LastSeen refresh at most once a minute).
- Records without a GPS fix, with an impossible position, or stamped more than
  `TELTONIKA_MAX_FUTURE_SECONDS` (default 600) in the future are skipped but
  acknowledged, so the device does not resend them forever.

## What is not (yet)

- Alarm / event IOs (panic button, towing, geofence), Codec 12 command
  responses, outbound commands (they return 501), UDP, Codec 16 and older.
- Battery and signal are not written (units differ between protocols).
- A packet in an unsupported codec is NOT acknowledged. The raw bytes are
  stored (`RawPackets`, `parsed = false`) so the codec can be added from a real
  capture. The device will keep retrying that packet until then.

## Delivery guarantee

Teltonika devices keep records until the server says how many it took. The
gateway acknowledges only records it stored (or skipped on purpose). If the
database write fails, it acknowledges fewer, and the device resends the rest.
Resends can create duplicate rows unless `GPS_TRACKING_IDEMPOTENT=true` is on,
which needs the unique index first: follow `docs/gps-tracking-idempotency.md`.
For Teltonika fleets that buffer offline, do that before going beyond a pilot.

## Turning it on

Off by default. In the gateway's environment:

```
ENABLED_PROTOCOLS=GT06,GPS103,TELTONIKA
```

The container start script is `gateway-user-data.sh` in the infrastructure
repo (add `-e ENABLED_PROTOCOLS=...` to the `docker run`). The startup log
prints `Protocols enabled: ...`.

Register the device's IMEI in `SetupShalotrackDevices` with
`Status = 'Activated'`, as for every device. The `GpsDevices` row is created
on first login with `ProtocolType = TELTONIKA` and model `FMB` unless
`DeviceCategory` is set.

## Device configuration

Use Teltonika Configurator (USB or Bluetooth) rather than SMS where you can:

- GPRS: the SIM's APN.
- Server: domain `gateway.shalotrack.com`, port `8000` (the NLB listener),
  protocol **TCP**.
- Data protocol / codec: **Codec 8 Extended** (or Codec 8).
- Data acquisition: the periods you want when moving, stopped and on stop.
- Ignition: confirm AVL ID 239 is enabled (it is by default on most FMB models).

SMS equivalent, for the parameter ids as published for FMB models (check them
against Teltonika's parameter list for your exact model and firmware before
sending; the default SMS login and password are empty, which is why the
command starts with two spaces):

```
  setparam 2001:<apn>;2004:gateway.shalotrack.com;2005:8000;2006:0
```

Parameters 2001 = APN, 2004 = server domain, 2005 = server port,
2006 = data protocol (0 = TCP).

## First-device check

1. `docker logs -f shalotrack-gateway` and power-cycle the device.
2. Expect `Device connected ... [TELTONIKA]`, `Teltonika login`, `Authorised
   Device`, then position lines and `Reply sent: 0000000N`.
3. `CurrentLocations.LastUpdate` advances and the marker moves on the map.
4. Tell-tale problems: `CRC mismatch` (corrupted stream), `unsupported codec`
   (device is on Codec 16 or similar), `Unrecognised protocol` (family not
   enabled, or the device is not sending the IMEI login first).

## Tests

```
python -m unittest discover -s test -p "test_teltonika*.py" -v
```

The CRC test uses the Codec 8 example published in Teltonika's own protocol
documentation, so a wrong CRC polynomial cannot pass.
