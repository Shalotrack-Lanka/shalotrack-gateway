"""
Same as smoke_test_tcp.py, but stays connected after login so you can
send it a live command via the Command API while it's listening.

Run this, then in another terminal:
    curl.exe -X POST http://localhost:9001/command -H "Content-Type: application/json" -d "{\"imei\":\"355172106043787\",\"command\":\"where\"}"

This script will print whatever bytes the gateway writes back to the
device socket — that's the command actually being delivered over the
TCP connection, proving the full command-send path works.

Press Ctrl+C to disconnect when done.
"""

import socket
import time

HOST = "127.0.0.1"
PORT = 9000
TEST_IMEI = "355172106043787"


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
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0D\x0A"


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(60.0)
    sock.connect((HOST, PORT))
    print(f"Connected to {HOST}:{PORT}")

    login_pkt = build_login_packet(TEST_IMEI, serial=1)
    sock.sendall(login_pkt)
    ack = sock.recv(1024)
    print(f"LOGIN ACK OK — {ack.hex()}")
    print("\nStaying connected. Now run the curl command in another terminal.")
    print("Waiting up to 60s for the gateway to push a command down this socket...\n")

    try:
        data = sock.recv(1024)
        if data:
            print(f"✅ COMMAND RECEIVED FROM GATEWAY — {data.hex()}")
            print("   This is the server pushing a command down to the device —")
            print("   proving the command_api.py -> event loop bridge works.")
        else:
            print("Connection closed by server with no data.")
    except socket.timeout:
        print("❌ No command received within 60s — nothing came through.")

    sock.close()


if __name__ == "__main__":
    main()