from datetime import datetime


def crc16_x25(data):

    #Calculate CRC-16/X-25 for the given byte sequence.
    #This algorithm is used by many GPS/GSM tracker protocols. The CRC is
    #initialized to 0xFFFF, each input byte is XORed into the CRC, then the
    #algorithm processes 8 bit shifts with the polynomial 0x8408.
    
    crc = 0xFFFF

    for b in data:
        crc ^= b

        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1

    crc ^= 0xFFFF
    return crc & 0xFFFF


def parse_login_packet(raw):
    # Parse a login packet and extract IMEI and packet serial number.

    imei = ""

    # IMEI is encoded in the packet payload bytes 4 through 11.
    for b in raw[4:12]:
        imei += f"{b:02x}"

    # The packet serial number is stored near the end of the packet.
    serial = raw[-6:-4].hex()

    return {
        "imei": imei.lstrip("0"),
        "serial": serial,
    }


def parse_location_packet(raw):
    # Parse a location packet and return structured GPS data.

    # Timestamp fields are stored as separate bytes starting at offset 4.
    year = raw[4] + 2000
    month = raw[5]
    day = raw[6]

    hour = raw[7]
    minute = raw[8]
    second = raw[9]

    timestamp = (
        f"{year}-{month:02}-{day:02} "
        f"{hour:02}:{minute:02}:{second:02}"
    )

    # Latitude and longitude are 4-byte big-endian integers.
    # The values are scaled by 1,800,000 to convert to degrees.
    lat_raw = int.from_bytes(raw[11:15], "big")
    lon_raw = int.from_bytes(raw[15:19], "big")

    latitude = lat_raw / 1800000
    longitude = lon_raw / 1800000

    # Speed is a single byte value in km/h.
    speed = raw[19]

    return {
        "latitude": latitude,
        "longitude": longitude,
        "speed": speed,
        "timestamp": timestamp,
    }


def build_ack(packet):
    #Build an acknowledgment packet for the received tracker message.

    # Protocol number identifies the message type at byte 3.
    protocol_number = packet[3]

    # The serial number is echoed back from the packet trailer.
    serial = packet[-6:-4]

    ack_body = (
        bytes([0x05])  # Ack packet type byte
        + bytes([protocol_number])
        + serial
    )

    crc = crc16_x25(ack_body)

    response = (
        b"\x78\x78"  # Packet start marker
        + ack_body
        + crc.to_bytes(2, "big")
        + b"\x0D\x0A"  # Packet terminator
    )

    return response

def parse_status_packet(packet):

    terminal_info = packet[4]
    voltage = packet[5]
    gsm_signal = packet[6]

    return {
        "battery_level": voltage,
        "gsm_signal": gsm_signal,
        "ignition_status": bool(terminal_info & 0x02), ## representing 2bytes it takes
        "power_cut": bool(terminal_info & 0x80),
        "gps_tracking": bool(terminal_info & 0x40),
        "charging": bool(terminal_info & 0x04),
        "activated": bool(terminal_info & 0x01)
    }