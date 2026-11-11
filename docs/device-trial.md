# Device login rule and the 24-hour trial

Code: `services/device_access.py` (rule), `repositories/device_repository.py` (`_admit`). Applies to every
protocol (GT06, GPS103, Teltonika) because they all log in through `DeviceRepository.get_device_by_imei`.

## Modes (`DEVICE_TRIAL_MODE`)
| Mode | Behaviour |
|---|---|
| `off` (default) | Exactly the old rule: only `Status = 'Activated'` may connect. The new columns are not even read. |
| `shadow` | Old rule decides. Where the new rule would decide differently, a `TRIAL-SHADOW` line is logged. Nothing is written. |
| `enforce` | New rule decides. First connection of a trial device writes `FirstSeenAt`. |

`DEVICE_TRIAL_HOURS` (default 24) sets the window.

## The rule (enforce)
1. IMEI not in `SetupShalotrackDevices` -> refuse.
2. Status Temporarily Stopped / Pending Repair / Broken Device -> refuse.
3. Status Activated, or `BoundAt` set (office bound it to a customer's vehicle) -> accept, as today.
4. Otherwise (Not Activated, Assigned to Customer, unbound) -> accept for `DEVICE_TRIAL_HOURS` from the first
   connection (`FirstSeenAt`), then refuse until the office binds it.
Devices with a `GpsDevices` row but no setup row (legacy/demo) stay allowed, as before.

If `FirstSeenAt` cannot be written the device is refused (fail closed): accepting without a clock would make the
trial endless.

## Database privileges (DB2, role `gateway_app`)
Enforce mode needs one extra privilege, nothing more:
```sql
GRANT UPDATE ("FirstSeenAt") ON "SetupShalotrackDevices" TO gateway_app;
```
The gateway never writes `BoundAt` or `Status`; those belong to the ERP sync.

## Rollout
1. Deploy with `DEVICE_TRIAL_MODE` unset (= off). No change.
2. Set `shadow`, watch the logs for a few days (`grep TRIAL-SHADOW`).
3. Run the GRANT, set `enforce`. Rollback = set back to `off`/`shadow` and restart; stamped `FirstSeenAt` values are harmless.
