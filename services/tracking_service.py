from database import managed_connection
from datetime import datetime, timezone
from utils.logger import log


def save_tracking(device_id, latitude, longitude, speed, heading, satellites, event_time):
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO "GpsTrackings"
            ("DeviceId","Latitude","Longitude","Altitude","Speed","Heading","Satellites","GpsAccuracy","EventTime","CreatedAt")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (device_id, latitude, longitude, None, speed, heading, satellites, None, event_time)
        )
        conn.commit()
        cursor.close()


def update_current_location(device_id, vehicle_id, latitude, longitude, speed, heading, event_time):
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT "IgnitionStatus" FROM "DeviceStatuses" WHERE "DeviceId" = %s',
            (device_id,)
        )
        row = cursor.fetchone()
        ignition_status = row[0] if row else False

        cursor.execute(
            """
            INSERT INTO "CurrentLocations"
            ("DeviceId","VehicleId","Latitude","Longitude","Speed","Heading","IgnitionStatus","MovementStatus","LastUpdate")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            (device_id, vehicle_id, latitude, longitude, speed, heading, ignition_status, speed > 0, event_time)
        )
        conn.commit()
        cursor.close()


def update_device_status(device_id, battery_level, gps_signal, ignition_status, movement_status, power_status):
    now = datetime.now(timezone.utc)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO "DeviceStatuses"
            ("DeviceId","IsOnline","LastHeartbeat","LastSeen","GpsSignal","BatteryLevel","IgnitionStatus","MovementStatus","PowerStatus","UpdatedAt")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            (device_id, True, now, now, gps_signal, battery_level, ignition_status, movement_status, power_status, now)
        )
        conn.commit()
        cursor.close()


def update_heartbeat(device_id):
    now = datetime.now(timezone.utc)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE "DeviceStatuses"
            SET "IsOnline" = TRUE, "LastHeartbeat" = %s, "LastSeen" = %s, "UpdatedAt" = %s
            WHERE "DeviceId" = %s
            """,
            (now, now, now, device_id)
        )
        conn.commit()
        cursor.close()


def set_device_offline(device_id):
    now = datetime.now(timezone.utc)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE "DeviceStatuses" SET "IsOnline" = FALSE, "UpdatedAt" = %s WHERE "DeviceId" = %s',
            (now, device_id)
        )
        conn.commit()
        cursor.close()


def update_last_seen(device_id):
    now = datetime.now(timezone.utc)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE "DeviceStatuses" SET "LastSeen" = %s, "UpdatedAt" = %s WHERE "DeviceId" = %s',
            (now, now, device_id)
        )
        conn.commit()
        cursor.close()


def set_device_online(device_id):
    now = datetime.now(timezone.utc)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE "DeviceStatuses" SET "IsOnline" = TRUE, "LastSeen" = %s, "UpdatedAt" = %s WHERE "DeviceId" = %s',
            (now, now, device_id)
        )
        conn.commit()
        cursor.close()