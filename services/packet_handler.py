from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    build_ack
)

from services.device_registry import (
    get_device,
    register_device,
    getdevice
)

from services.tracking_service import (
    get_device_by_imei,
    save_raw_packet,
    save_tracking
)


def process_packet(data, conn, addr):

    hex_data = data.hex()

    if len(hex_data) < 8:
        return

    protocol = hex_data[6:8]

    print("Protocol:", protocol)

    raw = bytes.fromhex(hex_data)

    if protocol == "01":

        print("📡 Login Packet")

        try:

            login = parse_login_packet(raw)

            print("DEBUG 1 - Login Parsed")

            device_id = get_device_by_imei(
                login["imei"]
            )

            print(
                f"DEBUG 2 - Device Lookup Result: {device_id}"
            )

            if not device_id:

                print(
                    f"❌ Unauthorized Device: {login['imei']}"
                )

                return

            print("DEBUG 3 - Device Authorized")

            register_device(
                login["imei"],
                addr[0],
                device_id
            )

            print("DEBUG 4 - Device Registered")

            save_raw_packet(
                device_id=device_id,
                protocol_number=protocol,
                raw_hex=hex_data,
                parsed=True
            )

            print("DEBUG 5 - Raw Packet Saved")

            print("📱 IMEI:", login["imei"])
            print("🆔 Device ID:", device_id)
            print("🔢 Serial:", login["serial"])

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

            #temporary device ID for testing - replace with actual device lookup in production
            device = get_device(
                "355172106043787"
            )

            save_tracking(
                device_id=device["DeviceId"],
                latitude=location["latitude"],
                longitude=location["longitude"],
                speed=location["speed"],
                event_time=location["timestamp"]
            )

            print(" GPS Tracking Saved")

        except Exception as ex:

            print(
                f"❌ LOCATION ERROR: {ex}"
            )

    elif protocol == "13":

        print("⚡ Status Packet")

    elif protocol == "23":

        print("💓 Heartbeat Packet")

    else:

        print("❓ Unknown Packet")

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