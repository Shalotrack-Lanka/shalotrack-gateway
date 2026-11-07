"""
Command path for GPS103 (TK103A/B) devices.

command_api.py chooses this path when the connected device's protocol is
GPS103; GT06 devices keep using services/command_service.py unchanged. The
portal names are mapped to what a TK103 can actually do over GPRS. Anything
else raises UnsupportedCommandError with a specific message (the API turns it
into HTTP 501 and the portal shows it to the operator), instead of the old
blanket "not supported" for every command.
"""

from services.command_service import UnsupportedCommandError
from services.device_registry import get_device, get_socket
from utils.gps103_command_builder import build_engine_resume, build_single_position
from utils.logger import log

GPS103 = "GPS103"

# portal command name -> builder(imei) -> bytes
SUPPORTED = {
    "where":    build_single_position,
    "position": build_single_position,
    "url":      build_single_position,
    "relay_on": build_engine_resume,
}


def supported_commands() -> list[str]:
    return sorted(SUPPORTED)


async def send_gps103_command(imei: str, command: str) -> bool:
    writer = get_socket(imei)
    if not writer:
        raise Exception("Device Offline")

    entry = get_device(imei)
    if not entry or entry.get("protocol") != GPS103:
        # Defence in depth: never write GPS103 text to another family's socket.
        raise UnsupportedCommandError("Not a GPS103 device")

    builder = SUPPORTED.get(command)
    if builder is None:
        if command == "relay_off":
            raise UnsupportedCommandError(
                "Engine cut (relay_off) is disabled for GPS103 devices for safety"
            )
        raise UnsupportedCommandError(
            f"'{command}' is not available over the data connection for GPS103 (TK103) "
            f"devices; it has to be sent to the device by SMS"
        )

    payload = builder(imei)
    log(f"📤 Sending GPS103 command '{command}' to {imei}: {payload.decode('ascii')}")
    writer.write(payload)
    await writer.drain()
    return True
