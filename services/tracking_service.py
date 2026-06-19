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