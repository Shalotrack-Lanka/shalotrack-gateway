# Import database connection helper and datetime for timestamps
from database import get_db_connection
from datetime import datetime


# Saves GPS tracking data to the GpsTrackings database table
# Parameters:   
#   device_id: unique identifier for the device sending the tracking data
#   latitude: latitude coordinate of the device's location
#   longitude: longitude coordinate of the device's location
#   speed: speed of the device at the time of tracking
#   event_time: timestamp of when the tracking event occurred
def save_tracking(
    device_id,
    latitude,
    longitude,
    speed,
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
            0,
            0,
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
    event_time
):

    conn = get_db_connection()
    cursor = conn.cursor()

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
            "LastUpdate" = EXCLUDED."LastUpdate",
            "MovementStatus" = EXCLUDED."MovementStatus"
        """,
        (
            device_id,
            vehicle_id,
            latitude,
            longitude,
            speed,
            0,
            False,
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

    now=datetime.utcnow()

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
