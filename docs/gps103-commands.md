# Commands for GPS103 (TK103A/B) devices

The ERP's Device Command Center sends `{imei, command}` to the gateway's Command
API. For GT06/V5 devices the gateway builds a binary frame (`utils/command_builder.py`).
A TK103 does not understand those frames, so GPS103 devices have their own path:

```
portal ──► Command API (X-Command-Api-Key) ──► device protocol?
                                                 ├─ GPS103 ──► services/gps103_command_service.py
                                                 │              └─ utils/gps103_command_builder.py  (text frame)
                                                 └─ GT06   ──► services/command_service.py          (unchanged)
```

The portal needs no change: it shows whatever message the gateway returns.

## What each portal button does for a TK103

| Portal command | TK103 behaviour | How |
|---|---|---|
| **Where**, `position`, `url` | Works | Sends `**,imei:<imei>,B;`. The tracker answers with a normal position report, which the gateway already stores; the marker moves. There is no separate "command response" row, the new position is the result. |
| **Relay On** (restore engine) | Works | Sends `**,imei:<imei>,K;` |
| **Relay Off** (immobilise) | **Refused on purpose** | Not exposed in the portal to any role. The API also refuses it. See "Safety". |
| Status, Version, IMEI, Params, GPRS settings, APN/Server/Timer queries | Not over the data connection | Returns HTTP 501 with a clear message. Use SMS (below). |
| Reset, Timer, Distance, Speed/Moving/Battery/Power alarms, Geofence, SOS numbers, APN, Server | Not over the data connection | Same: SMS only. |

Everything works only while the device is connected; an offline device returns
503 "Device offline", as for V5.

## SMS equivalents (operators only, do not show to customers)

These contain the device password, so they stay out of the portal's messages.
Default password `123456`, which must be changed. Syntax varies by clone; check
with `check<password>` after each change.

| Need | SMS |
|---|---|
| Read settings and state | `check<password>` |
| Server | `adminip<password> <host-or-ip> <port>` |
| APN | `apn<password> <apn>` |
| Report every N seconds | `t005s***n<password>` (005 = 5 s) |
| Change password | `password<old><new>` |

## Safety

- **Engine cut (`J`) is not implemented.** Immobilising a vehicle that may be
  moving can cause a crash, and this clone's relay wiring and behaviour cannot be
  verified from here. If the business needs it, build it with: a speed
  interlock (refuse above a few km/h using the last known speed), an explicit
  confirmation step in the portal, audit logging of who sent it, and a test on a
  parked vehicle first.
- The builder validates the IMEI as ASCII digits only, because it is
  interpolated into bytes sent to a vehicle. A hand-made request cannot add
  characters to the frame, and the gateway only writes to an IMEI that is
  currently connected.
- The Command API still requires `X-Command-Api-Key`; nothing here changes
  authentication.

## Verification status

The frame formats come from Traccar's open-source gps103 command encoder and are
tested (parser, service, and the whole HTTP-to-socket path). They have **not**
been verified on a physical TK103; clones differ. First real use:

1. `docker logs -f shalotrack-gateway`
2. Press **Where** for the TK103 in the portal.
3. Expect `Sending GPS103 command 'where' ... **,imei:...,B;` and, shortly after,
   a position report from the tracker (needs a GPS fix) and an updated marker.
4. If the tracker never answers, the unit does not accept GPRS commands in this
   format. Leave Where on SMS for that model and tell us which firmware it is.

## Possible next steps (decisions, not built)

- **Hide the buttons a TK103 cannot do** in the portal (Laravel), so operators do
  not click Status and get a 501. Small change in `shalotrack-admin`.
- **Send the SMS commands from the portal** through the SMS provider already used
  for OTP, so Status/Timer/Params work for TK103. It costs one SMS per command and
  needs the device password stored securely per device, so decide that deliberately.
- **Command responses**: a GPS103 tracker's reply to a command is not stored
  today (V5 replies are stored in `CommandResponses`).
