"""
/access report (ERP Gateway page). No database: the fetch is a fake.

    python -m unittest test.test_access_report -v
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
KEY = "test-shared-secret-do-not-use-in-prod"
os.environ["COMMAND_API_KEY"] = KEY

from services import access_report, device_registry  # noqa: E402
import command_api  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def row(imei, status, bound=None, seen=None):
    return {"ImeiNumber": imei, "Status": status, "BoundAt": bound, "FirstSeenAt": seen}


class Writer:
    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        for i in list(device_registry.get_all_devices()):
            device_registry.unregister_device(i)

    tearDown = setUp

    def report(self, rows):
        async def fetch():
            return rows
        return asyncio.run(access_report.build_report(fetch, NOW, 24))


class ReportTests(Base):
    def test_categories_and_hours_left(self):
        r = self.report([
            row("1", "Activated"),
            row("2", "Assigned to Customer", bound=NOW),
            row("3", "Not Activated", seen=NOW - timedelta(hours=6)),
            row("4", "Not Activated", seen=NOW - timedelta(hours=30)),
            row("5", "Not Activated"),
            row("6", "Temporarily Stopped"),
        ])
        self.assertEqual(r["counts"], {"allowed": 2, "trial": 1, "trial_not_started": 1, "trial_expired": 1, "stopped": 1})
        by = {d["imei"]: d for d in r["devices"]}
        self.assertEqual(by["3"]["trial_hours_left"], 18.0)
        self.assertEqual(by["4"]["category"], "trial_expired")
        self.assertEqual(by["5"]["category"], "trial_not_started")
        self.assertNotIn("1", by)                      # quiet, fine device: counted, not listed
        self.assertEqual(r["total_devices"], 6)

    def test_connected_fine_device_is_listed(self):
        device_registry.register_device("1", "1.1.1.1", "d", Writer())
        r = self.report([row("1", "Activated")])
        self.assertEqual(r["devices"][0]["connected"], True)
        self.assertEqual(r["connected_now"], 1)

    def test_connected_but_not_registered_is_reported(self):
        device_registry.register_device("999", "1.1.1.1", "d", Writer())
        r = self.report([row("1", "Activated")])
        self.assertEqual(r["connected_not_registered"], ["999"])

    def test_office_attention_comes_first(self):
        r = self.report([
            row("a", "Not Activated", seen=NOW - timedelta(hours=1)),
            row("b", "Not Activated", seen=NOW - timedelta(hours=40)),
            row("c", "Broken Device"),
        ])
        self.assertEqual([d["imei"] for d in r["devices"]], ["b", "c", "a"])

    def test_row_cap_sets_truncated(self):
        rows = [row(str(i), "Not Activated", seen=NOW - timedelta(hours=30)) for i in range(access_report.MAX_ROWS + 5)]
        r = self.report(rows)
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["devices"]), access_report.MAX_ROWS)
        self.assertEqual(r["counts"]["trial_expired"], access_report.MAX_ROWS + 5)


class EndpointTests(Base):
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

    def get(self, key=KEY):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Command-Api-Key": key} if key else {}
        c.request("GET", "/access", headers=h)
        r = c.getresponse()
        body = json.loads(r.read().decode())
        c.close()
        return r.status, body

    def test_requires_key(self):
        self.assertEqual(self.get(None)[0], 401)
        self.assertEqual(self.get("wrong")[0], 401)

    def test_database_error_is_503_without_details(self):
        async def boom():
            raise RuntimeError("secret-db-host down")
        original = command_api.build_report
        command_api.build_report = lambda: boom()
        try:
            status, body = self.get()
        finally:
            command_api.build_report = original
        self.assertEqual(status, 503)
        self.assertNotIn("secret-db-host", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
