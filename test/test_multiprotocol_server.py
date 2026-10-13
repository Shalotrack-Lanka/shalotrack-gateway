"""
End-to-end tests for the multi-protocol TCP server.

Starts the REAL tcp_server.handle_device on a localhost port and talks to it
over real sockets. Only the database layer is replaced with mocks, so framing,
protocol detection, handler logic, the registry and connection teardown are
all exercised for real. Needs `asyncpg` and `python-dotenv` importable (they
are in requirements.txt) but never connects to a database.

    python -m unittest discover -s test -p "test_multiprotocol*.py" -v
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tcp_server                                              # noqa: E402
from services import device_registry                            # noqa: E402
from services.command_service import send_command, UnsupportedCommandError  # noqa: E402
from repositories.device_repository import DeviceRepository     # noqa: E402
from repositories.raw_packet_repository import RawPacketRepository  # noqa: E402
from parsers.v5_parser import crc16_x25                         # noqa: E402

KNOWN_IMEI = "359586015829802"
OTHER_IMEI = "868683025991310"
GT06_IMEI = "355172106043787"
DEVICE_ID = "11111111-1111-1111-1111-111111111111"
VEHICLE_ID = "22222222-2222-2222-2222-222222222222"

LOGIN = f"##,imei:{KNOWN_IMEI},A;".encode()
HEARTBEAT = f"{KNOWN_IMEI};".encode()


def tracker(keyword="tracker", imei=KNOWN_IMEI, lat="0656.2000", lon="07951.2000", flag="A", speed="10.00"):
    return (
        f"imei:{imei},{keyword},261013143012,,F,090012.000,{flag},{lat},N,{lon},E,{speed},90;"
    ).encode()


# --- GT06 helpers (same framing as test/smoke_test_tcp.py) -----------------

def gt06_login(imei: str, serial: int = 1) -> bytes:
    body = bytes([0x01]) + bytes.fromhex(imei.zfill(16)) + serial.to_bytes(2, "big")
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0d\x0a"


def gt06_location(serial: int = 2) -> bytes:
    now = datetime.now(timezone.utc)
    dt = bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])
    payload = (
        dt + bytes([0x0C])
        + int(6.9271 * 1_800_000).to_bytes(4, "big")
        + int(79.8612 * 1_800_000).to_bytes(4, "big")
        + bytes([42])
        + ((90 & 0x03FF) | 0x0400 | 0x1000).to_bytes(2, "big")
    )
    body = bytes([0x12]) + payload + serial.to_bytes(2, "big")
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0d\x0a"


class ServerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        device_registry._by_imei.clear()
        device_registry._by_socket.clear()

        async def lookup(imei, **kwargs):
            return DEVICE_ID if imei in (KNOWN_IMEI, OTHER_IMEI, GT06_IMEI) else None

        self.mocks = {}
        patches = {
            "lookup": patch.object(DeviceRepository, "get_device_by_imei", side_effect=lookup),
            "vehicle": patch.object(DeviceRepository, "get_vehicle_by_device", return_value=VEHICLE_ID),
            "raw": patch.object(RawPacketRepository, "save"),
            "event_g103": patch("services.gps103_handler.create_event", new_callable=AsyncMock),
            "track_g103": patch("services.gps103_handler.save_tracking", new_callable=AsyncMock),
            "current_g103": patch("services.gps103_handler.update_current_location", new_callable=AsyncMock),
            "alive": patch("services.gps103_handler.mark_device_alive", new_callable=AsyncMock),
            "ignition": patch("services.gps103_handler.set_ignition", new_callable=AsyncMock),
            "event_gt06": patch("services.packet_handler.create_event", new_callable=AsyncMock),
            "track_gt06": patch("services.packet_handler.save_tracking", new_callable=AsyncMock),
            "current_gt06": patch("services.packet_handler.update_current_location", new_callable=AsyncMock),
            "offline": patch("tcp_server.set_device_offline", new_callable=AsyncMock),
        }
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        self.server = await asyncio.start_server(tcp_server.handle_device, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._stop_server)
        self._clients = []

    async def _stop_server(self):
        for _, writer in self._clients:
            writer.close()
        self.server.close()
        await self.server.wait_closed()

    async def connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self._clients.append((reader, writer))
        return reader, writer

    @staticmethod
    async def read(reader, timeout=2.0):
        return await asyncio.wait_for(reader.read(1024), timeout)

    @staticmethod
    async def until(predicate, timeout=2.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not met in time")
            await asyncio.sleep(0.01)

    async def login(self, payload=LOGIN):
        reader, writer = await self.connect()
        writer.write(payload)
        await writer.drain()
        self.assertEqual(await self.read(reader), b"LOAD")
        return reader, writer


class Gps103FlowTests(ServerTestCase):
    async def test_login_registers_device_as_gps103_and_replies_load(self):
        await self.login()
        entry = device_registry.get_device(KNOWN_IMEI)
        self.assertEqual(entry["protocol"], "GPS103")
        self.assertEqual(entry["device_id"], DEVICE_ID)
        # auto-registration must record the right protocol for a new device
        _, kwargs = self.mocks["lookup"].call_args
        self.assertEqual(kwargs["protocol_type"], "GPS103")
        await self.until(lambda: self.mocks["event_g103"].await_count == 1)
        self.assertEqual(self.mocks["event_g103"].call_args.kwargs["event_type"], "DEVICE_ONLINE")

    async def test_login_survives_fragmented_delivery(self):
        reader, writer = await self.connect()
        for chunk in (LOGIN[:3], LOGIN[3:11], LOGIN[11:]):
            writer.write(chunk)
            await writer.drain()
            await asyncio.sleep(0.05)
        self.assertEqual(await self.read(reader), b"LOAD")

    async def test_heartbeat_replies_on_and_marks_alive(self):
        reader, writer = await self.login()
        writer.write(HEARTBEAT)
        await writer.drain()
        self.assertEqual(await self.read(reader), b"ON")
        await self.until(lambda: any(c.kwargs.get("heartbeat") for c in self.mocks["alive"].await_args_list))

    async def test_valid_position_is_saved_with_converted_units(self):
        _, writer = await self.login()
        writer.write(tracker())
        await writer.drain()
        await self.until(lambda: self.mocks["track_g103"].await_count == 1)

        kwargs = self.mocks["track_g103"].call_args.kwargs
        self.assertAlmostEqual(kwargs["latitude"], 6 + 56.2 / 60, places=6)
        self.assertAlmostEqual(kwargs["longitude"], 79 + 51.2 / 60, places=6)
        self.assertAlmostEqual(kwargs["speed"], 18.52, places=2)       # 10 knots -> km/h
        self.assertEqual(kwargs["event_time"], datetime(2026, 10, 13, 9, 0, 12, tzinfo=timezone.utc))
        self.assertEqual(kwargs["satellites"], 0)
        await self.until(lambda: self.mocks["current_g103"].await_count == 1)
        self.assertEqual(self.mocks["current_g103"].call_args.kwargs["vehicle_id"], VEHICLE_ID)

    async def test_two_messages_in_one_segment_are_both_processed(self):
        _, writer = await self.login()
        writer.write(tracker() + tracker(lat="0656.3000"))
        await writer.drain()
        await self.until(lambda: self.mocks["track_g103"].await_count == 2)

    async def test_sos_is_acknowledged_and_recorded_as_high_severity(self):
        reader, writer = await self.login()
        writer.write(tracker("help me"))
        await writer.drain()
        self.assertEqual(await self.read(reader), f"**,imei:{KNOWN_IMEI},E;".encode())
        await self.until(lambda: any(
            c.kwargs.get("event_type") == "SOS" for c in self.mocks["event_g103"].await_args_list
        ))
        sos = next(c.kwargs for c in self.mocks["event_g103"].await_args_list if c.kwargs["event_type"] == "SOS")
        self.assertEqual(int(sos["severity"]), 3)
        self.assertIsNotNone(sos["latitude"])

    async def test_sos_at_invalid_position_is_still_recorded_without_coordinates(self):
        reader, writer = await self.login()
        writer.write(tracker("help me", lat="0000.0000", lon="00000.0000"))
        await writer.drain()
        await self.read(reader)                                       # SOS ack
        await self.until(lambda: any(
            c.kwargs.get("event_type") == "SOS" for c in self.mocks["event_g103"].await_args_list
        ))
        sos = next(c.kwargs for c in self.mocks["event_g103"].await_args_list if c.kwargs["event_type"] == "SOS")
        self.assertIsNone(sos["latitude"])
        self.mocks["track_g103"].assert_not_awaited()

    async def test_no_fix_report_stores_no_position(self):
        _, writer = await self.login()
        writer.write(tracker(flag="V"))
        await writer.drain()
        await asyncio.sleep(0.2)
        self.mocks["track_g103"].assert_not_awaited()

    async def test_acc_on_updates_ignition_and_records_event(self):
        _, writer = await self.login()
        writer.write(tracker("acc on"))
        await writer.drain()
        await self.until(lambda: self.mocks["ignition"].await_count == 1)
        self.assertEqual(self.mocks["ignition"].call_args.args, (DEVICE_ID, True))
        await self.until(lambda: any(
            c.kwargs.get("event_type") == "ACC_ON" for c in self.mocks["event_g103"].await_args_list
        ))

    async def test_a_failing_location_write_does_not_block_the_alarm_event(self):
        self.mocks["track_g103"].side_effect = RuntimeError("db down")
        reader, writer = await self.login()
        writer.write(tracker("help me"))
        await writer.drain()
        await self.read(reader)
        await self.until(lambda: any(
            c.kwargs.get("event_type") == "SOS" for c in self.mocks["event_g103"].await_args_list
        ))


class Gps103SecurityTests(ServerTestCase):
    async def test_unknown_imei_is_rejected_silently_and_disconnected(self):
        reader, writer = await self.connect()
        writer.write(b"##,imei:111111111111111,A;")
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")                # closed, no LOAD
        self.assertIsNone(device_registry.get_device("111111111111111"))

    async def test_data_before_login_is_rejected(self):
        reader, writer = await self.connect()
        writer.write(tracker())
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")
        self.mocks["track_g103"].assert_not_awaited()

    async def test_imei_swap_mid_connection_drops_the_socket(self):
        reader, writer = await self.login()
        writer.write(tracker(imei=OTHER_IMEI))
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")
        self.mocks["track_g103"].assert_not_awaited()

    async def test_heartbeat_for_a_different_imei_drops_the_socket(self):
        reader, writer = await self.login()
        writer.write(f"{OTHER_IMEI};".encode())
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")

    async def test_unrecognised_protocol_is_closed_immediately(self):
        reader, writer = await self.connect()
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")

    async def test_unterminated_flood_drops_the_connection_and_cleans_up(self):
        reader, writer = await self.login()
        writer.write(b"imei:" + b"A" * 5000)                          # never sends ';'
        await writer.drain()
        self.assertEqual(await self.read(reader), b"")                # server closed us
        await self.until(lambda: self.mocks["offline"].await_count == 1)
        self.assertIsNone(device_registry.get_device(KNOWN_IMEI))

    async def test_commands_are_refused_for_gps103_devices(self):
        _, writer = await self.login()
        with self.assertRaises(UnsupportedCommandError):
            await send_command(KNOWN_IMEI, b"\x78\x78\x00")


class ConnectionLifecycleTests(ServerTestCase):
    async def test_disconnect_marks_offline_and_clears_registry(self):
        _, writer = await self.login()
        writer.close()
        await self.until(lambda: self.mocks["offline"].await_count == 1)
        self.assertIsNone(device_registry.get_device(KNOWN_IMEI))

    async def test_stale_connection_closing_does_not_knock_a_reconnected_device_offline(self):
        _, old_writer = await self.login()
        _, new_writer = await self.login()                            # same IMEI, new socket
        old_writer.close()
        await asyncio.sleep(0.3)
        self.mocks["offline"].assert_not_awaited()
        self.assertIsNotNone(device_registry.get_device(KNOWN_IMEI))
        new_writer.close()
        await self.until(lambda: self.mocks["offline"].await_count == 1)


class Gt06RegressionTests(ServerTestCase):
    """The V5 path must behave exactly as before the multi-protocol change."""

    async def test_v5_login_ack_and_location_still_work(self):
        reader, writer = await self.connect()
        writer.write(gt06_login(GT06_IMEI))
        await writer.drain()
        ack = await self.read(reader)
        self.assertEqual(ack[:2], b"\x78\x78")
        self.assertEqual(ack[3], 0x01)
        self.assertEqual(device_registry.get_device(GT06_IMEI)["protocol"], "GT06")

        writer.write(gt06_location())
        await writer.drain()
        ack2 = await self.read(reader)
        self.assertEqual(ack2[3], 0x12)
        await self.until(lambda: self.mocks["track_gt06"].await_count == 1)
        kwargs = self.mocks["track_gt06"].call_args.kwargs
        self.assertAlmostEqual(kwargs["latitude"], 6.9271, places=4)

    async def test_gt06_and_gps103_devices_coexist_on_one_port(self):
        _, g103 = await self.login()
        reader, gt06 = await self.connect()
        gt06.write(gt06_login(GT06_IMEI))
        await gt06.drain()
        await self.read(reader)
        self.assertEqual(device_registry.get_device(KNOWN_IMEI)["protocol"], "GPS103")
        self.assertEqual(device_registry.get_device(GT06_IMEI)["protocol"], "GT06")


if __name__ == "__main__":
    unittest.main()
