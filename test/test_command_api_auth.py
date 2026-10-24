"""
Tests that the Command API refuses every request without the shared secret,
and accepts one presenting it. Starts the REAL HTTPServer on a loopback port
and talks to it over real HTTP — only the device-command functions underneath
are mocked, so the auth check in command_api.py runs for real.

    python -m unittest discover -s test -p "test_command_api_auth*.py" -v
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_KEY = "test-shared-secret-do-not-use-in-prod"
os.environ["COMMAND_API_KEY"] = TEST_KEY  # must be set before command_api is imported

import command_api  # noqa: E402


class CommandApiAuthTests(unittest.TestCase):
    """Starts a real HTTPServer bound to a free loopback port for each test."""

    def setUp(self):
        # command_api reads COMMAND_API_KEY once at import time into a module
        # global; make sure it still matches TEST_KEY even if an earlier test
        # file in the same run imported/changed it first.
        command_api.COMMAND_API_KEY = TEST_KEY

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        command_api._loop = self.loop

        self.server = command_api.HTTPServer(("127.0.0.1", 0), command_api.CommandAPIHandler)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)

    def _request(self, method, path, key=None, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if key is not None:
            headers["X-Command-Api-Key"] = key
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    # ---- GET /devices ----

    def test_get_without_key_is_rejected(self):
        status, _ = self._request("GET", "/devices")
        self.assertEqual(status, 401)

    def test_get_with_wrong_key_is_rejected(self):
        status, _ = self._request("GET", "/devices", key="wrong-key")
        self.assertEqual(status, 401)

    def test_get_with_correct_key_is_accepted(self):
        status, body = self._request("GET", "/devices", key=TEST_KEY)
        self.assertEqual(status, 200)
        self.assertIn("connected_devices", body)

    def test_health_also_requires_the_key(self):
        # No AWS load balancer health-checks this HTTP port (the NLB's health
        # check is a raw TCP check against the device-ingestion port, 8000),
        # so there is no reason for /health to be the one open door.
        status, _ = self._request("GET", "/health")
        self.assertEqual(status, 401)

    # ---- POST /command ----

    def test_post_command_without_key_is_rejected_before_touching_the_device(self):
        with patch("command_api.send_where", new_callable=AsyncMock) as mock_send:
            status, _ = self._request(
                "POST", "/command", body={"imei": "123", "command": "where"}
            )
            self.assertEqual(status, 401)
            mock_send.assert_not_called()

    def test_post_relay_off_without_key_is_rejected(self):
        # relay_off is the single most dangerous command this API exposes
        # (cuts a vehicle's engine relay) — confirm the auth gate covers it
        # specifically, not just the simpler read-only commands, and that it
        # is rejected before SIMPLE_COMMANDS["relay_off"] is ever looked up.
        with patch.dict(command_api.SIMPLE_COMMANDS, {"relay_off": AsyncMock()}):
            mock_relay_off = command_api.SIMPLE_COMMANDS["relay_off"]
            status, _ = self._request(
                "POST", "/command", body={"imei": "123", "command": "relay_off"}
            )
            self.assertEqual(status, 401)
            mock_relay_off.assert_not_called()

    def test_post_command_with_correct_key_is_accepted(self):
        with patch("command_api.SIMPLE_COMMANDS", {"where": AsyncMock()}):
            status, body = self._request(
                "POST", "/command", key=TEST_KEY, body={"imei": "123456789012345", "command": "where"}
            )
            self.assertEqual(status, 200)
            self.assertTrue(body.get("success"))

    # ---- Empty/missing key at startup must fail closed ----

    def test_authorized_helper_denies_everything_if_key_is_empty(self):
        old = command_api.COMMAND_API_KEY
        try:
            command_api.COMMAND_API_KEY = ""
            handler = command_api.CommandAPIHandler.__new__(command_api.CommandAPIHandler)
            handler.headers = {"X-Command-Api-Key": ""}
            self.assertFalse(handler._authorized())
            # Even a client that (somehow) sends an empty header must not
            # match an empty expected key.
            handler.headers = {"X-Command-Api-Key": "anything"}
            self.assertFalse(handler._authorized())
        finally:
            command_api.COMMAND_API_KEY = old


if __name__ == "__main__":
    unittest.main()