"""
Tests for the GPS103 silent-device poller.

    python -m unittest discover -s test -p "test_gps103_poller.py" -v
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.path.insert(0, os.path.dirname(__file__))
import config                                        # noqa: E402
from test_multiprotocol_server import ServerTestCase, KNOWN_IMEI, tracker   # noqa: E402
from services import device_registry as reg          # noqa: E402
from services import gps103_poller as poller          # noqa: E402

IMEI = "868683022801132"
GT06_IMEI = "359586015829802"


class FakeWriter:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def write(self, data):
        if self.fail:
            raise ConnectionResetError("gone")
        self.sent.append(data)

    async def drain(self):
        pass


def age(imei, seconds):
    """Pretend the device connected `seconds` ago and has not reported."""
    reg.get_device(imei)["connected_at"] = datetime.now(timezone.utc) - timedelta(seconds=seconds)


class PollerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reg._by_imei.clear()
        reg._by_socket.clear()
        for name, value in (("GPS103_POLL_SECONDS", 30), ("GPS103_POLL_IDLE_SECONDS", 300)):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.w = FakeWriter()
        reg.register_device(IMEI, "10.0.0.1", "dev-1", self.w, protocol="GPS103")

    async def test_silent_device_is_asked_once_with_the_verified_command(self):
        age(IMEI, 400)
        self.assertEqual(await poller.poll_once(), 1)
        self.assertEqual(self.w.sent, [b"**,imei:868683022801132,B;"])
        # asked just now -> not asked again until the interval passes
        self.assertEqual(await poller.poll_once(), 0)
        self.assertEqual(len(self.w.sent), 1)

    async def test_recent_position_means_no_poll(self):
        age(IMEI, 400)
        reg.note_position(IMEI, 40.0)
        self.assertEqual(await poller.poll_once(), 0)
        self.assertEqual(self.w.sent, [])

    async def test_moving_uses_short_interval_parked_uses_idle_interval(self):
        reg.note_position(IMEI, 40.0)
        reg.get_device(IMEI)["last_position_at"] -= timedelta(seconds=45)
        self.assertEqual(poller.due_devices(), [IMEI])             # moving, 45 s > 30 s

        reg.note_position(IMEI, 0.0)
        reg.get_device(IMEI)["last_position_at"] -= timedelta(seconds=45)
        self.assertEqual(poller.due_devices(), [])                  # parked, 45 s < 300 s
        reg.get_device(IMEI)["last_position_at"] -= timedelta(seconds=300)
        self.assertEqual(poller.due_devices(), [IMEI])

    async def test_freshly_connected_device_is_given_time_to_report(self):
        self.assertEqual(await poller.poll_once(), 0)

    async def test_other_protocols_are_never_written_to(self):
        w2 = FakeWriter()
        reg.register_device(GT06_IMEI, "10.0.0.2", "dev-2", w2, protocol="GT06")
        age(GT06_IMEI, 4000)
        age(IMEI, 400)
        await poller.poll_once()
        self.assertEqual(w2.sent, [])
        self.assertEqual(len(self.w.sent), 1)

    async def test_a_broken_socket_does_not_stop_the_others(self):
        bad, good = FakeWriter(fail=True), FakeWriter()
        reg.register_device("111111111111111", "10.0.0.3", "dev-3", bad, protocol="GPS103")
        reg.register_device("222222222222222", "10.0.0.4", "dev-4", good, protocol="GPS103")
        for imei in ("111111111111111", "222222222222222"):
            age(imei, 400)
        self.assertEqual(await poller.poll_once(), 1)
        self.assertEqual(good.sent, [b"**,imei:222222222222222,B;"])

    async def test_runner_survives_errors_and_can_be_cancelled(self):
        calls = []
        real_sleep = asyncio.sleep

        async def boom(*a, **k):
            calls.append(1)
            raise RuntimeError("x")

        with patch.object(poller, "poll_once", boom), \
             patch.object(config, "GPS103_POLL_SECONDS", 10), \
             patch("services.gps103_poller.asyncio.sleep", lambda s: real_sleep(0)):
            task = asyncio.create_task(poller.run_gps103_poller())
            for _ in range(20):
                await real_sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertGreater(len(calls), 1)


class HandlerHookTests(ServerTestCase):
    """A stored position must reach the registry, or the poller would keep asking."""

    async def test_stored_position_updates_the_registry(self):
        reader, writer = await self.login()
        self.assertIsNone(reg.get_device(KNOWN_IMEI).get("last_position_at"))
        writer.write(tracker(speed="10.00"))
        await writer.drain()
        await self.until(lambda: self.mocks["track_g103"].await_count == 1)
        await self.until(lambda: reg.get_device(KNOWN_IMEI).get("last_position_at") is not None)
        self.assertGreater(reg.get_device(KNOWN_IMEI)["last_speed"], 0)

    async def test_report_without_a_fix_does_not_count_as_a_position(self):
        reader, writer = await self.login()
        writer.write(tracker(flag="V"))
        await writer.drain()
        await asyncio.sleep(0.3)
        self.assertIsNone(reg.get_device(KNOWN_IMEI).get("last_position_at"))


class ConfigTests(unittest.TestCase):
    def reload(self, **env):
        import importlib
        with patch.dict(os.environ, env, clear=False):
            return importlib.reload(config)

    def tearDown(self):
        import importlib
        for k in ("GPS103_POLL_SECONDS", "GPS103_POLL_IDLE_SECONDS"):
            os.environ.pop(k, None)
        importlib.reload(config)

    def test_default_is_off(self):
        os.environ.pop("GPS103_POLL_SECONDS", None)
        self.assertEqual(self.reload().GPS103_POLL_SECONDS, 0)

    def test_tiny_values_are_raised_to_10_and_garbage_means_off(self):
        self.assertEqual(self.reload(GPS103_POLL_SECONDS="3").GPS103_POLL_SECONDS, 10)
        self.assertEqual(self.reload(GPS103_POLL_SECONDS="abc").GPS103_POLL_SECONDS, 0)
        self.assertEqual(self.reload(GPS103_POLL_SECONDS="-5").GPS103_POLL_SECONDS, 0)

    def test_idle_is_never_shorter_than_moving(self):
        c = self.reload(GPS103_POLL_SECONDS="60", GPS103_POLL_IDLE_SECONDS="20")
        self.assertEqual(c.GPS103_POLL_IDLE_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
