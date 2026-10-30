"""
Tests for the deep /health report (services/health_service.py) and its wiring into the
Command API.

No database and no network. The DB probe is replaced; the event loop is a REAL loop running
on a helper thread, because the whole point of the check is crossing from the HTTP server
thread to the gateway's main loop -- and "the loop is stuck" is simulated by really blocking it.

    python -m unittest discover -s test -p "test_health_service*.py" -v
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_KEY = "test-shared-secret-do-not-use-in-prod"
os.environ["COMMAND_API_KEY"] = TEST_KEY  # must be set before command_api is imported

import command_api  # noqa: E402
from services import device_registry, health_service  # noqa: E402


async def _ping_ok():
    return None


class HealthTestBase(unittest.TestCase):
    """Real event loop on a helper thread + clean module state for every test."""

    def setUp(self):
        health_service._db_cache = None
        health_service._last_logged_state = None
        health_service._started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        device_registry._last_packet_at = None
        device_registry._by_imei.clear()
        device_registry._by_socket.clear()

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()

        self._patch(health_service, "_db_ping", _ping_ok)
        self._patch(health_service, "_pool_stats", lambda: None)

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=3)
        if not self.loop.is_running():
            self.loop.close()
        device_registry._by_imei.clear()
        device_registry._by_socket.clear()
        device_registry._last_packet_at = None

    def _patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _health(self):
        return health_service.get_health(self.loop)


class HealthReportTests(HealthTestBase):

    def test_all_green(self):
        device_registry.mark_packet_received()
        report = self._health()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["reasons"], [])
        self.assertTrue(report["checks"]["event_loop"]["ok"])
        self.assertTrue(report["checks"]["database"]["ok"])
        self.assertEqual(report["devices_connected"], 0)

    def test_report_leaks_no_device_identifiers(self):
        device_registry.register_device("123456789012345", "10.0.9.9", "dev-1", object())
        device_registry.mark_packet_received()
        blob = json.dumps(self._health())
        self.assertNotIn("123456789012345", blob)
        self.assertNotIn("10.0.9.9", blob)

    def test_stuck_event_loop_is_down_and_database_is_not_probed(self):
        pings = []

        async def counting_ping():
            pings.append(1)

        self._patch(health_service, "_db_ping", counting_ping)
        self._patch(health_service, "LOOP_PROBE_TIMEOUT", 0.15)

        # Really wedge the loop: its thread sleeps, so nothing scheduled on it runs.
        self.loop.call_soon_threadsafe(time.sleep, 0.6)
        started = time.perf_counter()
        report = self._health()
        elapsed = time.perf_counter() - started

        self.assertEqual(report["status"], "down")
        self.assertFalse(report["checks"]["event_loop"]["ok"])
        self.assertIn("event loop", report["reasons"][0])
        self.assertIsNone(report["checks"]["database"]["ok"])
        self.assertEqual(pings, [])
        self.assertLess(elapsed, 0.5, "a wedged loop must be reported quickly, not waited on")

    def test_missing_loop_is_down_not_an_exception(self):
        report = health_service.get_health(None)
        self.assertEqual(report["status"], "down")

    def test_database_failure_is_down_and_does_not_leak_the_error_text(self):
        async def failing_ping():
            raise OSError("postgres://user:hunter2@db.example.com:6543/postgres")

        self._patch(health_service, "_db_ping", failing_ping)
        report = self._health()

        self.assertEqual(report["status"], "down")
        self.assertIn("database unreachable", report["reasons"][0])
        self.assertEqual(report["checks"]["database"]["error"], "OSError")
        blob = json.dumps(report)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("db.example.com", blob)

    def test_database_timeout_is_down(self):
        async def hanging_ping():
            await asyncio.sleep(5)

        self._patch(health_service, "_db_ping", hanging_ping)
        self._patch(health_service, "DB_PROBE_TIMEOUT", 0.2)
        report = self._health()

        self.assertEqual(report["status"], "down")
        self.assertEqual(report["checks"]["database"]["error"], "timeout")

    def test_slow_database_is_degraded(self):
        async def slow_ping():
            await asyncio.sleep(0.06)

        self._patch(health_service, "_db_ping", slow_ping)
        self._patch(health_service, "SLOW_DB_MS", 20.0)
        device_registry.mark_packet_received()
        report = self._health()

        self.assertEqual(report["status"], "degraded")
        self.assertIn("database slow", report["reasons"][0])

    def test_database_result_is_cached_so_polling_cannot_drain_the_pool(self):
        pings = []

        async def counting_ping():
            pings.append(1)

        self._patch(health_service, "_db_ping", counting_ping)
        device_registry.mark_packet_received()
        self._health()
        self._health()
        self._health()
        self.assertEqual(len(pings), 1)

        self._patch(health_service, "DB_CACHE_OK_SECONDS", 0.0)  # cache expired
        self._health()
        self.assertEqual(len(pings), 2)

    def test_failed_database_probe_is_retested_before_a_success_would_be(self):
        results = iter([OSError("boom"), None])

        async def flaky_ping():
            outcome = next(results)
            if outcome is not None:
                raise outcome

        self._patch(health_service, "_db_ping", flaky_ping)
        self._patch(health_service, "DB_CACHE_FAIL_SECONDS", 0.0)
        device_registry.mark_packet_received()

        self.assertEqual(self._health()["status"], "down")
        self.assertEqual(self._health()["status"], "ok")  # recovered on the very next poll

    def test_exhausted_pool_is_degraded(self):
        self._patch(health_service, "_pool_stats", lambda: {"size": 150, "idle": 0, "max": 150})
        device_registry.mark_packet_received()
        report = self._health()

        self.assertEqual(report["status"], "degraded")
        self.assertIn("pool exhausted", report["reasons"][0])

    def test_busy_but_not_exhausted_pool_is_fine(self):
        self._patch(health_service, "_pool_stats", lambda: {"size": 150, "idle": 3, "max": 150})
        device_registry.mark_packet_received()
        self.assertEqual(self._health()["status"], "ok")

    def test_near_connection_limit_is_degraded(self):
        self._patch(health_service, "MAX_CONNECTIONS", 10)
        for i in range(9):
            device_registry.register_device(f"{i:015d}", "10.0.0.1", f"dev-{i}", object())
        device_registry.mark_packet_received()
        report = self._health()

        self.assertEqual(report["status"], "degraded")
        self.assertIn("connection limit", report["reasons"][0])

    def test_total_silence_is_degraded(self):
        device_registry._last_packet_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        report = self._health()

        self.assertEqual(report["status"], "degraded")
        self.assertIn("no valid packet from any device for 20 min", report["reasons"][0])
        self.assertGreater(report["last_packet_age_s"], 1100)

    def test_no_packet_ever_after_a_long_uptime_is_degraded(self):
        health_service._started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        report = self._health()

        self.assertEqual(report["status"], "degraded")
        self.assertIn("since the gateway started", report["reasons"][0])
        self.assertIsNone(report["last_packet_age_s"])

    def test_a_freshly_restarted_gateway_gets_a_grace_period(self):
        health_service._started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        self.assertEqual(self._health()["status"], "ok")

    def test_worst_state_wins_and_every_reason_is_kept(self):
        async def failing_ping():
            raise OSError("x")

        self._patch(health_service, "_db_ping", failing_ping)
        device_registry._last_packet_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        report = self._health()

        self.assertEqual(report["status"], "down")
        self.assertEqual(len(report["reasons"]), 2)

    def test_a_state_change_is_logged_once_not_on_every_poll(self):
        logged = []
        self._patch(health_service, "log", logged.append)
        device_registry.mark_packet_received()

        self._health()
        self._health()
        self.assertEqual(len(logged), 1)  # unknown -> ok, then silence

        device_registry._last_packet_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        self._health()
        self._health()
        self.assertEqual(len(logged), 2)  # ok -> degraded

    def test_a_malformed_env_override_cannot_crash_startup(self):
        with patch.dict(os.environ, {"HEALTH_TEST_NUMBER": "abc"}):
            self.assertEqual(health_service._env_number("HEALTH_TEST_NUMBER", 600, int), 600)
        with patch.dict(os.environ, {"HEALTH_TEST_NUMBER": "  "}):
            self.assertEqual(health_service._env_number("HEALTH_TEST_NUMBER", 600, int), 600)
        with patch.dict(os.environ, {"HEALTH_TEST_NUMBER": "30"}):
            self.assertEqual(health_service._env_number("HEALTH_TEST_NUMBER", 600, int), 30)

    def test_mark_packet_received_sets_a_recent_timestamp(self):
        self.assertIsNone(device_registry.get_last_packet_at())
        device_registry.mark_packet_received()
        age = (datetime.now(timezone.utc) - device_registry.get_last_packet_at()).total_seconds()
        self.assertLess(age, 2)


class HealthEndpointTests(HealthTestBase):
    """GET /health over real HTTP against the real handler."""

    def setUp(self):
        super().setUp()
        command_api.COMMAND_API_KEY = TEST_KEY
        command_api._loop = self.loop

        self.server = command_api.ThreadingHTTPServer(("127.0.0.1", 0), command_api.CommandAPIHandler)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _get(self, path, key=TEST_KEY):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Command-Api-Key": key} if key is not None else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def test_health_requires_the_key_still(self):
        status, _ = self._get("/health", key=None)
        self.assertEqual(status, 401)
        status, _ = self._get("/health", key="wrong")
        self.assertEqual(status, 401)

    def test_healthy_gateway_answers_200_with_the_report(self):
        device_registry.mark_packet_received()
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("checks", body)
        self.assertIn("devices_connected", body)

    def test_degraded_gateway_still_answers_200_so_a_poller_gets_the_body(self):
        # no packet and a long uptime -> degraded
        health_service._started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "degraded")

    def test_down_gateway_answers_503_with_a_json_body(self):
        async def failing_ping():
            raise OSError("x")

        self._patch(health_service, "_db_ping", failing_ping)
        status, body = self._get("/health")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "down")

    def test_a_stalled_client_does_not_block_other_requests(self):
        stalled = socket.create_connection(("127.0.0.1", self.port), timeout=5)  # connects, sends nothing
        try:
            device_registry.mark_packet_received()
            started = time.perf_counter()
            status, _ = self._get("/health")
            self.assertEqual(status, 200)
            self.assertLess(time.perf_counter() - started, 2.0)
        finally:
            stalled.close()

    def test_the_command_api_is_started_threaded_with_the_stall_timeout(self):
        fake_server = MagicMock()
        with patch.object(command_api, "ThreadingHTTPServer", return_value=fake_server) as threaded, \
                patch.object(command_api, "COMMAND_API_KEY", TEST_KEY):
            command_api.start_command_api()

        threaded.assert_called_once()
        args = threaded.call_args[0]
        self.assertEqual(args[0][0], "0.0.0.0")
        self.assertIs(args[1], command_api.CommandAPIHandler)
        fake_server.serve_forever.assert_called_once()
        self.assertEqual(command_api.CommandAPIHandler.timeout, 10)


if __name__ == "__main__":
    unittest.main()