from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    parse_status_packet,
    parse_heartbeat_packet,
    parse_alarm_packet,
    get_alarm_name,
    build_ack
)

from parsers.command_response_parser import (
    parse_command_response
)

from services.command_response_service import (
    handle_command_response
)

from services.device_registry import (
    register_device,
    get_device,
    get_imei_by_socket
)

from services.tracking_service import (
    save_tracking,
    update_current_location,
    update_device_status,
    update_heartbeat
)

from repositories.device_repository import DeviceRepository
from repositories.raw_packet_repository import RawPacketRepository

from services.event_service import create_event

from constants.event_types import EventType
from constants.severity import Severity
from utils.logger import log


def _get_device(imei):
    if not imei:
        log("❌ Unknown Device")
        return None

    device = get_device(imei)

    if not device:
        log(f"❌ Device not registered: {imei}")
    return device


def _save_raw(device_id, protocol, hex_data):
    """Persist a raw packet to the repository."""
    RawPacketRepository.save(
        device_id=device_id,
        protocol_number=protocol,
        raw_hex=hex_data,
        parsed=True
    )


def _log_unknown(protocol, raw, hex_data):
    """Log enough context about unrecognised packets for later analysis."""
    log("\n========== UNKNOWN PACKET ==========")
    log(f"Protocol : {protocol}")
    log(f"Length   : {len(raw)}")
    log(f"Raw HEX  : {hex_data}")
    log("====================================")


def process_packet(data, conn, addr):

    raw = data

    if raw[0:2] == b"\x78\x78":
        protocol = f"{raw[3]:02x}"

    elif raw[0:2] == b"\x79\x79":
        protocol = f"{raw[4]:02x}"

    else:
        log("❌ Invalid packet")
        return

    log(f"Protocol: {protocol}")

    hex_data = raw.hex()

    current_imei = None
    connection_imei = get_imei_by_socket(conn)

    if protocol == "01":

        log("📡 Login Packet")

        try:

            login = parse_login_packet(raw)

            log(f"📡 Login packet received")

            device_id = DeviceRepository.get_device_by_imei(login["imei"])

            log(f"🔍 Device lookup: {login['imei']}")

            if not device_id:

                log(f"🆕 New Device Detected: {login['imei']}")
                
                device_id = DeviceRepository.register_device(
                    login["imei"]
                )

                log(f"📱 Device Auto registered: {login['imei']}")

            else:
                log(f"✅ Existing Device")

                register_device(
                    login["imei"],
                    addr[0],
                    device_id,
                    conn
                )

                log(f"📱 Device registered: {login['imei']}")

                _save_raw(
                    device_id, 
                    protocol, 
                    hex_data
                )

                log(f"📱 IMEI: {login['imei']}")
                log(f"🆔 Device ID: {device_id}")
                log(f"🔢 Serial: {login['serial']}")

                vehicle_id = DeviceRepository.get_vehicle_by_device(
                    device_id
                )

                create_event(
                    device_id=device_id,
                    vehicle_id=vehicle_id,
                    event_type=EventType.DEVICE_ONLINE.value,
                    severity=Severity.LOW,
                    description="Device connected to the gateway"
                )

                current_imei = login["imei"]

        except Exception as ex:

            log(f"❌ LOGIN ERROR: {ex}")

    elif protocol in ["12", "22"]:

        log("📍 LOCATION RECEIVED")

        try:

            location = parse_location_packet(raw)

            log(f"Latitude: {location['latitude']}")
            log(f"Longitude: {location['longitude']}")
            log(f"Speed: {location['speed']}")
            log(f"Time: {location['timestamp']}")

            log(
                f"🗺 https://maps.google.com/?q={location['latitude']},{location['longitude']}"
            )

            device = _get_device(connection_imei)

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                save_tracking(
                    device_id=device["device_id"],
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                    speed=location["speed"],
                    event_time=location["timestamp"]
                )

                log("✅ GPS Tracking Saved")

                vehicle_id = DeviceRepository.get_vehicle_by_device(device["device_id"])

                if vehicle_id:

                    update_current_location(
                        device_id=device["device_id"],
                        vehicle_id=vehicle_id,
                        latitude=location["latitude"],
                        longitude=location["longitude"],
                        speed=location["speed"],
                        event_time=location["timestamp"]
                    )

                log("✅ Current Location Updated")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ LOCATION ERROR: {ex}")

    elif protocol == "13":

        log("⚡ Status Packet")

        try:
            device = _get_device(connection_imei)

            if device:
                status = parse_status_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                update_device_status(
                    device_id=device["device_id"],
                    battery_level=status["battery_level"],
                    gps_signal=status["gsm_signal"],
                    ignition_status=status["ignition_status"],
                    movement_status=status["ignition_status"],
                    power_status=1 if not status["power_cut"] else 2
                )

                log(f"Battery: {status['battery_level']}")
                log(f"Signal: {status['gsm_signal']}")
                log(f"Ignition: {status['ignition_status']}")
                log(f"Power Cut: {status['power_cut']}")
                log(f"Charging: {status['charging']}")

                current_imei = connection_imei
        except Exception as ex:
            log(f"❌ STATUS ERROR: {ex}")

    elif protocol == "23":

        log("💓 Heartbeat Packet")

        try:

            device = _get_device(connection_imei)

            if device:

                heartbeat = parse_heartbeat_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                update_device_status(
                    device_id=device["device_id"],
                    battery_level=heartbeat["battery_level"],
                    gps_signal=heartbeat["gsm_signal"],
                    ignition_status=heartbeat["ignition_status"],
                    movement_status=heartbeat["ignition_status"],
                    power_status=1 if not heartbeat["power_cut"] else 2
                )

                update_heartbeat(
                    device["device_id"]
                )

                log("💓 Heartbeat Updated")
                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ HEARTBEAT ERROR: {ex}")

    elif protocol == "26":

        log("🚨 Alarm Packet")

        try:

            device = _get_device(connection_imei)

            if device:

                alarm = parse_alarm_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                log("🚨 Alarm Packet Saved")
                log(
                    f"Alarm : {get_alarm_name(alarm['alarm_type'])}"
                )
                log(f"Latitude : {alarm['latitude']}")
                log(f"Longitude : {alarm['longitude']}")
                log(f"Speed : {alarm['speed']}")
                log(f"Timestamp : {alarm['timestamp']}")
                log(f"Battery : {alarm['battery_level']}")
                log(f"Signal : {alarm['gsm_signal']}")
                log(f"Charging : {alarm['charging']}")
                log(f"Power Cut : {alarm['power_cut']}")
                log(f"Ignition : {alarm['ignition_status']}")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ ALARM ERROR: {ex}")

    elif protocol == "94":

        log("📡 Information Packet")

        try:

            device = _get_device(connection_imei)

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                log("✅ Information Packet Saved")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ INFO PACKET ERROR: {ex}")

    elif protocol == "8a":

        log("📨 Command Response Packet")

        try:

            device = _get_device(connection_imei)

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                log("✅ Command Response Saved")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ COMMAND RESPONSE ERROR: {ex}")

    elif protocol == "21":

        log("📨 Command Text Response")

        try:

            device = _get_device(connection_imei)

            if device:

                _save_raw(
                    device["device_id"],
                    protocol,
                    hex_data
                )

                response = parse_command_response(raw)

                handle_command_response(
                    device["device_id"],
                    connection_imei,
                    response
                )

                log("📨 Command Text Response")
                log(f"Command : {response['command']}")
                log(f"Raw Text : {response['raw_text']}")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ COMMAND RESPONSE ERROR: {ex}")

    elif protocol == "6e":

        log("⚙️ Configuration Packet")

        try:

            device = _get_device(connection_imei)

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                log("✅ Configuration Packet Saved")

                current_imei = connection_imei

        except Exception as ex:

            log(f"❌ CONFIGURATION PACKET ERROR: {ex}")

    else:

        _log_unknown(protocol, raw, hex_data)

        try:
            log(f"ASCII    : {raw.decode('ascii')}")
        except UnicodeDecodeError:
            pass

    try:

        ack = build_ack(raw)

        conn.send(ack)

        log(f"📤 ACK sent: {ack.hex().upper()}")

    except Exception as ex:

        log(f"❌ ACK ERROR: {ex}")

    return current_imei