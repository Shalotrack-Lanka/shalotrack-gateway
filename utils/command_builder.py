from parsers.v5_parser import crc16_x25

SERVER_FLAG = b"\x00\x00\x00\x01"
LANGUAGE = b"\x00\x02"

_serial = 1


def next_serial():
    global _serial
    value = _serial
    _serial += 1
    return value


def build_command(command: str):

    serial = next_serial().to_bytes(2, "big")

    command_bytes = command.encode("ascii")

    command_length = len(SERVER_FLAG) + len(command_bytes)

    body = (
        bytes([0x80])
        + bytes([command_length])
        + SERVER_FLAG
        + command_bytes
        + LANGUAGE
        + serial
    )

    packet_length = len(body) + 2

    crc = crc16_x25(
        bytes([packet_length]) + body
    )

    packet = (
        b"\x78\x78"
        + bytes([packet_length])
        + body
        + crc.to_bytes(2, "big")
        + b"\x0D\x0A"
    )

    return packet


def build_where():
    return build_command("WHERE#")


def build_status():
    return build_command("STATUS#")


def build_version():
    return build_command("VERSION#")


def build_imei():
    return build_command("IMEI#")


def build_params():
    return build_command("PARAM#")


def build_reset():
    return build_command("RESET#")


def build_relay_on():
    return build_command("RELAY,1#")


def build_relay_off():
    return build_command("RELAY,0#")