"""
Re-check loop, drop-device and the Command API /drop endpoint. No database: fetch/drop are fakes.

    python -m unittest discover -s test -p "test_device_enforcer*.py" -v
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
KEY = "test-shared-secret-do-not-use-in-prod"
os.environ["COMMAND_API_KEY"] = KEY

from services import device_access, device_enforcer, device_registry  # noqa: E402
import command_api  # noqa: E402

NOW = datetime.now(timezone.utc)


class FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def connect(imei):
    w = FakeWriter()
    device_registry.register_device(imei, "1.2.3.4", f"dev-{imei}", w)
    return w


def row(status, bound=None, seen=None):
    return {"ImeiNumber": "x", "Status": status, "BoundAt": bound, "FirstSeenAt": seen}


class Base(unittest.TestCase):
    def setUp(self):
        for imei in list(device_registry.get_all_devices()):
            device_registry.unregister_device(imei)

    tearDown = setUp


class RuleTests(Base):
    def test_old_rule_when_trial_off_or_shadow(self):
        for mode in ("off", "shadow"):
            self.assertTrue(device_enforcer.connected_rule_allows(row("Activated"), mode)[0])
            self.assertFalse(device_enforcer.connected_rule_allows(row("Temporarily Stopped"), mode)[0])
            self.assertFalse(device_enforcer.connected_rule_allows(row("Not Activated"), mode)[0])

    def test_legacy_device_without_setup_row_is_kept(self):
        self.assertTrue(device_enforcer.connected_rule_allows(None, "enforce")[0])
        self.assertTrue(device_enforcer.connected_rule_allows(None, "off")[0])

    def test_enforce_uses_the_trial_rule(self):
        f = device_enforcer.connected_rule_allows
        self.assertTrue(f(row("Not Activated", seen=NOW - timedelta(hours=2)), "enforce")[0])
        self.assertFalse(f(row("Not Activated", seen=NOW - timedelta(hours=30)), "enforce")[0])
        self.assertTrue(f(row("Assigned to Customer", bound=NOW), "enforce")[0])
        self.assertFalse(f(row("Broken Device", bound=NOW), "enforce")[0])

    def test_enforce_does_not_cut_a_connected_device_whose_clock_never_started(self):
        ok, why = device_enforcer.connected_rule_allows(row("Not Activated"), "enforce")
        self.assertTrue(ok)
        self.assertEqual(why, "trial_clock_missing")


class RecheckTests(Base):
    def run_once(self, mode, rows, **kw):
        async def fetch(_imeis):
            if isinstance(rows, Exception):
                raise rows
            return rows

        dropped = []

        async def drop(imei, reason):
            dropped.append(imei)
            return True

        with patch.object(device_access, "MODE", kw.get("trial", "off")):
            res = asyncio.run(device_enforcer.recheck_once(mode, fetch, drop))
        return res, dropped

    def test_off_does_nothing(self):
        connect("111111111111111")
        res, dropped = self.run_once("off", {"111111111111111": row("Temporarily Stopped")})
        self.assertEqual(dropped, [])

    def test_enforce_drops_a_stopped_device_and_keeps_good_ones(self):
        connect("111111111111111")
        connect("222222222222222")
        res, dropped = self.run_once("enforce", {
            "111111111111111": row("Temporarily Stopped"),
            "222222222222222": row("Activated"),
        })
        self.assertEqual(dropped, ["111111111111111"])

    def test_shadow_only_logs(self):
        connect("111111111111111")
        res, dropped = self.run_once("shadow", {"111111111111111": row("Broken Device")})
        self.assertEqual(dropped, [])
        self.assertEqual(res["would_drop"], ["111111111111111"])

    def test_trial_expiry_is_enforced_only_in_enforce_trial_mode(self):
        connect("111111111111111")
        old = {"111111111111111": row("Not Activated", seen=NOW - timedelta(hours=40))}
        _, dropped = self.run_once("enforce", old, trial="enforce")
        self.assertEqual(dropped, ["111111111111111"])

    def test_database_error_drops_nothing(self):
        connect("111111111111111")
        res, dropped = self.run_once("enforce", RuntimeError("db down"))
        self.assertEqual(dropped, [])
        self.assertIn("db down", res["error"])

    def test_mass_drop_valve_blocks_a_bad_sync(self):
        rows = {}
        for i in range(30):
            imei = f"{100000000000000 + i}"
            connect(imei)
            rows[imei] = row("Temporarily Stopped")       # everything suddenly "stopped"
        res, dropped = self.run_once("enforce", rows)
        self.assertEqual(dropped, [])
        self.assertTrue(res["blocked"])

    def test_small_fleet_may_still_drop_a_few(self):
        for i in range(3):
            connect(f"{100000000000000 + i}")
        rows = {f"{100000000000000 + i}": row("Temporarily Stopped") for i in range(3)}
        _, dropped = self.run_once("enforce", rows)
        self.assertEqual(len(dropped), 3)


class DropTests(Base):
    def test_drop_closes_the_socket(self):
        w = connect("111111111111111")
        self.assertTrue(asyncio.run(device_enforcer.drop_device("111111111111111", "test")))
        self.assertTrue(w.closed)

    def test_drop_of_an_offline_device_is_a_harmless_false(self):
        self.assertFalse(asyncio.run(device_enforcer.drop_device("999999999999999")))


class DropEndpointTests(Base):
    def setUp(self):
        super().setUp()
        command_api.COMMAND_API_KEY = KEY
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        command_api._loop = self.loop
        self.server = command_api.HTTPServer(("127.0.0.1", 0), command_api.CommandAPIHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        super().tearDown()

    def post(self, body, key=KEY):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if key is not None:
            h["X-Command-Api-Key"] = key
        c.request("POST", "/drop", body=json.dumps(body).encode(), headers=h)
        r = c.getresponse()
        data = json.loads(r.read().decode())
        c.close()
        return r.status, data

    def test_requires_the_key(self):
        self.assertEqual(self.post({"imei": "111111111111111"}, key=None)[0], 401)
        self.assertEqual(self.post({"imei": "111111111111111"}, key="wrong")[0], 401)

    def test_rejects_bad_imei(self):
        for bad in ("", "abc", "1" * 40, "12;drop"):
            self.assertEqual(self.post({"imei": bad})[0], 400, bad)

    def test_drops_a_connected_device(self):
        w = connect("111111111111111")
        status, body = self.post({"imei": "111111111111111", "reason": "erp stop"})
        self.assertEqual(status, 200)
        self.assertTrue(body["was_connected"])
        self.assertTrue(w.closed)

    def test_offline_device_is_reported_not_an_error(self):
        status, body = self.post({"imei": "999999999999999"})
        self.assertEqual(status, 200)
        self.assertFalse(body["was_connected"])


if __name__ == "__main__":
    unittest.main()
