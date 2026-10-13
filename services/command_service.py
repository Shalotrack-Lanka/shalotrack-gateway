from services.device_registry import get_socket, get_device
from utils.command_builder import (
    build_where, build_status, build_version, build_imei, build_params,
    build_gprsset, build_url, build_position, build_fence_query,
    build_moving_query, build_speed_query, build_sos_query,
    build_timer_query, build_apn_query, build_server_query,
    build_reset, build_relay_on, build_relay_off,
    build_timer, build_distance, build_speed_alarm, build_moving_alarm,
    build_fence_circle, build_sos_add, build_sos_delete,
    build_apn, build_server, build_batalm, build_poweralm,
)
from utils.logger import log


class UnsupportedCommandError(Exception):
    """The connected device speaks a protocol the command builders do not cover."""


async def send_command(imei, command):
    writer = get_socket(imei)
    if not writer:
        raise Exception("Device Offline")

    # Every builder in utils/command_builder.py produces a GT06 binary frame.
    # Writing that to a GPS103 (TK103A/B) socket would send the unit garbage
    # (or worse, something it misreads), so refuse until a per-protocol
    # command builder exists.
    entry = get_device(imei)
    protocol = entry.get("protocol", "GT06") if entry else "GT06"
    if protocol != "GT06":
        raise UnsupportedCommandError(
            f"Commands are not supported yet for {protocol} devices"
        )

    log(f"📤 Sending command to {imei} — HEX: {command.hex().upper()}")
    writer.write(command)
    await writer.drain()
    return True


# Query commands
async def send_where(imei):        return await send_command(imei, build_where())
async def send_status(imei):       return await send_command(imei, build_status())
async def send_version(imei):      return await send_command(imei, build_version())
async def send_imei(imei):         return await send_command(imei, build_imei())
async def send_params(imei):       return await send_command(imei, build_params())
async def send_gprsset(imei):      return await send_command(imei, build_gprsset())
async def send_url(imei):          return await send_command(imei, build_url())
async def send_position(imei):     return await send_command(imei, build_position())
async def send_fence_query(imei):  return await send_command(imei, build_fence_query())
async def send_moving_query(imei): return await send_command(imei, build_moving_query())
async def send_speed_query(imei):  return await send_command(imei, build_speed_query())
async def send_sos_query(imei):    return await send_command(imei, build_sos_query())
async def send_timer_query(imei):  return await send_command(imei, build_timer_query())
async def send_apn_query(imei):    return await send_command(imei, build_apn_query())
async def send_server_query(imei): return await send_command(imei, build_server_query())

# Control commands
async def send_reset(imei):        return await send_command(imei, build_reset())
async def send_relay_on(imei):     return await send_command(imei, build_relay_on())
async def send_relay_off(imei):    return await send_command(imei, build_relay_off())

# Config commands — these take parameters
async def send_timer(imei, t1: int, t2: int):
    return await send_command(imei, build_timer(t1, t2))

async def send_distance(imei, meters: int):
    return await send_command(imei, build_distance(meters))

async def send_speed_alarm(imei, enabled: bool, interval: int = 20, limit_kmh: int = 100, sms: bool = True):
    return await send_command(imei, build_speed_alarm(enabled, interval, limit_kmh, sms))

async def send_moving_alarm(imei, enabled: bool, radius_m: int = 300, sms: bool = True):
    return await send_command(imei, build_moving_alarm(enabled, radius_m, sms))

async def send_fence_circle(imei, enabled: bool, lat: float, lon: float, radius_100m: int, trigger: str = "", sms: bool = True):
    return await send_command(imei, build_fence_circle(enabled, lat, lon, radius_100m, trigger, sms))

async def send_sos_add(imei, phone1: str, phone2: str = "", phone3: str = ""):
    return await send_command(imei, build_sos_add(phone1, phone2, phone3))

async def send_sos_delete(imei):
    return await send_command(imei, build_sos_delete())

async def send_apn(imei, apn_name: str, user: str = "", pwd: str = ""):
    return await send_command(imei, build_apn(apn_name, user, pwd))

async def send_server(imei, domain_or_ip: str, port: int, use_domain: bool = True, udp: bool = False):
    return await send_command(imei, build_server(domain_or_ip, port, use_domain, udp))

async def send_batalm(imei, enabled: bool, sms: bool = True):
    return await send_command(imei, build_batalm(enabled, sms))

async def send_poweralm(imei, enabled: bool, sms: bool = True):
    return await send_command(imei, build_poweralm(enabled, sms))


# Every future command (STATUS, VERSION, PARAM, TIME, APN, etc.) will simply be:
#
# command = build_xxx()
#
# await send_command(
#     imei,
#     command
# )
#
# No duplicated code.