# Import database connection helper and datetime for timestamps
from database import get_db_connection
from datetime import datetime

# Saves raw GPS device packet data to the RawPackets database table
# Parameters:
#   device_id: unique identifier for the device sending the packet
#   protocol_number: protocol version/type identifier
#   raw_hex: hexadecimal string representation of the packet data
#   parsed: boolean indicating whether the packet was successfully parsed
def save_raw_packet(
    device_id,
    protocol_number,
    raw_hex,
    parsed
):

    # Establish connection to the database
    conn = get_db_connection()
    cursor = conn.cursor()

    # Insert the raw packet record into RawPackets table
    # Calculated fields:
    #   PacketLength: derived from hex string length (divided by 2 since each byte = 2 hex chars)
    #   ReceivedAt: current UTC timestamp of when packet was received
    cursor.execute(
        """
        INSERT INTO "RawPackets"
        (
            "DeviceId",
            "ProtocolNumber",
            "RawHex",
            "PacketLength",
            "ReceivedAt",
            "Parsed"
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        """,
        (
            device_id,
            protocol_number,
            raw_hex,
            len(raw_hex) // 2,
            datetime.utcnow(),
            parsed
        )
    )

    # Commit the transaction to persist data to the database
    conn.commit()

    # Clean up database resources
    cursor.close()
    conn.close()


# Retrieves a device ID from the database by searching for a specific IMEI number
# Parameters:
#   imei: the IMEI (International Mobile Equipment Identity) number to search for
# Returns:
#   Device ID as a string if device is found, None if not found
def get_device_by_imei(imei):

    # Establish connection to the database
    conn = get_db_connection()
    cursor = conn.cursor()

    # Query the GpsDevices table to find a device with the specified IMEI number
    cursor.execute(
        """
        SELECT "DeviceId"
        FROM "GpsDevices"
        WHERE "ImeiNumber" = %s
        """,
        (imei,)
    )

    # Fetch the first result (IMEI numbers are unique, so only one result possible)
    result = cursor.fetchone()

    # Clean up database resources
    cursor.close()
    conn.close()

    # Return the DeviceId if found, converted to string; otherwise return None
    if result:
        return str(result[0])

    return None

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


def get_vehicle_by_device(device_id):

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT "VehicleId"
        FROM "DeviceAssignments"
        WHERE "DeviceId" = %s
        AND "Status" = 1
        LIMIT 1
        """,
        (device_id,)
    )

    result = cursor.fetchone()

    cursor.close()
    conn.close()

    if result:
        return str(result[0])

    return None



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