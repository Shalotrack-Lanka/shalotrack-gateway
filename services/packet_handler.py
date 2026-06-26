from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    build_ack,
    parse_status_packet
)

from services.device_registry import (
    get_device,
    register_device,
)

from services.tracking_service import (
    get_device_by_imei,
    save_raw_packet,
    save_tracking,
    get_vehicle_by_device,
    update_current_location,
    update_device_status
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
                device_id=device["device_id"],
                latitude=location["latitude"],
                longitude=location["longitude"],
                speed=location["speed"],
                event_time=location["timestamp"]
            )

            print(" GPS Tracking Saved")

            vehicle_id = get_vehicle_by_device(
                device["device_id"]
            )

            if vehicle_id:

                update_current_location(
                    device_id=device["device_id"],
                    vehicle_id=vehicle_id,
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                    speed=location["speed"],
                    event_time=location["timestamp"]
                )
            
            print(" Current Location Updated")

        except Exception as ex:

            print(
                f"❌ LOCATION ERROR: {ex}"
            )

    elif protocol == "13":

        print("⚡ Status Packet")

        try:
            device = get_device(addr[0])

            if not device:
                print(
                    f"❌ Unknown Device from IP: {addr[0]}"
                )
                return
            else:
                status = parse_status_packet(raw)
                update_device_status(
                    device_id=device["device_id"],
                    battery_level=status["battery_level"],
                    gps_signal=status["gsm_signal"],
                    ignition_status=status["ignition_status"],
                    movement_status=location["speed"] > 0,
                    power_status=1 if not status["power_cut"] else 2
                )

                print("Battery:", status["battery_level"])
                print("Signal:", status["gsm_signal"])
                print("Ignition:", status["ignition_status"])
                print("Power Cut:", status["power_cut"])
                print("Charging:", status["charging"])
        
        except Exception as ex:
            print(ex)


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