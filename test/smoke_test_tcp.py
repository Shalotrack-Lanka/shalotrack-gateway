"""
Standalone smoke test for the asyncio gateway rewrite.

Simulates a real GT06/V5 device over a raw TCP socket — no mocking, no
imports from the gateway codebase. Sends a LOGIN packet, then a LOCATION
packet, and checks the gateway ACKs both correctly.
"""

import socket
import time
from datetime import datetime, timezone

HOST = "127.0.0.1"
PORT = 9000

TEST_IMEI = "355172106043787"  # pre-existing test device


def crc16_x25(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    crc ^= 0xFFFF
    return crc & 0xFFFF


def build_login_packet(imei: str, serial: int) -> bytes:
    imei_padded = imei.zfill(16)
    imei_bytes = bytes.fromhex(imei_padded)
    serial_bytes = serial.to_bytes(2, "big")

    body = bytes([0x01]) + imei_bytes + serial_bytes
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)

    return (
        b"\x78\x78"
        + bytes([length])
        + body
        + crc.to_bytes(2, "big")
        + b"\x0D\x0A"
    )


def build_location_packet(serial: int, lat: float, lon: float, speed: int, heading: int) -> bytes:
    now = datetime.now(timezone.utc)
    date_time = bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])

    gps_info = bytes([0x0C])

    lat_raw = int(abs(lat) * 1_800_000)
    lon_raw = int(abs(lon) * 1_800_000)

    course_status = (heading & 0x03FF) | 0x0400 | 0x1000
    course_bytes = course_status.to_bytes(2, "big")

    payload = (
        date_time
        + gps_info
        + lat_raw.to_bytes(4, "big")
        + lon_raw.to_bytes(4, "big")
        + bytes([speed])
        + course_bytes
    )

    serial_bytes = serial.to_bytes(2, "big")
    body = bytes([0x12]) + payload + serial_bytes
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)

    return (
        b"\x78\x78"
        + bytes([length])
        + body
        + crc.to_bytes(2, "big")
        + b"\x0D\x0A"
    )


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect((HOST, PORT))
    print(f"Connected to {HOST}:{PORT}")

    login_pkt = build_login_packet(TEST_IMEI, serial=1)
    sock.sendall(login_pkt)
    ack = sock.recv(1024)
    assert ack, "No ACK received for LOGIN packet"
    assert ack[0:2] == b"\x78\x78", f"Malformed ACK: {ack.hex()}"
    print(f"LOGIN ACK OK — {ack.hex()}")

    time.sleep(0.3)

    loc_pkt = build_location_packet(serial=2, lat=6.9271, lon=79.8612, speed=42, heading=90)
    sock.sendall(loc_pkt)
    ack2 = sock.recv(1024)
    assert ack2, "No ACK received for LOCATION packet"
    assert ack2[0:2] == b"\x78\x78", f"Malformed ACK: {ack2.hex()}"
    print(f"LOCATION ACK OK — {ack2.hex()}")

    print("\n✅ Smoke test passed — gateway accepted login + GPS packet and ACKed both.")
    print(f"   Now verify in staging DB: IMEI {TEST_IMEI} should have a GpsDevices row,")
    print(f"   a GpsTrackings row near lat=6.9271 lon=79.8612, and a DEVICE_ONLINE event.")

    sock.close()


if __name__ == "__main__":
    main()