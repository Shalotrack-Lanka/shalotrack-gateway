import time
import json
from telemetry import telemetry as tel
from opentelemetry import trace

from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    parse_status_packet,
    parse_heartbeat_packet,
    parse_alarm_packet,
    parse_information_packet,
    get_alarm_name,
    build_ack
)

from parsers.command_response_parser import parse_command_response
from services.command_response_service import handle_command_response
from services.device_registry import register_device, get_device, get_imei_by_socket
from services.tracking_service import save_tracking, update_current_location, update_device_status, update_heartbeat
from repositories.device_repository import DeviceRepository
from repositories.raw_packet_repository import RawPacketRepository
from services.event_service import create_event
from constants.event_types import EventType
from constants.severity import Severity
from decoders.information_decoder import decode_information
from utils.logger import log


def _get_device(imei, conn=None):
    if not imei:
        # Force close the connection so the device must reconnect and send login
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        log("❌ Unknown Device — connection closed to force re-login")
        return None
    device = get_device(imei)
    if not device:
        log(f"❌ Device not registered: {imei}")
    return device


def _save_raw(device_id, protocol, hex_data):
    RawPacketRepository.save(
        device_id=device_id,
        protocol_number=protocol,
        raw_hex=hex_data,
        parsed=True
    )


def _log_unknown(protocol, raw, hex_data):
    log("\n========== UNKNOWN PACKET ==========")
    log(f"Protocol : {protocol}")
    log(f"Length   : {len(raw)}")
    log(f"Raw HEX  : {hex_data}")
    log("====================================")


def process_packet(data, conn, addr):
    start_time = time.time()

    with tel.tracer.start_as_current_span("process_gps_packet") as span:
        span.set_attribute("packet.raw_length", len(data))

        raw = data

        if raw[0:2] == b"\x78\x78":
            protocol = f"{raw[3]:02x}"
        elif raw[0:2] == b"\x79\x79":
            protocol = f"{raw[4]:02x}"
        else:
            log("❌ Invalid packet")
            tel.parsing_errors.add(1, {"error_type": "InvalidHeader"})
            return

        span.set_attribute("packet.protocol", protocol)
        log(f"Protocol: {protocol}")
        hex_data = raw.hex()

        current_imei = None
        connection_imei = get_imei_by_socket(conn)

        # ================================================================
        # 0x01 — LOGIN
        # ================================================================
        if protocol == "01":
            log("📡 Login Packet")
            try:
                login = parse_login_packet(raw)
                span.set_attribute("device.imei", login["imei"])
                log(f"📡 Login packet received — IMEI: {login['imei']}")

                device_id = DeviceRepository.get_device_by_imei(login["imei"])
                log(f"🔍 Device lookup: {login['imei']}")

                if not device_id:
                    log(f"🚫 REJECTED — IMEI not admin-activated: {login['imei']} from {addr[0]}")
                    tel.parsing_errors.add(1, {"error_type": "UnauthorisedDevice", "protocol": protocol})
                else:
                    log(f"✅ Authorised Device — IMEI: {login['imei']}")
                    register_device(login["imei"], addr[0], device_id, conn)
                    _save_raw(device_id, protocol, hex_data)
                    log(f"📱 IMEI: {login['imei']}")
                    log(f"🆔 Device ID: {device_id}")
                    log(f"🔢 Serial: {login['serial']}")
                    vehicle_id = DeviceRepository.get_vehicle_by_device(device_id)
                    create_event(
                        device_id=device_id,
                        vehicle_id=vehicle_id,
                        event_type=EventType.DEVICE_ONLINE.value,
                        severity=Severity.LOW,
                        description="Device connected to the gateway"
                    )
                    current_imei = login["imei"]

            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ LOGIN ERROR: {ex}")

        # ================================================================
        # 0x12 / 0x22 — GPS LOCATION
        # ================================================================
        elif protocol in ["12", "22"]:
            log("📍 LOCATION RECEIVED")
            try:
                location = parse_location_packet(raw)
                log(f"Latitude: {location['latitude']}")
                log(f"Longitude: {location['longitude']}")
                log(f"Speed: {location['speed']}")
                log(f"Time: {location['timestamp']}")
                log(f"🗺 https://maps.google.com/?q={location['latitude']},{location['longitude']}")

                device = _get_device(connection_imei, conn)
                if device:
                    _save_raw(device["device_id"], protocol, hex_data)
                    save_tracking(
                        device_id=device["device_id"],
                        latitude=location["latitude"],
                        longitude=location["longitude"],
                        speed=location["speed"],
                        heading=location["heading"],
                        satellites=location["satellites"],
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
                            heading=location["heading"],
                            event_time=location["timestamp"]
                        )
                        log("✅ Current Location Updated")
                    else:
                        log("ℹ️ Device not assigned to a vehicle. Skipping CurrentLocation update.")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ LOCATION ERROR: {ex}")

        # ================================================================
        # 0x13 — STATUS
        # ================================================================
        elif protocol == "13":
            log("⚡ Status Packet")
            try:
                device = _get_device(connection_imei, conn)
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
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ STATUS ERROR: {ex}")

        # ================================================================
        # 0x23 — HEARTBEAT
        # ================================================================
        elif protocol == "23":
            log("💓 Heartbeat Packet")
            try:
                device = _get_device(connection_imei, conn)
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
                    update_heartbeat(device["device_id"])
                    log("💓 Heartbeat Updated")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ HEARTBEAT ERROR: {ex}")

        # ================================================================
        # 0x26 — ALARM
        # ================================================================
        elif protocol == "26":
            log("🚨 Alarm Packet")
            try:
                device = _get_device(connection_imei, conn)
                if device:
                    alarm = parse_alarm_packet(raw)
                    _save_raw(device["device_id"], protocol, hex_data)

                    alarm_name = get_alarm_name(alarm["alarm_type"])
                    log(f"🚨 Alarm Type  : {alarm_name}")
                    log(f"Latitude       : {alarm['latitude']}")
                    log(f"Longitude      : {alarm['longitude']}")
                    log(f"Speed          : {alarm['speed']}")
                    log(f"Timestamp      : {alarm['timestamp']}")
                    log(f"Battery        : {alarm['battery_level']}")
                    log(f"Signal         : {alarm['gsm_signal']}")
                    log(f"Charging       : {alarm['charging']}")
                    log(f"Power Cut      : {alarm['power_cut']}")
                    log(f"Ignition       : {alarm['ignition_status']}")

                    high_severity_alarms = {1, 2, 12, 13}
                    severity = (
                        Severity.HIGH if alarm["alarm_type"] in high_severity_alarms
                        else Severity.MEDIUM
                    )

                    vehicle_id = DeviceRepository.get_vehicle_by_device(device["device_id"])
                    create_event(
                        device_id=device["device_id"],
                        vehicle_id=vehicle_id,
                        event_type=alarm_name,
                        severity=severity,
                        latitude=alarm["latitude"],
                        longitude=alarm["longitude"],
                        description=f"Alarm: {alarm_name} at {alarm['timestamp']}",
                        metadata={
                            "alarm_type": alarm["alarm_type"],
                            "speed": alarm["speed"],
                            "battery_level": alarm["battery_level"],
                            "gsm_signal": alarm["gsm_signal"],
                            "ignition_status": alarm["ignition_status"],
                            "power_cut": alarm["power_cut"],
                            "charging": alarm["charging"],
                            "mcc": alarm["mcc"],
                            "mnc": alarm["mnc"],
                            "lac": alarm["lac"],
                            "cell_id": alarm["cell_id"],
                        }
                    )
                    log(f"✅ Alarm Event persisted to DB: {alarm_name}")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ ALARM ERROR: {ex}")

        # ================================================================
        # 0x94 — INFORMATION
        # ================================================================
        elif protocol == "94":
            log("📡 Information Packet")
            try:
                device = _get_device(connection_imei, conn)
                if device:
                    _save_raw(device["device_id"], protocol, hex_data)
                    info = parse_information_packet(raw)

                    if info["is_ascii"]:
                        log(f"ℹ️ Information (text): {info['text']}")
                        if info["values"]:
                            decoded = decode_information(info["values"])
                            log(f"ℹ️ Decoded config: {json.dumps(decoded, default=str)}")
                            vehicle_id = DeviceRepository.get_vehicle_by_device(device["device_id"])
                            create_event(
                                device_id=device["device_id"],
                                vehicle_id=vehicle_id,
                                event_type="DEVICE_INFORMATION",
                                severity=Severity.LOW,
                                description="Device configuration received",
                                metadata=decoded
                            )
                            log("✅ Device configuration persisted to DB")
                        else:
                            log("ℹ️ Information packet had no parseable key=value pairs")
                    else:
                        log(f"ℹ️ Information (non-ASCII payload): {info['raw_hex']}")

                    log("✅ Information Packet Saved")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ INFO PACKET ERROR: {ex}")

        # ================================================================
        # 0x8a — COMMAND ACK
        # ================================================================
        elif protocol == "8a":
            log("📨 Command ACK Packet")
            try:
                device = _get_device(connection_imei, conn)
                if device:
                    _save_raw(device["device_id"], protocol, hex_data)
                    if raw[0:2] == b"\x79\x79":
                        body_start = 5
                    else:
                        body_start = 4
                    echoed_protocol = f"{raw[body_start]:02x}"
                    echoed_serial = raw[body_start + 1: body_start + 3].hex()
                    log(f"✅ Command ACK — echoed protocol: 0x{echoed_protocol}, serial: {echoed_serial}")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ COMMAND ACK ERROR: {ex}")

        # ================================================================
        # 0x21 — COMMAND TEXT RESPONSE
        # ================================================================
        elif protocol == "21":
            log("📨 Command Text Response")
            try:
                device = _get_device(connection_imei, conn)
                if device:
                    _save_raw(device["device_id"], protocol, hex_data)
                    response = parse_command_response(raw)
                    handle_command_response(device["device_id"], connection_imei, response)
                    log(f"Command  : {response['command']}")
                    log(f"Raw Text : {response['raw_text']}")
                    if response["data"]:
                        log(f"Parsed   : {json.dumps(response['data'], default=str)}")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ COMMAND RESPONSE ERROR: {ex}")

        # ================================================================
        # 0x6e — CONFIGURATION
        # ================================================================
        elif protocol == "6e":
            log("⚙️ Configuration Packet")
            try:
                device = _get_device(connection_imei, conn)
                if device:
                    _save_raw(device["device_id"], protocol, hex_data)
                    info = parse_information_packet(raw)

                    if info["is_ascii"]:
                        log(f"⚙️ Config (text): {info['text']}")
                        if info["values"]:
                            decoded = decode_information(info["values"])
                            log(f"⚙️ Decoded config: {json.dumps(decoded, default=str)}")
                            vehicle_id = DeviceRepository.get_vehicle_by_device(device["device_id"])
                            create_event(
                                device_id=device["device_id"],
                                vehicle_id=vehicle_id,
                                event_type="DEVICE_CONFIGURATION",
                                severity=Severity.LOW,
                                description="Device configuration packet received on connect",
                                metadata=decoded
                            )
                            log("✅ Configuration persisted to DB")
                        else:
                            log("⚙️ Configuration packet had no parseable key=value pairs")
                    else:
                        log(f"⚙️ Configuration (non-ASCII payload): {info['raw_hex']}")

                    log("✅ Configuration Packet Saved")
                    current_imei = connection_imei
            except Exception as ex:
                tel.parsing_errors.add(1, {"error_type": type(ex).__name__, "protocol": protocol})
                span.record_exception(ex)
                span.set_status(trace.StatusCode.ERROR, str(ex))
                log(f"❌ CONFIGURATION PACKET ERROR: {ex}")

        else:
            _log_unknown(protocol, raw, hex_data)
            try:
                log(f"ASCII    : {raw.decode('ascii')}")
            except UnicodeDecodeError:
                pass

        # ACK always fires — even for rejected/unknown devices
        # BUT if we closed the connection above, send will silently fail — that's fine
        try:
            ack = build_ack(raw)
            conn.send(ack)
            log(f"📤 ACK sent: {ack.hex().upper()}")
        except Exception as ex:
            log(f"❌ ACK ERROR: {ex}")

        tel.packets_processed.add(1, {"status": "success", "protocol": protocol})
        duration = time.time() - start_time
        tel.processing_latency.record(duration)

        return current_imei