# Power-cut and offline alerts

## What reaches the phone, and why it used to be missing

A push notification is produced by the **API**, not the gateway. Two things drive it:

| Alert | Source | Driven by |
|---|---|---|
| Power disconnected (`PowerCut`) | API `LocationNotificationListener` | change of `DeviceStatuses.PowerStatus` (Postgres trigger → `device_status_updates`) |
| Device offline (`DeviceOffline`) | API `DeviceOfflineAlertWorker` | `DeviceStatuses.LastSeen` older than 15 min (see the API repo's `docs/device-offline-alerts.md`) |

Before this change only GT06 units that put the power bit in their *status / heartbeat* packets ever
changed `PowerStatus`. A power-cut **alarm** from any family only wrote a row to `DeviceEvents`, which
nothing turns into a push. The gateway now writes `PowerStatus` for all four families through
`tracking_service.set_power_status(device_id, disconnected)`. Write it on the **transition only**:
the trigger fires on every UPDATE.

## Per family

| Family | Power cut is detected from | Power restored is detected from |
|---|---|---|
| GT06 | status/heartbeat power bit (unchanged); **new:** alarm packet type 2 or its power bit | next status/heartbeat packet (unchanged) |
| GPS103 / TK103 | `ac alarm` message | **not reported by the device.** Reset at the next login, or at the first message more than `GPS103_POWER_CUT_RESET_SECONDS` (default 600) after the alarm. `0` disables the timed reset |
| H02 / LK208 | status word bit 19 cleared | bit 19 set again |
| Teltonika FMB | IO 252 ("Unplug") = 1 | IO 252 = 0 |

H02 and Teltonika power detection follow the published protocol documents and are **not yet checked on a
physical unit**. The H02 path sits behind `H02_STATUS_FLAGS=true` (off by default, see `docs/h02.md`);
Teltonika is on as soon as the unit reports IO 252 (configure it in the Teltonika Configurator).
A battery-powered tracker (LK208) has no external power to cut, so this never fires for it.

## GPS103 trade-off

A TK103 never says "power back". After an alarm the gateway marks power disconnected and restores it on
login or after the reset window. Consequences: a unit that stays unplugged and reconnects (it has a
backup battery) is reported as restored at that login; a unit that is restored quickly shows
"disconnected" until the window passes. Pick the window to match the reporting interval of your units.

## Verifying on a real unit

1. Unplug the supply. Within one report: `🚨 Alarm event recorded: POWER_CUT` (GPS103/H02) and
   `SELECT "PowerStatus" FROM "DeviceStatuses" WHERE "DeviceId" = ...` returns `1`.
2. The owner's phone receives "<vehicle>: PowerCut / Device power disconnected" (needs an active
   vehicle assignment and a registered FCM token).
3. Plug back in and confirm `PowerStatus` returns to `0`.
