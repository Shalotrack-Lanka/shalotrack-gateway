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

    # Improvement 1: return datetime object instead of a formatted string so
    # callers (save_tracking, update_current_location) receive a proper type.
    timestamp = datetime(year, month, day, hour, minute, second)

    # Latitude and longitude are 4-byte big-endian integers.
    # The values are scaled by 1,800,000 to convert to degrees.
    lat_raw = int.from_bytes(raw[11:15], "big")
    lon_raw = int.from_bytes(raw[15:19], "big")

    latitude = lat_raw / 1800000
    longitude = lon_raw / 1800000

    # Speed is a single byte value in km/h.
    speed = raw[19]

    # GPS info byte: upper nibble = data length, lower nibble = satellite count.
    # Exposed here to keep location and alarm parsers symmetrical.
    gps_length = raw[10] >> 4
    satellites = raw[10] & 0x0F

    # Improvement 2: decode the course/status word so the parser works correctly
    # in all hemispheres, not just northern/eastern.
    #   bits 0-9  : heading in degrees (0-359)
    #   bit 10    : latitude is south if set
    #   bit 11    : longitude is west if set
    #   bit 12    : GPS fix acquired if set
    course_status = int.from_bytes(raw[20:22], "big")

    heading = course_status & 0x03FF
    # FIX: per the protocol spec's own worked example ("BYTE_1 Bit2 = 1 (North
    # Latitude)"), this bit set means NORTH, not south -- the original code had
    # this backwards. Longitude's bit was already correct (spec: "BYTE_1 Bit3 = 0
    # (East Longitude)", so bit set correctly means West).
    is_north = bool(course_status & 0x0400)
    is_west = bool(course_status & 0x0800)
    gps_fixed = bool(course_status & 0x1000)

    if not is_north:
        latitude = -latitude
    if is_west:
        longitude = -longitude

    return {
        "latitude": latitude,
        "longitude": longitude,
        "speed": speed,
        "timestamp": timestamp,
        "heading": heading,
        "gps_fixed": gps_fixed,
        "gps_length": gps_length,
        "satellites": satellites,
    }


def build_ack(packet):
    #Build an acknowledgment packet for the received tracker message.

    # Protocol byte position differs by frame type:
    #   7878 short frame: protocol at byte 3
    #   7979 long frame:  protocol at byte 4 (bytes 2-3 are the 2-byte length field)
    if packet[0:2] == b"\x79\x79":
        protocol_number = packet[4]
    else:
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


def parse_terminal_status(packet):
    # Shared decoder for status (0x13) and heartbeat (0x23) packets.
    # Both carry identical terminal info, voltage, and GSM signal fields.

    terminal_info = packet[4]
    voltage = packet[5]
    gsm_signal = packet[6]

    return {
        "battery_level": voltage,
        "gsm_signal": gsm_signal,
        "ignition_status": bool(terminal_info & 0x02),  # bit 1 = ignition
        "power_cut": bool(terminal_info & 0x80),
        "gps_tracking": bool(terminal_info & 0x40),
        "charging": bool(terminal_info & 0x04),
        "activated": bool(terminal_info & 0x01)
    }


def parse_status_packet(packet):
    return parse_terminal_status(packet)


def parse_heartbeat_packet(packet):
    return parse_terminal_status(packet)


def parse_alarm_packet(packet):
    """
    Protocol 0x26 Alarm Packet
    GT06 / V5 Alarm Information
    """

    # --------------------------
    # Date & Time
    # --------------------------

    year = packet[4] + 2000
    month = packet[5]
    day = packet[6]

    hour = packet[7]
    minute = packet[8]
    second = packet[9]

    timestamp = datetime(year, month, day, hour, minute, second)

    # --------------------------
    # GPS
    # --------------------------

    gps_length = packet[10] >> 4    # upper nibble: number of GPS data bytes
    satellites = packet[10] & 0x0F  # lower nibble: satellites in view

    latitude_raw = int.from_bytes(packet[11:15], "big")
    longitude_raw = int.from_bytes(packet[15:19], "big")

    latitude = latitude_raw / 1800000
    longitude = longitude_raw / 1800000

    speed = packet[19]

    # Improvement 2 (applied to alarm packet): same hemisphere correction as
    # parse_location_packet so alarm coordinates are correct worldwide.
    course_status = int.from_bytes(packet[20:22], "big")

    heading = course_status & 0x03FF
    # FIX: per the protocol spec's own worked example ("BYTE_1 Bit2 = 1 (North
    # Latitude)"), this bit set means NORTH, not south -- the original code had
    # this backwards. Longitude's bit was already correct (spec: "BYTE_1 Bit3 = 0
    # (East Longitude)", so bit set correctly means West).
    is_north = bool(course_status & 0x0400)
    is_west = bool(course_status & 0x0800)
    gps_fixed = bool(course_status & 0x1000)

    if not is_north:
        latitude = -latitude
    if is_west:
        longitude = -longitude

    # --------------------------
    # LBS
    # --------------------------

    mcc = int.from_bytes(packet[22:24], "big")
    mnc = packet[24]
    lac = int.from_bytes(packet[25:27], "big")
    cell_id = int.from_bytes(packet[27:30], "big")

    # --------------------------
    # Terminal Information
    # --------------------------

    terminal_info = packet[30]
    battery_level = packet[31]
    gsm_signal = packet[32]

    alarm_language = packet[33]
    alarm_type = alarm_language >> 4
    language = "English" if alarm_language & 0x01 else "Chinese"

    return {
        "timestamp": timestamp,
        "latitude": latitude,
        "longitude": longitude,
        "speed": speed,
        "satellites": satellites,
        "gps_length": gps_length,
        "heading": heading,
        "gps_fixed": gps_fixed,
        "course_status": course_status,
        "mcc": mcc,
        "mnc": mnc,
        "lac": lac,
        "cell_id": cell_id,
        "terminal_info": terminal_info,
        "battery_level": battery_level,
        "gsm_signal": gsm_signal,
        "alarm_type": alarm_type,
        "language": language,
        "ignition_status": bool(terminal_info & 0x02),
        "power_cut": bool(terminal_info & 0x80),
        "gps_tracking": bool(terminal_info & 0x40),
        "charging": bool(terminal_info & 0x04),
        "activated": bool(terminal_info & 0x01),
    }


def parse_information_packet(packet):
    # ---------------------------------------
    # Skip protocol byte (0x94)
    # Skip CRC + Serial + End Marker
    # ---------------------------------------

    if packet[:2] == b"\x79\x79":
        payload = packet[5:-6]
    else:
        payload = packet[4:-6]

    result = {
        "is_ascii": False,
        "raw_hex": payload.hex(),
        "values": {}
    }

    try:

        text = payload.decode("ascii").strip("\x00")
        # Require printable characters only
        if all(32 <= ord(c) <= 126 for c in text):

            result["is_ascii"] = True
            result["text"] = text

            values = {}

            for item in text.split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    values[key.strip()] = value.strip()
            result["values"] = values
    except UnicodeDecodeError:
        pass
    return result

def get_alarm_name(alarm_type: int) -> str:
    """Return a human-readable name for a Protocol 0x26 alarm type code."""

    alarms = {
        0:  "NORMAL",
        1:  "SOS",
        2:  "POWER_CUT",
        3:  "SHOCK",
        4:  "FENCE_IN",
        5:  "FENCE_OUT",
        6:  "OVERSPEED",
        7:  "LOW_BATTERY",
        8:  "VIBRATION",
        9:  "MOVE",
        10: "ACC_ON",
        11: "ACC_OFF",
        12: "TOW",
        13: "GPS_ANTENNA",
        14: "EXTERNAL_POWER",
    }

    return alarms.get(alarm_type, f"UNKNOWN (0x{alarm_type:02X})")