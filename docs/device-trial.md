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

## 5-minute re-check and drop (Phase C2)
The login rule only runs when a device connects. A device already connected when it is stopped would stay online,
so the gateway also re-checks every connected device on a timer, and the ERP/API can drop one on demand.

| Env var | Default | Meaning |
|---|---|---|
| `DEVICE_RECHECK_MODE` | `off` | `off` nothing; `shadow` logs `RECHECK-SHADOW would drop ...`; `enforce` closes the socket |
| `DEVICE_RECHECK_SECONDS` | `300` | Interval |
| `DEVICE_RECHECK_MAX_DROP_FRACTION` | `0.2` | Safety valve: if one cycle would drop more than this share of connected devices (min allowance 5), it drops NOTHING and logs an alarm (a bad sync, not real stops) |

- One batched query per 1000 connected IMEIs. A database error drops nothing.
- Uses the same rule as login (trial rule when `DEVICE_TRIAL_MODE=enforce`, otherwise the old Activated rule).
  A connected device whose trial clock is missing is kept, never cut.
- Legacy devices with no setup row are never dropped.
- A dropped device cannot log back in because the login rule refuses it. Dropped devices keep recording up to the drop.

### `POST /drop` (Command API, same `X-Command-Api-Key`)
Body `{"imei": "<digits 5-20>", "reason": "<=60 chars>"}` -> `{"success": true, "imei": "...", "was_connected": bool}`.
Works in any mode (it is an explicit order). Offline device = `was_connected: false`, not an error.

### Rollout
1. Deploy with `DEVICE_RECHECK_MODE` unset (off). 2. Set `shadow`, watch `RECHECK-SHADOW` for a few days. 3. Set `enforce`.
Rollback: set back to `off` and restart.

### `GET /access` (Command API, same key) - read-only report for the ERP "Gateway Devices" page
Returns `trial_mode`, `recheck_mode`, `connected_now`, `counts` (allowed / trial / trial_not_started / trial_expired / stopped)
and up to 1000 `devices` that need attention or are connected, with trial hours left. It answers "what would the trial
rule do to each device right now" whatever the mode is. Nothing is written. A database error returns 503 with no details.
