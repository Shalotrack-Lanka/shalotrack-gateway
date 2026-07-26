# Import database connection helper and datetime for timestamps
from database import get_db_connection
from datetime import datetime, timezone


# Saves GPS tracking data to the GpsTrackings database table
# Parameters:   
#   device_id: unique identifier for the device sending the tracking data
#   latitude: latitude coordinate of the device's location
#   longitude: longitude coordinate of the device's location
#   speed: speed of the device at the time of tracking
#   heading: direction of travel in degrees (0-359), from the parsed packet
#   satellites: number of GPS satellites in view, from the parsed packet
#   event_time: timestamp of when the tracking event occurred
def save_tracking(
    device_id,
    latitude,
    longitude,
    speed,
    heading,
    satellites,
    event_time
):

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO "GpsTrackings"
        (
            "DeviceId",
            "Latitude",
            "Longitude",
            "Altitude",
            "Speed",
            "Heading",
            "Satellites",
            "GpsAccuracy",
            "EventTime",
            "CreatedAt"
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW()
        )
        """,
        (
            device_id,
            latitude,
            longitude,
            None,
            speed,
            heading,
            satellites,
            None,
            event_time
        )
    )

    conn.commit()

    cursor.close()
    conn.close()



def update_current_location(
    device_id,
    vehicle_id,
    latitude,
    longitude,
    speed,
    heading,
    event_time
):

    conn = get_db_connection()
    cursor = conn.cursor()

    # FIX: IgnitionStatus was previously hardcoded to False on every write, and
    # was missing from the ON CONFLICT DO UPDATE clause entirely -- meaning it
    # could never reflect the real ignition state, ever, once a device's row
    # existed. Location packets (protocol 12/22) don't carry ignition data
    # themselves (that comes from Status/Heartbeat packets, protocol 13/23), so
    # we look up the device's latest known ignition state from DeviceStatuses --
    # which IS correctly updated by update_device_status() below -- and use that
    # real value here instead. Costs one extra SELECT per location update.
    cursor.execute(
        """
        SELECT "IgnitionStatus" FROM "DeviceStatuses" WHERE "DeviceId" = %s
        """,
        (device_id,)
    )
    row = cursor.fetchone()
    ignition_status = row[0] if row else False

    cursor.execute(
        """
        INSERT INTO "CurrentLocations"
        (
            "DeviceId",
            "VehicleId",
            "Latitude",
            "Longitude",
            "Speed",
            "Heading",
            "IgnitionStatus",
            "MovementStatus",
            "LastUpdate"
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        ON CONFLICT ("DeviceId")
        DO UPDATE SET
            "VehicleId" = EXCLUDED."VehicleId",
            "Latitude" = EXCLUDED."Latitude",
            "Longitude" = EXCLUDED."Longitude",
            "Speed" = EXCLUDED."Speed",
            "Heading" = EXCLUDED."Heading",
            "LastUpdate" = EXCLUDED."LastUpdate",
            "MovementStatus" = EXCLUDED."MovementStatus",
            "IgnitionStatus" = EXCLUDED."IgnitionStatus"
        """,
        (
            device_id,
            vehicle_id,
            latitude,
            longitude,
            speed,
            heading,
            ignition_status,
            speed > 0,
            event_time
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


def update_device_status(
    device_id,
    battery_level,
    gps_signal,
    ignition_status,
    movement_status,
    power_status
):

    conn = get_db_connection()
    cursor = conn.cursor()

    now=datetime.now(timezone.utc)

    cursor.execute(
        """
        INSERT INTO "DeviceStatuses"
        (
            "DeviceId",
            "IsOnline",
            "LastHeartbeat",
            "LastSeen",
            "GpsSignal",
            "BatteryLevel",
            "IgnitionStatus",
            "MovementStatus",
            "PowerStatus",
            "UpdatedAt"
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )

        ON CONFLICT ("DeviceId")

        DO UPDATE SET

            "IsOnline" = EXCLUDED."IsOnline",
            "LastHeartbeat" = EXCLUDED."LastHeartbeat",
            "LastSeen" = EXCLUDED."LastSeen",
            "GpsSignal" = EXCLUDED."GpsSignal",
            "BatteryLevel" = EXCLUDED."BatteryLevel",
            "IgnitionStatus" = EXCLUDED."IgnitionStatus",
            "MovementStatus" = EXCLUDED."MovementStatus",
            "PowerStatus" = EXCLUDED."PowerStatus",
            "UpdatedAt" = EXCLUDED."UpdatedAt"
        """,
        (
            device_id,
            True,
            now,
            now,
            gps_signal,
            battery_level,
            ignition_status,
            movement_status,
            power_status,
            now
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


def update_heartbeat(device_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc)

    cursor.execute(
        """
        UPDATE "DeviceStatuses"
        SET
            "IsOnline" = TRUE,
            "LastHeartbeat" = %s,
            "LastSeen" = %s,
            "UpdatedAt" = %s
        WHERE "DeviceId"=%s
        """,
        (
            now,
            now,
            now,
            device_id
        )
    )

    conn.commit()
    cursor.close()
    conn.close()


def set_device_offline(device_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc)

    cursor.execute(
        """
        UPDATE "DeviceStatuses"

        SET

            "IsOnline" = FALSE,

            "UpdatedAt" = %s

        WHERE "DeviceId" = %s
        """,
        (
            now,
            device_id
        )
    )

    conn.commit()
    cursor.close()
    conn.close()


def update_last_seen(device_id):

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now(timezone.utc)

    cursor.execute(
        """
        UPDATE "DeviceStatuses"

        SET

            "LastSeen" = %s,

            "UpdatedAt" = %s

        WHERE "DeviceId" = %s
        """,
        (
            now,
            now,
            device_id
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


def set_device_online(device_id):

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now(timezone.utc)

    cursor.execute(
        """
        UPDATE "DeviceStatuses"

        SET

            "IsOnline" = TRUE,

            "LastSeen" = %s,

            "UpdatedAt" = %s

        WHERE "DeviceId" = %s
        """,
        (
            now,
            now,
            device_id
        )
    )

    conn.commit()

    cursor.close()
    conn.close()