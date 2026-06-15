from parsers.v5_parser import (
    parse_login_packet,
    parse_location_packet,
    build_ack
)

from services.device_registry import register_device


def process_packet(data, conn, addr):

    hex_data = data.hex()

    if len(hex_data) < 8:
        return

    protocol = hex_data[6:8]

    print("Protocol:", protocol)

    raw = bytes.fromhex(hex_data)

    if protocol == "01":

        print("📡 Login Packet")

        login = parse_login_packet(raw)

        register_device(
            login["imei"],
            addr[0]
        )

        print("📱 IMEI:", login["imei"])
        print("🔢 Serial:", login["serial"])

    elif protocol in ["12", "22"]:

        print("📍 LOCATION RECEIVED")

        location = parse_location_packet(raw)

        print("Latitude:", location["latitude"])
        print("Longitude:", location["longitude"])
        print("Speed:", location["speed"])
        print("Time:", location["timestamp"])

        print(
            f"🗺 https://maps.google.com/?q={location['latitude']},{location['longitude']}"
        )

    elif protocol == "13":

        print("⚡ Status Packet")

    elif protocol == "23":

        print("💓 Heartbeat Packet")

    else:

        print("❓ Unknown Packet")

    ack = build_ack(data)

    conn.send(ack)

    print("📤 ACK sent:", ack.hex())