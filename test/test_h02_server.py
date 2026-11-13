"""
End-to-end tests for the H02 pipeline (LK-GPS LK208 and the "*HQ,<id>,V1,...#"
family).

Runs the REAL tcp_server.handle_device on a localhost port and talks to it over
real sockets, with H02 enabled via config.ENABLED_PROTOCOLS. Only the database
layer is mocked, exactly like test_multiprotocol_server.py, which provides the
harness.

    python -m unittest discover -s test -p "test_h02_server.py" -v
"""

import asyncio
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                   # noqa: E402
from constants.severity import Severity                        # noqa: E402
from services import device_registry, h02_handler               # noqa: E402
from services.command_service import send_command, UnsupportedCommandError  # noqa: E402
from test_multiprotocol_server import ServerTestCase, DEVICE_ID, VEHICLE_ID  # noqa: E402

IMEI = "353588020068342"            # 15-digit IMEI, as the ERP expects
SHORT_ID = "4106000826"             # 10-digit id some units report
UNKNOWN_IMEI = "999999999999999"
UNKNOWN_SHORT_ID = "9999999999"

# Real binary capture (device 4106000826, Bogota, 2015-08-31T21:53:21Z).
BIN_BOGOTA = bytes.fromhex(
    "24410600082621532131081504419390060740418306000000fffffbfdff0015060000002c02dc0c000000001f")

IDLE = "FFFFFBFF"                   # no alarm, ignition off
IGNITION_ON = "FFFFFFFF"            # bit 10 set
SOS = "FFFFFBFD"                    # bit 1 cleared


def stamp(seconds_ago: float = 30):
    """(hhmmss, ddmmyy) of a moment `seconds_ago` before now, UTC."""
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return t.strftime("%H%M%S"), t.strftime("%d%m%y")


def v1(device=IMEI, seconds_ago=30, valid="A", lat="0656.2000", ns="N", lon="07951.2000", ew="E",
       speed="10.00", course="090", status=IDLE, mtype="V1"):
    hhmmss, ddmmyy = stamp(seconds_ago)
    return (f"*HQ,{device},{mtype},{hhmmss},{valid},{lat},{ns},{lon},{ew},{speed},{course},"
            f"{ddmmyy},{status}#").encode()


def htbt(device=IMEI):
    return f"*HQ,{device},HTBT,100#".encode()


ACK = re.compile(rb"^\*HQ,(\d+),V4,(V1|NBR),(\d{14})#$")


class H02ServerTests(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        h02_handler._last_alive.clear()
        h02_handler._last_ignition.clear()
        h02_handler._last_alarm.clear()

        async def lookup(imei, **kwargs):
            return DEVICE_ID if imei in (IMEI, SHORT_ID, "359586015829802") else None

        self.mocks["lookup"].side_effect = lookup

        for name, target, extra in (
            ("save", "services.h02_handler.save_location", {"new_callable": AsyncMock, "return_value": True}),
            ("event", "services.h02_handler.create_event", {"new_callable": AsyncMock}),
            ("alive_h", "services.h02_handler.mark_device_alive", {"new_callable": AsyncMock}),
            ("ignition_h", "services.h02_handler.set_ignition", {"new_callable": AsyncMock}),
            # Only forced saves (first message, unknown type) reach RawPackets in these tests.
            ("policy", "services.h02_handler.should_store_raw", {"side_effect": lambda always=False: always}),
            ("log_h", "services.h02_handler.log", {}),
        ):
            patcher = patch(target, **extra)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        enabled = patch.object(config, "ENABLED_PROTOCOLS", frozenset({"GT06", "GPS103", "H02"}))
        enabled.start()
        self.addCleanup(enabled.stop)

    async def send(self, writer, frame):
        writer.write(frame)
        await writer.drain()

    async def assert_silent(self, reader, wait=0.3):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1024), wait)

    async def login_ok(self, device=IMEI):
        """Connect with a V1 position report; return after the acknowledgement."""
        reader, writer = await self.connect()
        await self.send(writer, v1(device))
        self.assertRegex(await self.read(reader), ACK)
        return reader, writer

    def logged(self):
        return " ".join(str(c.args[0]) for c in self.mocks["log_h"].call_args_list)

    # -- first message = login ----------------------------------------------

    async def test_first_message_authenticates_registers_and_is_acknowledged(self):
        reader, writer = await self.connect()
        await self.send(writer, v1())
        reply = await self.read(reader)
        match = ACK.match(reply)
        self.assertIsNotNone(match, reply)
        self.assertEqual((match.group(1), match.group(2)), (IMEI.encode(), b"V1"))
        # the acknowledgement carries the SERVER clock, UTC, within a few seconds
        sent = datetime.strptime(match.group(3).decode(), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - sent).total_seconds()), 5)

        entry = device_registry.get_device(IMEI)
        self.assertEqual(entry["protocol"], "H02")
        self.assertEqual(entry["device_id"], DEVICE_ID)
        kwargs = self.mocks["lookup"].call_args.kwargs
        self.assertEqual((kwargs["protocol_type"], kwargs["default_model"]), ("H02", "H02"))

        await self.until(lambda: self.mocks["event"].await_count == 1)
        self.assertEqual(self.mocks["event"].call_args.kwargs["event_type"], "DEVICE_ONLINE")
        await self.until(lambda: self.mocks["raw"].await_count == 1)         # first message is always kept
        self.assertEqual(self.mocks["raw"].call_args.kwargs["protocol_number"], "H02")

    async def test_the_first_message_position_is_stored_too(self):
        await self.login_ok()
        await self.until(lambda: self.mocks["save"].await_count == 1)

    async def test_unknown_id_is_closed_without_a_reply(self):
        reader, writer = await self.connect()
        await self.send(writer, v1(UNKNOWN_IMEI))
        self.assertEqual(await self.read(reader), b"")                        # closed, nothing sent
        self.assertIsNone(device_registry.get_device(UNKNOWN_IMEI))
        self.mocks["save"].assert_not_awaited()

    async def test_unknown_non_imei_id_is_closed_and_the_log_explains_why(self):
        reader, writer = await self.connect()
        await self.send(writer, v1(UNKNOWN_SHORT_ID))
        self.assertEqual(await self.read(reader), b"")
        self.assertIn("10-digit id", self.logged())

    async def test_a_rejected_text_login_logs_the_whole_first_frame(self):
        reader, writer = await self.connect()
        frame = v1(UNKNOWN_SHORT_ID, status=SOS)
        await self.send(writer, frame)
        self.assertEqual(await self.read(reader), b"")
        line = next(str(c.args[0]) for c in self.mocks["log_h"].call_args_list if "REJECTED" in str(c.args[0]))
        self.assertIn(UNKNOWN_SHORT_ID, line)
        self.assertIn("first frame (text, V1)", line)
        self.assertIn(SOS, line)                                   # status word visible in one attempt
        self.assertNotIn("\n", line)

    async def test_a_rejected_binary_login_logs_the_first_frame_as_hex(self):
        async def reject(imei, **kwargs):
            return None

        self.mocks["lookup"].side_effect = reject
        reader, writer = await self.connect()
        await self.send(writer, BIN_BOGOTA)
        self.assertEqual(await self.read(reader), b"")
        line = next(str(c.args[0]) for c in self.mocks["log_h"].call_args_list if "REJECTED" in str(c.args[0]))
        self.assertIn("first frame (binary, BIN)", line)
        self.assertIn(BIN_BOGOTA[:20].hex(), line)

    async def test_a_known_non_imei_id_is_accepted(self):
        # Whether the ERP can hold such an id is a database matter (docs/h02.md);
        # when it can, the gateway serves it.
        await self.login_ok(SHORT_ID)
        self.assertEqual(device_registry.get_device(SHORT_ID)["protocol"], "H02")

    async def test_family_that_is_not_enabled_is_treated_as_unknown(self):
        with patch.object(config, "ENABLED_PROTOCOLS", frozenset({"GT06", "GPS103"})):
            reader, writer = await self.connect()
            await self.send(writer, v1())
            self.assertEqual(await self.read(reader), b"")
        self.assertIsNone(device_registry.get_device(IMEI))
        self.mocks["lookup"].assert_not_called()

    async def test_unparseable_first_message_closes_the_connection(self):
        reader, writer = await self.connect()
        await self.send(writer, b"*HQ,abc,V1,x#")
        self.assertEqual(await self.read(reader), b"")
        self.mocks["lookup"].assert_not_called()

    async def test_login_survives_fragmented_delivery(self):
        reader, writer = await self.connect()
        stream = v1()
        for i in range(0, len(stream), 5):
            await self.send(writer, stream[i:i + 5])
            await asyncio.sleep(0.01)
        self.assertRegex(await self.read(reader), ACK)

    # -- messages after login -----------------------------------------------

    async def test_heartbeat_is_echoed_and_refreshes_last_seen(self):
        reader, writer = await self.login_ok()
        await self.send(writer, htbt())
        self.assertEqual(await self.read(reader), f"*HQ,{IMEI},HTBT#".encode())
        heartbeats = [c for c in self.mocks["alive_h"].await_args_list if c.kwargs.get("heartbeat")]
        self.assertEqual(len(heartbeats), 1)
        self.mocks["save"].assert_awaited_once()                              # no extra position from a heartbeat

    async def test_position_is_stored_with_speed_converted_from_knots(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(lat="0956.3869", ns="N", lon="08406.7068", ew="W", speed="10.00", course="123"))
        self.assertRegex(await self.read(reader), ACK)
        await self.until(lambda: self.mocks["save"].await_count == 1)
        saved = self.mocks["save"].call_args.kwargs
        self.assertEqual(saved["device_id"], DEVICE_ID)
        self.assertAlmostEqual(saved["latitude"], 9.93978, places=5)
        self.assertAlmostEqual(saved["longitude"], -84.11178, places=5)
        self.assertAlmostEqual(saved["speed"], 18.52, places=2)               # 10 kn
        self.assertEqual(saved["heading"], 123)
        self.assertEqual(saved["satellites"], 0)
        self.assertIs(saved["event_time"].tzinfo, timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - saved["event_time"]).total_seconds()), 60)

    async def test_speed_is_taken_as_kmh_when_configured(self):
        from parsers import h02_parser
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        with patch.object(h02_parser, "H02_SPEED_IN_KMH", True):
            await self.send(writer, v1(speed="10.00"))
            await self.read(reader)
        self.assertAlmostEqual(self.mocks["save"].call_args.kwargs["speed"], 10.0, places=2)

    async def test_no_fix_report_is_acknowledged_but_not_stored(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(valid="V"))
        self.assertRegex(await self.read(reader), ACK)                         # ack or the unit resends forever
        self.mocks["save"].assert_not_awaited()

    async def test_zero_zero_position_is_not_stored(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(lat="0000.0000", lon="00000.0000"))
        await self.read(reader)
        self.mocks["save"].assert_not_awaited()

    async def test_impossible_speed_is_not_stored(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(speed="400.00"))                            # 740 km/h
        await self.read(reader)
        self.mocks["save"].assert_not_awaited()

    async def test_future_timestamp_is_not_stored(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(seconds_ago=-3600))                         # one hour ahead
        await self.read(reader)
        self.mocks["save"].assert_not_awaited()

    async def test_database_failure_does_not_drop_the_connection(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].side_effect = RuntimeError("db down")
        await self.send(writer, v1())
        self.assertRegex(await self.read(reader), ACK)                         # still answered
        await self.send(writer, htbt())
        self.assertEqual(await self.read(reader), f"*HQ,{IMEI},HTBT#".encode())

    async def test_unparseable_message_after_login_keeps_the_connection(self):
        reader, writer = await self.login_ok()
        await self.send(writer, b"*HQ,abc,V1,x#")
        await self.assert_silent(reader)
        await self.send(writer, htbt())
        self.assertEqual(await self.read(reader), f"*HQ,{IMEI},HTBT#".encode())

    async def test_two_frames_in_one_segment_are_both_handled(self):
        reader, writer = await self.login_ok()
        await self.send(writer, htbt() + v1() + b"\r\n")
        data = b""
        while data.count(b"#") < 2:
            data += await self.read(reader)
        self.assertTrue(data.startswith(f"*HQ,{IMEI},HTBT#".encode()))
        self.assertRegex(data.split(b"#", 1)[1], ACK)

    async def test_unknown_message_type_is_kept_raw_and_not_answered(self):
        reader, writer = await self.login_ok()
        before = self.mocks["raw"].await_count
        await self.send(writer, f"*HQ,{IMEI},ZZ9,1,2#".encode())
        await self.assert_silent(reader)
        await self.until(lambda: self.mocks["raw"].await_count == before + 1)
        self.assertIs(self.mocks["raw"].call_args.kwargs["parsed"], False)

    async def test_cell_tower_report_is_alive_only(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, f"*HQ,{IMEI},V3,095426,74001,01,010278,045142,128,-92,02DE,0,X,090517,FFFFFBFF#".encode())
        await self.assert_silent(reader)
        self.mocks["save"].assert_not_awaited()

    async def test_id_switch_after_login_is_treated_as_spoofing(self):
        reader, writer = await self.login_ok()
        self.mocks["save"].reset_mock()
        await self.send(writer, v1(SHORT_ID))                                  # a DIFFERENT, valid id
        self.assertEqual(await self.read(reader), b"")                         # dropped, no ack
        self.mocks["save"].assert_not_awaited()
        await self.until(lambda: device_registry.get_device(IMEI) is None)     # registration torn down
        self.assertIn("MISMATCH", self.logged())

    async def test_last_seen_refresh_is_rate_limited(self):
        reader, writer = await self.login_ok()
        alive_after_login = self.mocks["alive_h"].await_count
        for _ in range(3):
            await self.send(writer, v1())
            await self.read(reader)
        self.assertEqual(self.mocks["alive_h"].await_count, alive_after_login)   # 3 positions, 0 extra writes

    async def test_disconnect_marks_the_device_offline(self):
        reader, writer = await self.login_ok()
        writer.close()
        await self.until(lambda: self.mocks["offline"].await_count == 1)
        self.assertIsNone(device_registry.get_device(IMEI))

    # -- binary form -----------------------------------------------------------

    async def test_binary_first_message_logs_in_and_stores_the_position(self):
        reader, writer = await self.connect()
        await self.send(writer, BIN_BOGOTA)
        await self.until(lambda: self.mocks["save"].await_count == 1)
        saved = self.mocks["save"].call_args.kwargs
        self.assertAlmostEqual(saved["latitude"], 4.69898, places=5)
        self.assertAlmostEqual(saved["longitude"], -74.06972, places=5)
        self.assertEqual(saved["event_time"], datetime(2015, 8, 31, 21, 53, 21, tzinfo=timezone.utc))
        self.assertEqual(device_registry.get_device(SHORT_ID)["protocol"], "H02")
        await self.assert_silent(reader)                                       # binary frames are not acknowledged

    # -- status word (off by default) --------------------------------------------

    async def test_status_flags_are_ignored_by_default(self):
        self.assertFalse(config.H02_STATUS_FLAGS)
        reader, writer = await self.login_ok()
        await self.send(writer, v1(status=SOS))
        await self.read(reader)
        await self.send(writer, v1(status=IGNITION_ON))
        await self.read(reader)
        self.mocks["ignition_h"].assert_not_awaited()
        self.assertEqual([c.kwargs["event_type"] for c in self.mocks["event"].await_args_list],
                         ["DEVICE_ONLINE"])

    async def test_ignition_is_written_once_per_change_when_enabled(self):
        with patch.object(config, "H02_STATUS_FLAGS", True):
            reader, writer = await self.login_ok()                              # IDLE -> ignition off
            for status in (IGNITION_ON, IGNITION_ON, IDLE):
                await self.send(writer, v1(status=status))
                await self.read(reader)
        values = [c.args for c in self.mocks["ignition_h"].await_args_list]
        self.assertEqual(values, [(DEVICE_ID, False), (DEVICE_ID, True), (DEVICE_ID, False)])

    async def test_alarm_event_is_recorded_once_per_transition_when_enabled(self):
        with patch.object(config, "H02_STATUS_FLAGS", True):
            reader, writer = await self.login_ok()
            for status in (SOS, SOS, IDLE, SOS):
                await self.send(writer, v1(status=status))
                await self.read(reader)
        alarms = [c.kwargs for c in self.mocks["event"].await_args_list if c.kwargs["event_type"] == "SOS"]
        self.assertEqual(len(alarms), 2)                                        # not 3: the repeat is not a new alarm
        self.assertEqual(alarms[0]["severity"], Severity.HIGH)
        self.assertEqual(alarms[0]["vehicle_id"], VEHICLE_ID)
        self.assertAlmostEqual(alarms[0]["latitude"], 6.9366, places=3)
        self.assertEqual(alarms[0]["metadata"]["protocol"], "H02")
        self.assertEqual(alarms[0]["metadata"]["status"], SOS)

    async def test_alarm_without_a_fix_has_no_coordinates(self):
        with patch.object(config, "H02_STATUS_FLAGS", True):
            reader, writer = await self.login_ok()
            await self.send(writer, v1(valid="V", status=SOS))
            await self.read(reader)
        alarm = [c.kwargs for c in self.mocks["event"].await_args_list if c.kwargs["event_type"] == "SOS"][0]
        self.assertIsNone(alarm["latitude"])
        self.assertIs(alarm["metadata"]["gps_fix"], False)

    # -- isolation --------------------------------------------------------------

    async def test_commands_to_an_h02_device_are_refused_not_written(self):
        reader, writer = await self.login_ok()
        with self.assertRaises(UnsupportedCommandError):
            await send_command(IMEI, b"\x78\x78\x00")
        await self.assert_silent(reader)                                        # nothing reached the device

    async def test_existing_families_still_work_on_the_same_port(self):
        reader, writer = await self.connect()
        await self.send(writer, b"##,imei:359586015829802,A;")
        self.assertEqual(await self.read(reader), b"LOAD")
        self.assertEqual(device_registry.get_device("359586015829802")["protocol"], "GPS103")

    async def test_http_junk_is_still_rejected(self):
        reader, writer = await self.connect()
        await self.send(writer, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(await self.read(reader), b"")
        self.mocks["lookup"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
