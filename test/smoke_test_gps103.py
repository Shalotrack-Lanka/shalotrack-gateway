"""
Standalone smoke test: simulates a TK103A/B (GPS103 protocol) unit against a
RUNNING gateway. Use it on staging to prove the whole chain (detection, login
allowlist, DB writes) before a physical unit is available.

    python test/smoke_test_gps103.py <imei> [host] [port]

The IMEI must be Activated in SetupShalotrackDevices (or already in
GpsDevices), exactly like a real unit. No imports from the gateway code.

Afterwards, check the database: a GpsDevices row (ProtocolType = 'GPS103'), a
GpsTrackings row near lat 6.9271 / lon 79.8612, a DEVICE_ONLINE event, and an
SOS row in DeviceEvents.
"""

import socket
import sys
import time
from datetime import datetime, timezone


def nmea(value: float, width: int) -> str:
    degrees = int(abs(value))
    minutes = (abs(value) - degrees) * 60
    return f"{degrees:0{width}d}{minutes:07.4f}"


def tracker(imei: str, keyword: str, lat: float, lon: float, knots: float) -> bytes:
    now = datetime.now(timezone.utc)
    local = now.astimezone(timezone.utc).strftime("%y%m%d%H%M%S")      # offset 0: valid, simplest
    utc = now.strftime("%H%M%S.000")
    hemi_lat = "N" if lat >= 0 else "S"
    hemi_lon = "E" if lon >= 0 else "W"
    return (
        f"imei:{imei},{keyword},{local},,F,{utc},A,"
        f"{nmea(lat, 2)},{hemi_lat},{nmea(lon, 3)},{hemi_lon},{knots:.2f},90;"
    ).encode()


def expect(sock: socket.socket, expected: bytes, label: str):
    reply = sock.recv(1024)
    assert reply == expected, f"{label}: expected {expected!r}, got {reply!r}"
    print(f"  {label}: {reply!r} OK")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    imei = sys.argv[1]
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 9000

    sock = socket.create_connection((host, port), timeout=5)
    print(f"Connected to {host}:{port}")

    sock.sendall(f"##,imei:{imei},A;".encode())
    expect(sock, b"LOAD", "login")
    time.sleep(0.3)

    sock.sendall(f"{imei};".encode())
    expect(sock, b"ON", "heartbeat")

    sock.sendall(tracker(imei, "tracker", 6.9271, 79.8612, 12.0))
    print("  position sent (no reply expected)")
    time.sleep(0.3)

    sock.sendall(tracker(imei, "help me", 6.9271, 79.8612, 0.0))
    expect(sock, f"**,imei:{imei},E;".encode(), "SOS acknowledgement")

    print("\n✅ GPS103 smoke test passed. Verify the rows in the database.")
    sock.close()


if __name__ == "__main__":
    main()