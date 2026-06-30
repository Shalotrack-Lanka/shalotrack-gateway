from services.device_registry import (
    get_device,
    get_socket,
    get_all_devices
)

from utils.command_builder import (
    build_where,
    build_reset,
    build_relay_on,
    build_relay_off,
)


def send_command(imei, command):

    sock = get_socket(imei)

    if not sock:
        raise Exception("Device Offline")
    
    print("===================================")
    print("Sending bytes:")
    print(command)
    print(command.hex())
    print("===================================")

    sock.sendall(command)

    print(f"📤 Command sent to {imei}: {command.decode()}")
    return True


def send_where(imei):

    command = build_where()

    send_command(
        imei,
        command
    )
    return command


def reboot(imei):

    command = build_reset()

    send_command(
        imei,
        command
    )
    return command


def cut_engine(imei):

    command = build_relay_off()

    send_command(
        imei,
        command
    )
    return command


def resume_engine(imei):

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