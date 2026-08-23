from services.device_registry import get_socket
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


def send_command(imei, command):
    sock = get_socket(imei)
    if not sock:
        raise Exception("Device Offline")
    log(f"📤 Sending command to {imei} — HEX: {command.hex().upper()}")
    sock.sendall(command)
    return True


# Query commands
def send_where(imei):       return send_command(imei, build_where())
def send_status(imei):      return send_command(imei, build_status())
def send_version(imei):     return send_command(imei, build_version())
def send_imei(imei):        return send_command(imei, build_imei())
def send_params(imei):      return send_command(imei, build_params())
def send_gprsset(imei):     return send_command(imei, build_gprsset())
def send_url(imei):         return send_command(imei, build_url())
def send_position(imei):    return send_command(imei, build_position())
def send_fence_query(imei): return send_command(imei, build_fence_query())
def send_moving_query(imei):return send_command(imei, build_moving_query())
def send_speed_query(imei): return send_command(imei, build_speed_query())
def send_sos_query(imei):   return send_command(imei, build_sos_query())
def send_timer_query(imei): return send_command(imei, build_timer_query())
def send_apn_query(imei):   return send_command(imei, build_apn_query())
def send_server_query(imei):return send_command(imei, build_server_query())

# Control commands
def send_reset(imei):       return send_command(imei, build_reset())
def send_relay_on(imei):    return send_command(imei, build_relay_on())
def send_relay_off(imei):   return send_command(imei, build_relay_off())

# Config commands — these take parameters
def send_timer(imei, t1: int, t2: int):
    return send_command(imei, build_timer(t1, t2))

def send_distance(imei, meters: int):
    return send_command(imei, build_distance(meters))

def send_speed_alarm(imei, enabled: bool, interval: int = 20, limit_kmh: int = 100, sms: bool = True):
    return send_command(imei, build_speed_alarm(enabled, interval, limit_kmh, sms))

def send_moving_alarm(imei, enabled: bool, radius_m: int = 300, sms: bool = True):
    return send_command(imei, build_moving_alarm(enabled, radius_m, sms))

def send_fence_circle(imei, enabled: bool, lat: float, lon: float, radius_100m: int, trigger: str = "", sms: bool = True):
    return send_command(imei, build_fence_circle(enabled, lat, lon, radius_100m, trigger, sms))

def send_sos_add(imei, phone1: str, phone2: str = "", phone3: str = ""):
    return send_command(imei, build_sos_add(phone1, phone2, phone3))

def send_sos_delete(imei):
    return send_command(imei, build_sos_delete())

def send_apn(imei, apn_name: str, user: str = "", pwd: str = ""):
    return send_command(imei, build_apn(apn_name, user, pwd))

def send_server(imei, domain_or_ip: str, port: int, use_domain: bool = True, udp: bool = False):
    return send_command(imei, build_server(domain_or_ip, port, use_domain, udp))

def send_batalm(imei, enabled: bool, sms: bool = True):
    return send_command(imei, build_batalm(enabled, sms))

def send_poweralm(imei, enabled: bool, sms: bool = True):
    return send_command(imei, build_poweralm(enabled, sms))


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