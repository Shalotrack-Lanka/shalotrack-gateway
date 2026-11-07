"""
Tests for outbound commands to GPS103 (TK103) devices.

Three layers:
  * the frame builder (pure),
  * the command service against a fake socket,
  * the whole path: real HTTP Command API -> device registry -> the real TCP
    server's socket to a connected tracker, with only the database mocked.

    python -m unittest discover -s test -p "test_gps103_commands.py" -v
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_KEY = "test-shared-secret-do-not-use-in-prod"
os.environ.setdefault("COMMAND_API_KEY", TEST_KEY)    # read once at import time

import command_api                                                  # noqa: E402
from services import device_registry                                # noqa: E402
from services.command_service import UnsupportedCommandError        # noqa: E402
from services.gps103_command_service import send_gps103_command, supported_commands  # noqa: E402
from utils import gps103_command_builder as b                       # noqa: E402
from test_multiprotocol_server import (                             # noqa: E402
    ServerTestCase, KNOWN_IMEI, GT06_IMEI, LOGIN, gt06_login,
)

IMEI = KNOWN_IMEI   # the GPS103 IMEI the shared harness already authorises


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------

class BuilderTests(unittest.TestCase):
    def test_exact_frames(self):
        self.assertEqual(b.build_single_position(IMEI), b"**,imei:359586015829802,B;")
        self.assertEqual(b.build_engine_resume(IMEI), b"**,imei:359586015829802,K;")

    def test_frames_are_pure_ascii_text_not_gt06_binary(self):
        for frame in (b.build_single_position(IMEI), b.build_engine_resume(IMEI)):
            self.assertFalse(frame.startswith(b"\x78\x78"))
            frame.decode("ascii")

    def test_imei_is_validated_so_nothing_can_be_injected(self):
        for bad in ("", "123", "35958601582980;K", "35958601582980\n", "3595860158298xx",
                    "٣٥٩٥٨٦٠١٥٨٢٩٨٠٢", "1" * 40, None, 359586015829802):
            with self.assertRaises(ValueError, msg=repr(bad)):
                b.build_single_position(bad)

    def test_no_engine_cut_builder_exists(self):
        self.assertFalse(hasattr(b, "build_engine_stop"))


# ---------------------------------------------------------------------------
# service, fake socket
# ---------------------------------------------------------------------------

class FakeWriter:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        device_registry._by_imei.clear()
        device_registry._by_socket.clear()
        self.writer = FakeWriter()

    def register(self, protocol="GPS103"):
        device_registry.register_device(IMEI, "10.0.0.1", "dev-1", self.writer, protocol=protocol)

    async def test_supported_commands_are_sent_as_text(self):
        self.register()
        for command, expected in (("where", b"B;"), ("position", b"B;"), ("url", b"B;"), ("relay_on", b"K;")):
            self.writer.sent.clear()
            self.assertTrue(await send_gps103_command(IMEI, command))
            self.assertEqual(self.writer.sent, [b"**,imei:359586015829802," + expected])

    async def test_other_commands_explain_that_they_are_sms_only(self):
        self.register()
        for command in ("status", "version", "params", "reset", "timer", "apn", "server", "sos_delete"):
            with self.assertRaises(UnsupportedCommandError) as ctx:
                await send_gps103_command(IMEI, command)
            self.assertIn(command, str(ctx.exception))
            self.assertIn("SMS", str(ctx.exception))
        self.assertEqual(self.writer.sent, [])           # nothing was written to the device

    async def test_engine_cut_is_refused_with_a_safety_message(self):
        self.register()
        with self.assertRaises(UnsupportedCommandError) as ctx:
            await send_gps103_command(IMEI, "relay_off")
        self.assertIn("safety", str(ctx.exception))
        self.assertEqual(self.writer.sent, [])

    async def test_offline_device(self):
        with self.assertRaises(Exception) as ctx:
            await send_gps103_command(IMEI, "where")
        self.assertIn("offline", str(ctx.exception).lower())

    async def test_never_writes_gps103_text_to_another_family(self):
        self.register(protocol="GT06")
        with self.assertRaises(UnsupportedCommandError):
            await send_gps103_command(IMEI, "where")
        self.assertEqual(self.writer.sent, [])

    def test_supported_list(self):
        self.assertEqual(supported_commands(), ["position", "relay_on", "url", "where"])


# ---------------------------------------------------------------------------
# whole path
# ---------------------------------------------------------------------------

class CommandPathTests(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        key = patch.object(command_api, "COMMAND_API_KEY", TEST_KEY)
        key.start()
        self.addCleanup(key.stop)

        loop = patch.object(command_api, "_loop", asyncio.get_running_loop())
        loop.start()
        self.addCleanup(loop.stop)

        self.http = command_api.HTTPServer(("127.0.0.1", 0), command_api.CommandAPIHandler)
        self.http_port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

        def stop_http():
            self.http.shutdown()
            self.http.server_close()
        self.addCleanup(stop_http)

    def _post_blocking(self, body, key=TEST_KEY):
        conn = HTTPConnection("127.0.0.1", self.http_port, timeout=8)
        conn.request("POST", "/command", body=json.dumps(body).encode(),
                     headers={"X-Command-Api-Key": key, "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        conn.close()
        return resp.status, data

    async def post(self, imei, command, params=None):
        # Off the event loop: the API hands the send back to this loop and waits.
        return await asyncio.to_thread(self._post_blocking, {"imei": imei, "command": command, "params": params or {}})

    async def assert_silent(self, reader, wait=0.3):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1024), wait)

    # -- GPS103 ---------------------------------------------------------

    async def test_where_reaches_the_tracker_as_a_text_command(self):
        reader, _ = await self.login()
        status, body = await self.post(IMEI, "where")
        self.assertEqual(status, 200, body)
        self.assertEqual(await self.read(reader), b"**,imei:359586015829802,B;")

    async def test_relay_on_reaches_the_tracker(self):
        reader, _ = await self.login()
        status, _ = await self.post(IMEI, "relay_on")
        self.assertEqual(status, 200)
        self.assertEqual(await self.read(reader), b"**,imei:359586015829802,K;")

    async def test_sms_only_command_returns_501_with_a_specific_message(self):
        reader, _ = await self.login()
        status, body = await self.post(IMEI, "status")
        self.assertEqual(status, 501)
        self.assertIn("SMS", body["error"])
        self.assertNotIn("not supported yet", body["error"])
        await self.assert_silent(reader)

    async def test_engine_cut_is_refused_and_nothing_is_sent(self):
        reader, _ = await self.login()
        status, body = await self.post(IMEI, "relay_off")
        self.assertEqual(status, 501)
        self.assertIn("safety", body["error"])
        await self.assert_silent(reader)

    async def test_offline_device_is_503(self):
        status, _ = await self.post(IMEI, "where")
        self.assertEqual(status, 503)

    async def test_unknown_command_is_400_for_every_family(self):
        await self.login()
        status, body = await self.post(IMEI, "format_disk")
        self.assertEqual(status, 400)
        self.assertIn("Unknown command", body["error"])

    async def test_a_hand_made_imei_cannot_reach_a_device(self):
        reader, _ = await self.login()
        status, _ = await self.post(IMEI + ";K", "where")
        self.assertEqual(status, 503)
        await self.assert_silent(reader)

    async def test_missing_key_is_still_401(self):
        await self.login()
        status, _ = await asyncio.to_thread(self._post_blocking, {"imei": IMEI, "command": "where"}, "wrong")
        self.assertEqual(status, 401)

    # -- regression: GT06 / V5 unchanged ---------------------------------

    async def test_gt06_where_is_still_a_binary_frame(self):
        reader, writer = await self.connect()
        writer.write(gt06_login(GT06_IMEI))
        await writer.drain()
        await self.read(reader)                               # login ACK

        status, _ = await self.post(GT06_IMEI, "where")
        self.assertEqual(status, 200)
        frame = await self.read(reader)
        self.assertEqual(frame[:2], b"\x78\x78")
        self.assertEqual(frame[3], 0x80)                      # protocol 0x80 command packet
        self.assertIn(b"WHERE#", frame)


if __name__ == "__main__":
    unittest.main()
