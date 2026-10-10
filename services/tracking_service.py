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