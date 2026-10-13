from database import managed_connection
from datetime import datetime, timezone
from utils.logger import log
# Every float bound to a numeric column MUST go through clean_decimal — see
# utils/numeric.py for the 2026-10-10 incident this prevents.
from utils.numeric import clean_decimal as _clean_decimal


async def save_tracking(device_id, latitude, longitude, speed, heading, satellites, event_time):
    async with managed_connection() as conn:
        await conn.execute(
            """
            INSERT INTO "GpsTrackings"
            ("DeviceId","Latitude","Longitude","Altitude","Speed","Heading","Satellites","GpsAccuracy","EventTime","CreatedAt")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            """,
            device_id, _clean_decimal(latitude), _clean_decimal(longitude), None,
            _clean_decimal(speed, 2), _clean_decimal(heading, 2), satellites, None, event_time
        )


async def update_current_location(device_id, vehicle_id, latitude, longitude, speed, heading, event_time):
    async with managed_connection() as conn:
        ignition_status = await conn.fetchval(
            'SELECT "IgnitionStatus" FROM "DeviceStatuses" WHERE "DeviceId" = $1',
            device_id
        )
        ignition_status = ignition_status if ignition_status is not None else False

        await conn.execute(
            """
            INSERT INTO "CurrentLocations"
            ("DeviceId","VehicleId","Latitude","Longitude","Speed","Heading","IgnitionStatus","MovementStatus","LastUpdate")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT ("DeviceId") DO UPDATE SET
                "VehicleId"      = EXCLUDED."VehicleId",
                "Latitude"       = EXCLUDED."Latitude",
                "Longitude"      = EXCLUDED."Longitude",
                "Speed"          = EXCLUDED."Speed",
                "Heading"        = EXCLUDED."Heading",
                "LastUpdate"     = EXCLUDED."LastUpdate",
                "MovementStatus" = EXCLUDED."MovementStatus",
                "IgnitionStatus" = EXCLUDED."IgnitionStatus"
            """,
            device_id, vehicle_id, _clean_decimal(latitude), _clean_decimal(longitude),
            _clean_decimal(speed, 2), _clean_decimal(heading, 2), ignition_status, speed > 0, event_time
        )


async def update_device_status(device_id, battery_level, gps_signal, ignition_status, movement_status, power_status):
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            """
            INSERT INTO "DeviceStatuses"
            ("DeviceId","IsOnline","LastHeartbeat","LastSeen","GpsSignal","BatteryLevel","IgnitionStatus","MovementStatus","PowerStatus","UpdatedAt")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT ("DeviceId") DO UPDATE SET
                "IsOnline"       = EXCLUDED."IsOnline",
                "LastHeartbeat"  = EXCLUDED."LastHeartbeat",
                "LastSeen"       = EXCLUDED."LastSeen",
                "GpsSignal"      = EXCLUDED."GpsSignal",
                "BatteryLevel"   = EXCLUDED."BatteryLevel",
                "IgnitionStatus" = EXCLUDED."IgnitionStatus",
                "MovementStatus" = EXCLUDED."MovementStatus",
                "PowerStatus"    = EXCLUDED."PowerStatus",
                "UpdatedAt"      = EXCLUDED."UpdatedAt"
            """,
            device_id, True, now, now, gps_signal, battery_level, ignition_status, movement_status, power_status, now
        )


async def mark_device_alive(device_id, heartbeat: bool = False):
    """
    Mark a device online and refresh LastSeen (and LastHeartbeat when
    `heartbeat` is True) WITHOUT touching battery/signal/ignition/power.

    For protocols whose heartbeat carries no telemetry (GPS103: the heartbeat
    is just the IMEI). update_device_status() cannot be used there: it
    overwrites BatteryLevel/GpsSignal with whatever it is given, and
    DeviceStatuses.BatteryLevel / GpsSignal / PowerStatus are NOT NULL, so we
    cannot pass "unknown" either.

    First contact (no DeviceStatuses row yet) inserts neutral defaults:
    BatteryLevel 0, GpsSignal 0, PowerStatus 1 (normal). BatteryLevel 0 is a
    placeholder meaning "not reported" -- see docs/multi-protocol.md.
    """
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            """
            INSERT INTO "DeviceStatuses"
            ("DeviceId","IsOnline","LastHeartbeat","LastSeen","GpsSignal","BatteryLevel","IgnitionStatus","MovementStatus","PowerStatus","UpdatedAt")
            VALUES ($1, TRUE, $2, $3, 0, 0, FALSE, FALSE, 1, $3)
            ON CONFLICT ("DeviceId") DO UPDATE SET
                "IsOnline"      = TRUE,
                "LastHeartbeat" = COALESCE(EXCLUDED."LastHeartbeat", "DeviceStatuses"."LastHeartbeat"),
                "LastSeen"      = EXCLUDED."LastSeen",
                "UpdatedAt"     = EXCLUDED."UpdatedAt"
            """,
            device_id, now if heartbeat else None, now
        )


async def set_ignition(device_id, ignition_status: bool):
    """
    Record ignition state only (GPS103 reports it via 'acc on' / 'acc off'
    alarms and an optional flag in position reports). Same insert-defaults
    rule as mark_device_alive().
    """
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            """
            INSERT INTO "DeviceStatuses"
            ("DeviceId","IsOnline","LastHeartbeat","LastSeen","GpsSignal","BatteryLevel","IgnitionStatus","MovementStatus","PowerStatus","UpdatedAt")
            VALUES ($1, TRUE, NULL, $2, 0, 0, $3, FALSE, 1, $2)
            ON CONFLICT ("DeviceId") DO UPDATE SET
                "IgnitionStatus" = EXCLUDED."IgnitionStatus",
                "LastSeen"       = EXCLUDED."LastSeen",
                "UpdatedAt"      = EXCLUDED."UpdatedAt"
            """,
            device_id, now, bool(ignition_status)
        )


async def update_heartbeat(device_id):
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            """
            UPDATE "DeviceStatuses"
            SET "IsOnline" = TRUE, "LastHeartbeat" = $1, "LastSeen" = $2, "UpdatedAt" = $3
            WHERE "DeviceId" = $4
            """,
            now, now, now, device_id
        )


async def set_device_offline(device_id):
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            'UPDATE "DeviceStatuses" SET "IsOnline" = FALSE, "UpdatedAt" = $1 WHERE "DeviceId" = $2',
            now, device_id
        )


async def update_last_seen(device_id):
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            'UPDATE "DeviceStatuses" SET "LastSeen" = $1, "UpdatedAt" = $2 WHERE "DeviceId" = $3',
            now, now, device_id
        )


async def set_device_online(device_id):
    now = datetime.now(timezone.utc)
    async with managed_connection() as conn:
        await conn.execute(
            'UPDATE "DeviceStatuses" SET "IsOnline" = TRUE, "LastSeen" = $1, "UpdatedAt" = $2 WHERE "DeviceId" = $3',
            now, now, device_id
        )