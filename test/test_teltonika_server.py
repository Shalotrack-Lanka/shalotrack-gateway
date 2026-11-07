"""
End-to-end tests for the Teltonika pipeline.

Runs the REAL tcp_server.handle_device on a localhost port and talks to it
over real sockets, with Teltonika enabled via config.ENABLED_PROTOCOLS. Only
the database layer is mocked, exactly like test_multiprotocol_server.py, which
provides the harness.

    python -m unittest discover -s test -p "test_teltonika_server.py" -v
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                   # noqa: E402
from services import device_registry, teltonika_handler         # noqa: E402
from test_multiprotocol_server import ServerTestCase, DEVICE_ID, VEHICLE_ID  # noqa: E402
from teltonika_fixtures import build_avl_packet, encode_record, login_frame  # noqa: E402

IMEI = "352093081452251"
UNKNOWN_IMEI = "999999999999999"


def ms(seconds_ago: float) -> int:
    return int((datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).timestamp() * 1000)


def fix(seconds_ago=30, lat=6.9271, lon=79.8612, speed=42, angle=169, sats=9, **io):
    return encode_record(ms(seconds_ago), lat=lat, lon=lon, speed=speed, angle=angle, satellites=sats, **io)


def no_fix(seconds_ago=30):
    return encode_record(ms(seconds_ago), lat=0, lon=0, satellites=0)


class TeltonikaServerTests(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        teltonika_handler._last_alive.clear()
        teltonika_handler._last_ignition.clear()

        async def lookup(imei, **kwargs):
            return DEVICE_ID if imei in (IMEI, "359586015829802") else None

        self.mocks["lookup"].side_effect = lookup

        for name, target, extra in (
            ("save", "services.teltonika_handler.save_location", {"new_callable": AsyncMock, "return_value": True}),
            ("event", "services.teltonika_handler.create_event", {"new_callable": AsyncMock}),
            ("alive_t", "services.teltonika_handler.mark_device_alive", {"new_callable": AsyncMock}),
            ("ignition_t", "services.teltonika_handler.set_ignition", {"new_callable": AsyncMock}),
            # Only forced saves (login, undecodable) reach RawPackets in these tests.
            ("policy", "services.teltonika_handler.should_store_raw", {"side_effect": lambda always=False: always}),
        ):
            patcher = patch(target, **extra)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        enabled = patch.object(config, "ENABLED_PROTOCOLS", frozenset({"GT06", "GPS103", "TELTONIKA"}))
        enabled.start()
        self.addCleanup(enabled.stop)

    async def login_ok(self):
        reader, writer = await self.connect()
        writer.write(login_frame(IMEI))
        await writer.drain()
        self.assertEqual(await self.read(reader), b"\x01")
        return reader, writer

    async def send(self, writer, frame):
        writer.write(frame)
        await writer.drain()

    async def assert_silent(self, reader, wait=0.3):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1024), wait)

    # -- login ---------------------------------------------------------

    async def test_login_registers_device_and_replies_01(self):
        await self.login_ok()
        entry = device_registry.get_device(IMEI)
        self.assertEqual(entry["protocol"], "TELTONIKA")
        self.assertEqual(entry["device_id"], DEVICE_ID)
        self.assertEqual(self.mocks["lookup"].call_args.kwargs["protocol_type"], "TELTONIKA")
        self.assertEqual(self.mocks["lookup"].call_args.kwargs["default_model"], "FMB")
        await self.until(lambda: self.mocks["event"].await_count == 1)
        self.assertEqual(self.mocks["event"].call_args.kwargs["event_type"], "DEVICE_ONLINE")
        # the login frame is always kept in RawPackets
        await self.until(lambda: self.mocks["raw"].await_count == 1)
        self.assertEqual(self.mocks["raw"].call_args.kwargs["protocol_number"], "TELTONIKA")

    async def test_unknown_imei_is_closed_without_a_reply(self):
        reader, writer = await self.connect()
        await self.send(writer, login_frame(UNKNOWN_IMEI))
        self.assertEqual(await self.read(reader), b"")        # closed, nothing sent
        self.assertIsNone(device_registry.get_device(UNKNOWN_IMEI))

    async def test_family_that_is_not_enabled_is_treated_as_unknown(self):
        with patch.object(config, "ENABLED_PROTOCOLS", frozenset({"GT06", "GPS103"})):
            reader, writer = await self.connect()
            await self.send(writer, login_frame(IMEI))
            self.assertEqual(await self.read(reader), b"")
        self.assertIsNone(device_registry.get_device(IMEI))
        self.mocks["lookup"].assert_not_called()

    async def test_data_before_login_closes_the_connection(self):
        reader, writer = await self.connect()
        # An AVL packet as the very first bytes is not even detected as Teltonika.
        await self.send(writer, build_avl_packet([fix()]))
        self.assertEqual(await self.read(reader), b"")
        self.mocks["save"].assert_not_awaited()

    async def test_login_and_data_survive_fragmented_delivery(self):
        reader, writer = await self.connect()
        stream = login_frame(IMEI)
        for i in range(0, len(stream), 3):
            await self.send(writer, stream[i:i + 3])
            await asyncio.sleep(0.01)
        self.assertEqual(await self.read(reader), b"\x01")

        packet = build_avl_packet([fix()])
        for i in range(0, len(packet), 7):
            await self.send(writer, packet[i:i + 7])
            await asyncio.sleep(0.01)
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")

    # -- data ----------------------------------------------------------

    async def test_two_records_are_saved_and_acknowledged_with_the_count(self):
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([fix(60, speed=10), fix(30, speed=20)]))
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x02")

        self.assertEqual(self.mocks["save"].await_count, 2)
        first, second = (c.kwargs for c in self.mocks["save"].await_args_list)
        self.assertEqual(first["device_id"], DEVICE_ID)
        self.assertAlmostEqual(first["latitude"], 6.9271, places=6)
        self.assertAlmostEqual(first["longitude"], 79.8612, places=6)
        self.assertEqual((first["speed"], second["speed"]), (10, 20))
        self.assertEqual(first["heading"], 169)
        self.assertEqual(first["satellites"], 9)
        self.assertLess(first["event_time"], second["event_time"])
        self.assertIs(first["event_time"].tzinfo, timezone.utc)
        self.assertNotIn("vehicle_id", first)

    async def test_records_without_a_fix_are_skipped_but_still_acknowledged(self):
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([no_fix(), fix(), no_fix()]))
        # Acknowledged, or the device would resend the same empty records forever.
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x03")
        self.assertEqual(self.mocks["save"].await_count, 1)

    async def test_future_timestamp_is_skipped_but_acknowledged(self):
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([fix(seconds_ago=-3600)]))     # one hour ahead
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")
        self.mocks["save"].assert_not_awaited()

    async def test_impossible_position_is_skipped_but_acknowledged(self):
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([fix(speed=900)]))
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")
        self.mocks["save"].assert_not_awaited()

    async def test_database_failure_acknowledges_only_what_was_stored(self):
        self.mocks["save"].side_effect = [True, RuntimeError("db down"), True]
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([fix(90), fix(60), fix(30)]))
        # Stops at the failed record: the device keeps records 2 and 3 and resends them.
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")
        self.assertEqual(self.mocks["save"].await_count, 2)

    async def test_bad_crc_is_not_acknowledged_and_stores_nothing(self):
        reader, writer = await self.login_ok()
        packet = bytearray(build_avl_packet([fix()]))
        packet[25] ^= 0xFF
        await self.send(writer, bytes(packet))
        await self.assert_silent(reader)
        self.mocks["save"].assert_not_awaited()

        # The connection is still usable: a good packet right after is accepted.
        await self.send(writer, build_avl_packet([fix()]))
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")

    async def test_unsupported_codec_keeps_raw_bytes_and_is_not_acknowledged(self):
        reader, writer = await self.login_ok()
        before = self.mocks["raw"].await_count
        await self.send(writer, build_avl_packet([fix()], codec=0x10))
        await self.assert_silent(reader)
        await self.until(lambda: self.mocks["raw"].await_count == before + 1)
        self.assertIs(self.mocks["raw"].call_args.kwargs["parsed"], False)
        self.mocks["save"].assert_not_awaited()

    async def test_codec8_extended_packet_is_handled(self):
        reader, writer = await self.login_ok()
        rec = encode_record(ms(20), lat=6.9271, lon=79.8612, satellites=8, speed=15,
                            io1={239: 1}, iox={0x0301: b"\x01\x02"}, extended=True)
        await self.send(writer, build_avl_packet([rec], codec=0x8E))
        self.assertEqual(await self.read(reader), b"\x00\x00\x00\x01")
        self.assertEqual(self.mocks["save"].await_count, 1)

    # -- status --------------------------------------------------------

    async def test_ignition_is_written_once_per_change_not_per_packet(self):
        reader, writer = await self.login_ok()
        await self.send(writer, build_avl_packet([fix(io1={239: 1})]))
        await self.read(reader)
        await self.send(writer, build_avl_packet([fix(io1={239: 1})]))
        await self.read(reader)
        self.assertEqual(self.mocks["ignition_t"].await_count, 1)
        self.assertEqual(self.mocks["ignition_t"].call_args.args, (DEVICE_ID, True))

        await self.send(writer, build_avl_packet([fix(io1={239: 0})]))
        await self.read(reader)
        self.assertEqual(self.mocks["ignition_t"].await_count, 2)
        self.assertEqual(self.mocks["ignition_t"].call_args.args, (DEVICE_ID, False))

    async def test_last_seen_refresh_is_rate_limited(self):
        reader, writer = await self.login_ok()
        login_calls = self.mocks["alive_t"].await_count          # login marks the device online once
        for _ in range(3):
            await self.send(writer, build_avl_packet([fix()]))   # no ignition IO -> only the heartbeat path
            await self.read(reader)
        heartbeats = [c for c in self.mocks["alive_t"].await_args_list if c.kwargs.get("heartbeat")]
        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(self.mocks["alive_t"].await_count, login_calls + 1)

    # -- isolation -----------------------------------------------------

    async def test_existing_families_still_work_on_the_same_port(self):
        reader, writer = await self.connect()
        await self.send(writer, b"##,imei:359586015829802,A;")
        self.assertEqual(await self.read(reader), b"LOAD")
        self.assertEqual(device_registry.get_device("359586015829802")["protocol"], "GPS103")


if __name__ == "__main__":
    unittest.main()
