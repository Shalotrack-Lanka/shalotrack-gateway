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
    crc = crc16_x25(bytes([packet_length]) + body)
    packet = (
        b"\x78\x78"
        + bytes([packet_length])
        + body
        + crc.to_bytes(2, "big")
        + b"\x0D\x0A"
    )
    return packet


# ================================================================
# QUERY commands
# ================================================================

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

def build_gprsset():
    """Query current GPRS/APN/server settings."""
    return build_command("GPRSSET#")

def build_url():
    """Query the Google Maps URL for current position."""
    return build_command("URL#")

def build_position():
    """Request current position as a Google Maps link."""
    return build_command("POSITION#")

def build_fence_query():
    """Query current geofence settings."""
    return build_command("FENCE#")

def build_moving_query():
    """Query current moving alarm settings."""
    return build_command("MOVING#")

def build_speed_query():
    """Query current overspeed alarm settings."""
    return build_command("SPEED#")

def build_sos_query():
    """Query SOS phone numbers."""
    return build_command("SOS#")

def build_timer_query():
    """Query current GPS upload interval settings."""
    return build_command("TIMER#")

def build_apn_query():
    """Query current APN setting."""
    return build_command("APN#")

def build_server_query():
    """Query current server IP/domain settings."""
    return build_command("SERVER#")


# ================================================================
# CONTROL commands
# ================================================================

def build_reset():
    """Reboot the device (reboots in ~20s after receiving)."""
    return build_command("RESET#")

def build_relay_on():
    """Restore engine relay (re-enable ignition). RELAY,0 = connected."""
    return build_command("RELAY,0#")

def build_relay_off():
    """Cut engine relay (immobilize vehicle). RELAY,1 = cut off."""
    return build_command("RELAY,1#")


# ================================================================
# CONFIGURATION commands
# ================================================================

def build_timer(t1: int, t2: int):
    """
    Set GPS upload interval.
    t1 = interval when ACC ON  (5-18000 seconds, 0 = no upload), default 10
    t2 = interval when ACC OFF (5-18000 seconds), default 10
    Example: build_timer(20, 300) → TIMER,20,300#
    """
    return build_command(f"TIMER,{t1},{t2}#")

def build_distance(meters: int):
    """
    Set distance-based upload interval.
    meters = 50-10000, default 300
    Example: build_distance(200) → DISTANCE,200#
    """
    return build_command(f"DISTANCE,{meters}#")

def build_speed_alarm(enabled: bool, interval: int = 20, limit_kmh: int = 100, sms: bool = True):
    """
    Configure overspeed alarm.
    enabled    = True/False
    interval   = 5-600 seconds between alerts, default 20
    limit_kmh  = 1-255 km/h speed limit, default 100
    sms        = True = SMS+GPRS, False = GPRS only
    """
    state = "ON" if enabled else "OFF"
    mode = 1 if sms else 0
    return build_command(f"SPEED,{state},{interval},{limit_kmh},{mode}#")

def build_moving_alarm(enabled: bool, radius_m: int = 300, sms: bool = True):
    """
    Configure moving alarm (vehicle moved while parked).
    enabled   = True/False
    radius_m  = 100-1000 meters, default 300
    sms       = True = SMS+GPRS, False = GPRS only
    """
    state = "ON" if enabled else "OFF"
    mode = 1 if sms else 0
    return build_command(f"MOVING,{state},{radius_m},{mode}#")

def build_fence_circle(enabled: bool, lat: float, lon: float, radius_100m: int, trigger: str = "", sms: bool = True):
    """
    Set circular geofence.
    enabled      = True/False
    lat, lon     = center coordinates
    radius_100m  = 1-9999 (units of 100m, so 5 = 500m radius)
    trigger      = "IN", "OUT", or "" (both)
    sms          = True = SMS+GPRS, False = GPRS only
    """
    state = "ON" if enabled else "OFF"
    mode = 1 if sms else 0
    return build_command(f"FENCE,{state},0,{lat},{lon},{radius_100m},{trigger},{mode}#")

def build_sos_add(phone1: str, phone2: str = "", phone3: str = ""):
    """
    Set SOS phone numbers (up to 3).
    Example: build_sos_add("+94771234567")
    """
    numbers = ",".join(filter(None, [phone1, phone2, phone3]))
    return build_command(f"SOS,A,{numbers}#")

def build_sos_delete():
    """Delete all SOS phone numbers."""
    return build_command("SOS,D#")

def build_apn(apn_name: str, user: str = "", pwd: str = ""):
    """
    Set APN.
    Example: build_apn("dialogbb") or build_apn("internet", "user", "pass")
    """
    if user or pwd:
        return build_command(f"APN,{apn_name},{user},{pwd}#")
    return build_command(f"APN,{apn_name}#")

def build_server(domain_or_ip: str, port: int, use_domain: bool = True, udp: bool = False):
    """
    Set server address.
    use_domain = True  → mode=1 (domain name)
    use_domain = False → mode=0 (IP address)
    udp        = True  → protocol=1 (UDP), False → protocol=0 (TCP)
    Example: build_server("api.shalotrack.com", 8000)
    """
    mode = 1 if use_domain else 0
    protocol = 1 if udp else 0
    return build_command(f"SERVER,{mode},{domain_or_ip},{port},{protocol}#")

def build_batalm(enabled: bool, sms: bool = True):
    """
    Configure low battery alarm.
    enabled = True/False
    sms     = True = SMS+GPRS, False = GPRS only
    """
    state = "ON" if enabled else "OFF"
    mode = 1 if sms else 0
    return build_command(f"BATALM,{state},{mode}#")

def build_poweralm(enabled: bool, sms: bool = True):
    """
    Configure power cut-off alarm.
    enabled = True/False
    sms     = True = SMS+GPRS, False = GPRS only
    """
    state = "ON" if enabled else "OFF"
    mode = 1 if sms else 0
    return build_command(f"POWERALM,{state},{mode}#")