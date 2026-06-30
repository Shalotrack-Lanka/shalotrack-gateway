from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    parse_status_packet,
    parse_heartbeat_packet,
    parse_alarm_packet,
    get_alarm_name,
    build_ack
)
from services.device_registry import (
    register_device,
    get_device
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



# ---------------------------------------------------------------------------
# TODO (Bug 4): Replace TEST_IMEI with per-connection IMEI once multi-tracker
# support is wired end-to-end. Only one place to change when that happens.
TEST_IMEI = "355172106043787"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_device(imei=None):
    """Look up a registered device and print a clear error if not found.
    Defaults to TEST_IMEI. Pass a specific IMEI to override.
    """
    if imei is None:
        imei = TEST_IMEI
    device = get_device(imei)
    if not device:
        print("❌ Unknown Device")
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
    print("❓ Unknown Packet")
    print("Protocol :", protocol)
    print("Length   :", len(raw))
    print("Raw      :", hex_data)


# ---------------------------------------------------------------------------


def process_packet(data, conn, addr):

    # FIX: Detect protocol correctly for both 7878 (short) and 7979 (long) frames.
    # Previously hex_data[6:8] was used, which read the wrong byte offset for 7979
    # frames — causing 94 packets to appear as protocol "08" and fall into Unknown.
    raw = data

    if raw[0:2] == b"\x78\x78":
        protocol = f"{raw[3]:02x}"

    elif raw[0:2] == b"\x79\x79":
        protocol = f"{raw[4]:02x}"

    else:
        print("❌ Invalid packet")
        return

    print("Protocol:", protocol)

    hex_data = raw.hex()

    # FIX (Bug 3): Use current_imei to track the result instead of returning early.
    # The ACK must always be sent at the end, so we never return mid-function.
    current_imei = None

    if protocol == "01":

        print("📡 Login Packet")

        try:

            login = parse_login_packet(raw)

            print("DEBUG 1 - Login Parsed")

            device_id = DeviceRepository.get_device_by_imei(login["imei"])

            print(
                f"DEBUG 2 - Device Lookup Result: {device_id}"
            )

            if not device_id:

                print(
                    f"❌ Unauthorized Device: {login['imei']}"
                )
                current_imei = None  # explicitly reject — ACK still sent, but device stays unregistered

            else:

                print("DEBUG 3 - Device Authorized")

                register_device(
                    login["imei"],
                    addr[0],
                    device_id,
                    conn
                )

                print("DEBUG 4 - Device Registered")

                _save_raw(device_id, protocol, hex_data)

                print("DEBUG 5 - Raw Packet Saved")

                print("📱 IMEI:", login["imei"])
                print("🆔 Device ID:", device_id)
                print("🔢 Serial:", login["serial"])

                vehicle_id = DeviceRepository.get_vehicle_by_device(device_id)

                create_event(
                    device_id=device_id,
                    vehicle_id=vehicle_id,
                    event_type=EventType.DEVICE_ONLINE.value,
                    severity=Severity.LOW,
                    description="Device connected to the gateway"
                )

                current_imei = login["imei"]  # FIX (Bug 3): store instead of return

        except Exception as ex:

            print(
                f"❌ LOGIN ERROR: {ex}"
            )

    elif protocol in ["12", "22"]:

        print("📍 LOCATION RECEIVED")

        try:

            location = parse_location_packet(raw)

            print("Latitude:", location["latitude"])
            print("Longitude:", location["longitude"])
            print("Speed:", location["speed"])
            print("Time:", location["timestamp"])

            print(
                f"🗺 https://maps.google.com/?q={location['latitude']},{location['longitude']}"
            )

            # TODO (Bug 4): Replace hardcoded IMEI with current_imei once login flow is wired end-to-end
            device = _get_device()

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                save_tracking(
                    device_id=device["device_id"],
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                    speed=location["speed"],
                    event_time=location["timestamp"]
                )

                print("✅ GPS Tracking Saved")

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

                print("✅ Current Location Updated")

                current_imei = TEST_IMEI

        except Exception as ex:

            print(
                f"❌ LOCATION ERROR: {ex}"
            )

    elif protocol == "13":

        print("⚡ Status Packet")

        try:
            device = _get_device()

            if device:
                status = parse_status_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                update_device_status(
                    device_id=device["device_id"],
                    battery_level=status["battery_level"],
                    gps_signal=status["gsm_signal"],
                    ignition_status=status["ignition_status"],
                    movement_status=status["ignition_status"],  # no speed in status packet; ignition is the best proxy
                    power_status=1 if not status["power_cut"] else 2
                )

                print("Battery:", status["battery_level"])
                print("Signal:", status["gsm_signal"])
                print("Ignition:", status["ignition_status"])
                print("Power Cut:", status["power_cut"])
                print("Charging:", status["charging"])

                current_imei = TEST_IMEI  # FIX (Bug 3): store instead of return

        except Exception as ex:
            print(f"❌ STATUS ERROR: {ex}")

    elif protocol == "23":

        print("💓 Heartbeat Packet")

        try:

            device = _get_device()

            if device:

                heartbeat = parse_heartbeat_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                # FIX (Bug 1): device is a dict, not a string — use device["device_id"].
                # FIX (Bug 1): heartbeat["heartbeat"] doesn't exist — use correct field names.
                update_device_status(
                    device_id=device["device_id"],
                    battery_level=heartbeat["battery_level"],
                    gps_signal=heartbeat["gsm_signal"],
                    ignition_status=heartbeat["ignition_status"],
                    movement_status=heartbeat["ignition_status"],  # no speed in heartbeat packet; ignition is the best proxy
                    power_status=1 if not heartbeat["power_cut"] else 2
                )

                update_heartbeat(
                    device["device_id"]
                )

                print("💓 Heartbeat Updated")
                current_imei = TEST_IMEI  # FIX (Bug 3): store instead of return

        except Exception as ex:

            print(f"❌ HEARTBEAT ERROR: {ex}")

    elif protocol == "26":

        print("🚨 Alarm Packet")

        try:

            device = _get_device()

            if device:

                alarm = parse_alarm_packet(raw)

                _save_raw(device["device_id"], protocol, hex_data)

                print("🚨 Alarm Packet Saved")
                print(
                    "Alarm :",
                    get_alarm_name(
                        alarm["alarm_type"]
                    )
                )
                print("Latitude :", alarm["latitude"])
                print("Longitude :", alarm["longitude"])
                print("Speed :", alarm["speed"])
                print("Timestamp :", alarm["timestamp"])
                print("Battery :", alarm["battery_level"])
                print("Signal :", alarm["gsm_signal"])
                print("Charging :", alarm["charging"])
                print("Power Cut :", alarm["power_cut"])
                print("Ignition :", alarm["ignition_status"])

                current_imei = TEST_IMEI  # FIX (Bug 3): store instead of return

        except Exception as ex:

            print(f"❌ ALARM ERROR: {ex}")

    elif protocol == "94":

        print("📡 Information Packet")

        try:

            device = _get_device()

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                print("✅ Information Packet Saved")

                current_imei = TEST_IMEI

        except Exception as ex:

            print(f"❌ INFO PACKET ERROR: {ex}")

    elif protocol == "8a":

        print("📨 Command Response Packet")

        try:

            device = _get_device()

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                print("✅ Command Response Saved")

                current_imei = TEST_IMEI

        except Exception as ex:

            print(f"❌ COMMAND RESPONSE ERROR: {ex}")

    elif protocol == "21":

        print("📨 Command Text Response")

        try:

            device = _get_device()

            if device:

                _save_raw(
                    device["device_id"],
                    protocol,
                    hex_data
                )

                if raw[:2] == b"\x79\x79":
                    payload = raw[10:-6]
                else:
                    payload = raw[9:-6]

                text = payload.decode(
                    "ascii",
                    errors="ignore"
                )

                print()
                print("=" * 60)
                print(text)
                print("=" * 60)

                current_imei = TEST_IMEI

        except Exception as ex:

            print(ex)

    elif protocol == "6e":

        print("⚙️ Configuration Packet")

        try:

            device = _get_device()

            if device:

                _save_raw(device["device_id"], protocol, hex_data)

                print("✅ Configuration Packet Saved")

                current_imei = TEST_IMEI

        except Exception as ex:

            print(f"❌ CONFIGURATION PACKET ERROR: {ex}")

    else:

        print("\n========== UNKNOWN PACKET ==========")
        print("Protocol :", protocol)
        print("Length   :", len(raw))
        print("Raw HEX  :", hex_data)

        try:
            print("ASCII    :", raw.decode("ascii"))
        except:
            pass

        print("====================================\n")

    # FIX (Bug 3): ACK is now always sent at the end, regardless of protocol.
    # Previously, every early `return` inside each branch silently skipped this block,
    # causing trackers to never receive acknowledgment and reconnect in a loop.
    try:

        ack = build_ack(data)

        conn.send(ack)

        print(
            "📤 ACK sent:",
            ack.hex()
        )

    except Exception as ex:

        print(
            f"❌ ACK ERROR: {ex}"
        )

    return current_imei