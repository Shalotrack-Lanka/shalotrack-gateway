from services.device_registry import (
    get_socket,
    get_all_devices,
    get_device
)

from utils.command_builder import (
    build_where,
    build_status,
    build_version,
    build_params,
    build_imei,
    build_reset,
    build_relay_on,
    build_relay_off,
)


def send_command(imei, command):

    sock = get_socket(imei)

    if not sock:
        raise Exception("Device Offline")

    print("\n" + "=" * 60)
    print("📤 Sending Command")
    print("=" * 60)
    print(f"IMEI   : {imei}")
    print(f"Length : {len(command)} bytes")
    print(f"HEX    : {command.hex().upper()}")
    print("=" * 60 + "\n")

    sock.sendall(command)

    return True


def send_where(imei):

    command = build_where()

    send_command(
        imei,
        command
    )
    return command

def send_status(imei):

    command = build_status()

    send_command(
        imei,
        command
    )
    return command


def send_version(imei):

    command = build_version()

    send_command(
        imei,
        command
    )
    return command


def send_params(imei):

    command = build_params()

    send_command(
        imei,
        command
    )
    return command


def send_imei(imei):

    command = build_imei()

    send_command(
        imei,
        command
    )
    return command


def send_reset(imei):

    command = build_reset()

    send_command(
        imei,
        command
    )
    return command


def send_relay_off(imei):

    command = build_relay_off()

    send_command(
        imei,
        command
    )
    return command


def send_relay_on(imei):

    command = build_relay_on()

    send_command(
        imei,
        command
    )
    return command


# Every future command (STATUS, VERSION, PARAM, TIME, APN, etc.) will simply be:
#
# command = build_xxx()
#
# send_command(
#     imei,
#     command
# )
#
# No duplicated code.