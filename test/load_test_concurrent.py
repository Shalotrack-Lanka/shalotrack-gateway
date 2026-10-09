"""
Concurrency load test for the asyncio gateway rewrite.

Opens N fake devices simultaneously (asyncio, not threads — this script
itself proves asyncio client-side concurrency is cheap, same idea as the
gateway's own server-side concurrency). Each device logs in, sends a few
GPS location updates spaced out like a real tracker would, and the
script reports how many succeeded, failed, and how long it all took.

Requires: 50 test devices pre-loaded via load_test_devices.sql
(IMEIs 355172100000001 .. 355172100000050, all Activated).

Usage:
    python test/load_test_concurrent.py
    python test/load_test_concurrent.py --devices 200   # push harder
"""

import argparse
import asyncio
import time
from datetime import datetime, timezone

HOST = "127.0.0.1"
PORT = 9000

BASE_IMEI = "35517210000"  # + 4-digit zero-padded index 0001..NNNN


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
    imei_bytes = bytes.fromhex(imei.zfill(16))
    serial_bytes = serial.to_bytes(2, "big")
    body = bytes([0x01]) + imei_bytes + serial_bytes
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0D\x0A"


def build_location_packet(serial: int, lat: float, lon: float, speed: int, heading: int) -> bytes:
    now = datetime.now(timezone.utc)
    date_time = bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])
    gps_info = bytes([0x0C])
    lat_raw = int(abs(lat) * 1_800_000)
    lon_raw = int(abs(lon) * 1_800_000)
    course_status = (heading & 0x03FF) | 0x0400 | 0x1000
    payload = (
        date_time + gps_info
        + lat_raw.to_bytes(4, "big") + lon_raw.to_bytes(4, "big")
        + bytes([speed]) + course_status.to_bytes(2, "big")
    )
    serial_bytes = serial.to_bytes(2, "big")
    body = bytes([0x12]) + payload + serial_bytes
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0D\x0A"


async def simulate_device(index: int, updates: int, results: dict) -> None:
    imei = f"{BASE_IMEI}{index:04d}"
    serial = 1
    start = time.monotonic()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT), timeout=5.0
        )

        login_pkt = build_login_packet(imei, serial)
        writer.write(login_pkt)
        await writer.drain()
        serial += 1

        ack = await asyncio.wait_for(reader.read(1024), timeout=5.0)
        if not ack or ack[0:2] != b"\x78\x78":
            raise RuntimeError(f"bad login ack: {ack!r}")

        for i in range(updates):
            lat = 6.9271 + (index * 0.001)
            lon = 79.8612 + (i * 0.001)
            loc_pkt = build_location_packet(serial, lat, lon, speed=30 + i, heading=90)
            writer.write(loc_pkt)
            await writer.drain()
            serial += 1

            ack = await asyncio.wait_for(reader.read(1024), timeout=5.0)
            if not ack or ack[0:2] != b"\x78\x78":
                raise RuntimeError(f"bad location ack on update {i}: {ack!r}")

            await asyncio.sleep(0.2)

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        elapsed = time.monotonic() - start
        results["ok"].append((imei, elapsed))

    except Exception as ex:
        elapsed = time.monotonic() - start
        results["failed"].append((imei, str(ex), elapsed))


async def main(num_devices: int, updates_per_device: int):
    print(f"Simulating {num_devices} concurrent devices, {updates_per_device} GPS updates each...")
    print(f"(Requires IMEIs {BASE_IMEI}0001..{BASE_IMEI}{num_devices:04d} pre-loaded as Activated)\n")

    results = {"ok": [], "failed": []}
    start = time.monotonic()

    tasks = [
        simulate_device(i, updates_per_device, results)
        for i in range(1, num_devices + 1)
    ]
    await asyncio.gather(*tasks)

    total_elapsed = time.monotonic() - start

    print(f"{'='*60}")
    print(f"RESULTS — {num_devices} devices, {updates_per_device} updates each")
    print(f"{'='*60}")
    print(f"✅ Succeeded : {len(results['ok'])}")
    print(f"❌ Failed    : {len(results['failed'])}")
    print(f"⏱  Total wall time : {total_elapsed:.2f}s")

    if results["ok"]:
        times = [t for _, t in results["ok"]]
        print(f"   Avg per-device time : {sum(times)/len(times):.2f}s")
        print(f"   Max per-device time : {max(times):.2f}s")

    if results["failed"]:
        print(f"\nFAILURES:")
        for imei, err, elapsed in results["failed"][:10]:
            print(f"   {imei}: {err} (after {elapsed:.2f}s)")
        if len(results["failed"]) > 10:
            print(f"   ... and {len(results['failed']) - 10} more")

    if not results["failed"]:
        print(f"\n✅ ALL {num_devices} DEVICES SUCCEEDED — gateway handled full concurrency cleanly.")
    else:
        print(f"\n⚠️  {len(results['failed'])}/{num_devices} devices failed — investigate before deploying.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=50)
    parser.add_argument("--updates", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.devices, args.updates))