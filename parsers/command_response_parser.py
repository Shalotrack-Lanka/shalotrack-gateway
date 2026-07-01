import re
from datetime import datetime


def parse_command_response(packet):
    """
    Parse Protocol 0x21 Command Response Packet.
    """

    if packet[:2] == b"\x79\x79":
        payload = packet[5:-6]
    else:
        payload = packet[4:-6]

    text = payload.decode(
        "ascii",
        errors="ignore"
    ).strip()

    result = {
        "command": "UNKNOWN",
        "success": True,
        "raw_text": text,
        "data": {}
    }

    # ------------------------------------
    # WHERE
    # ------------------------------------

    if "Current position!" in text:

        result["command"] = "WHERE"

        latitude = None
        longitude = None
        course = None
        speed = None
        timestamp = None

        lat_match = re.search(r"Lat:([NS])([\d.]+)", text)
        lon_match = re.search(r"Lon:([EW])([\d.]+)", text)
        course_match = re.search(r"Course:(\d+)", text)
        speed_match = re.search(r"Speed:(\d+)", text)
        time_match = re.search(r"DateTime:(.+)", text)

        if lat_match:
            latitude = float(lat_match.group(2))
            if lat_match.group(1) == "S":
                latitude *= -1

        if lon_match:
            longitude = float(lon_match.group(2))
            if lon_match.group(1) == "W":
                longitude *= -1

        if course_match:
            course = int(course_match.group(1))

        if speed_match:
            speed = int(speed_match.group(1))

        if time_match:
            try:
                timestamp = datetime.strptime(
                    time_match.group(1).strip(),
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                pass

        result["data"] = {
            "latitude": latitude,
            "longitude": longitude,
            "course": course,
            "speed": speed,
            "timestamp": timestamp
        }

        return result

    # ------------------------------------
    # STATUS
    # ------------------------------------

    if "[STATUS]" in text:

        result["command"] = "STATUS"

        data = {}

        for line in text.splitlines():

            if ":" not in line:
                continue

            key, value = line.split(":", 1)

            data[key.strip()] = value.strip()

        result["data"] = data

        return result

    # ------------------------------------
    # VERSION
    # ------------------------------------

    if "[VERSION]" in text:

        result["command"] = "VERSION"

        firmware = text.replace(
            "[VERSION]",
            ""
        ).strip()

        result["data"] = {
            "firmware": firmware
        }

        return result

    # ------------------------------------
    # PARAM
    # ------------------------------------

    if "[PARAM]" in text:

        result["command"] = "PARAM"

        data = {}

        for line in text.splitlines():

            if ":" not in line:
                continue

            key, value = line.split(":", 1)

            data[key.strip()] = value.strip()

        result["data"] = data

        return result

    return result