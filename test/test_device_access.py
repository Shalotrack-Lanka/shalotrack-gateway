"""
Trial login rule: pure decisions, and the repository wiring for off / shadow / enforce.
No database needed; the connection is a small fake.

    python -m unittest discover -s test -p "test_device_access*.py" -v
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import device_access  # noqa: E402
from repositories.device_repository import DeviceRepository  # noqa: E402
from repositories import device_repository  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
BOUND = datetime(2026, 11, 1, tzinfo=timezone.utc)


class DecideTests(unittest.TestCase):
    def d(self, status, bound=None, seen=None):
        return device_access.decide(status, bound, seen, now=NOW, trial_hours=24)

    def test_unknown_is_refused(self):
        self.assertFalse(self.d(None).allowed)

    def test_stopped_statuses_are_refused_even_if_bound(self):
        for st in ("Temporarily Stopped", "Pending Repair", "Broken Device"):
            self.assertFalse(self.d(st, bound=BOUND).allowed, st)

    def test_activated_is_accepted_bound_or_not(self):
        self.assertTrue(self.d("Activated").allowed)
        self.assertTrue(self.d("Activated", bound=BOUND).allowed)

    def test_bound_device_is_accepted_whatever_its_status_label(self):
        self.assertTrue(self.d("Assigned to Customer", bound=BOUND).allowed)

    def test_first_connection_starts_the_trial(self):
        r = self.d("Not Activated")
        self.assertTrue(r.allowed)
        self.assertTrue(r.stamp_first_seen)

    def test_inside_window_accepted_after_window_refused(self):
        self.assertTrue(self.d("Not Activated", seen=NOW - timedelta(hours=23, minutes=59)).allowed)
        r = self.d("Not Activated", seen=NOW - timedelta(hours=24, minutes=1))
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "trial_expired")

    def test_assigned_to_customer_unbound_gets_the_same_trial(self):
        self.assertTrue(self.d("Assigned to Customer", seen=NOW - timedelta(hours=1)).allowed)
        self.assertFalse(self.d("Assigned to Customer", seen=NOW - timedelta(hours=30)).allowed)

    def test_naive_timestamp_is_treated_as_utc(self):
        seen = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        self.assertTrue(self.d("Not Activated", seen=seen).allowed)


class FakeConn:
    """Answers the two lookups and records the trial-clock UPDATE."""

    def __init__(self, gps=None, setup=None, fail_update=False):
        self.gps, self.setup, self.fail_update = gps, setup, fail_update
        self.updates = []
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if 'SELECT "DeviceId" FROM "GpsDevices"' in sql:
            return {"DeviceId": "new-dev"}   # re-fetch after auto-registration
        if '"GpsDevices" g' in sql:
            return self.gps
        if 'FROM "SetupShalotrackDevices"' in sql:
            return self.setup
        return None

    async def execute(self, sql, *args):
        if 'UPDATE "SetupShalotrackDevices"' in sql:
            if self.fail_update:
                raise RuntimeError("permission denied for column FirstSeenAt")
            self.updates.append(args)
        return "OK"


def run_lookup(conn, mode):
    async def get_conn():
        return conn

    async def release(_c):
        return None

    class _Managed:
        async def __aenter__(self_inner):
            return conn

        async def __aexit__(self_inner, *a):
            return False

    with patch.object(device_access, "MODE", mode), \
         patch.object(device_repository, "get_db_connection", get_conn), \
         patch.object(device_repository, "release_db_connection", release), \
         patch.object(device_repository, "managed_connection", lambda: _Managed()):
        return asyncio.run(DeviceRepository.get_device_by_imei("111"))


def setup_row(status, bound=None, seen=None):
    return {"ImeiNumber": "111", "DeviceCategory": "V5", "SimNumber": None,
            "Status": status, "BoundAt": bound, "FirstSeenAt": seen}


def gps_row(status, bound=None, seen=None):
    return {"DeviceId": "dev-1", "Status": status, "BoundAt": bound, "FirstSeenAt": seen}


class RepositoryTests(unittest.TestCase):
    def test_off_mode_is_exactly_the_old_rule_and_never_reads_new_columns(self):
        c = FakeConn(gps=gps_row("Not Activated"))
        self.assertIsNone(run_lookup(c, "off"))
        self.assertTrue(all("FirstSeenAt" not in q for q in c.queries))
        self.assertEqual(run_lookup(FakeConn(gps=gps_row("Activated")), "off"), "dev-1")
        self.assertIsNone(run_lookup(FakeConn(gps=None, setup=setup_row("Not Activated")), "off"))

    def test_legacy_device_without_setup_row_still_allowed(self):
        for mode in ("off", "shadow", "enforce"):
            self.assertEqual(run_lookup(FakeConn(gps=gps_row(None)), mode), "dev-1", mode)

    def test_shadow_behaves_like_off_and_writes_nothing(self):
        c = FakeConn(gps=gps_row("Not Activated"))
        self.assertIsNone(run_lookup(c, "shadow"))
        self.assertEqual(c.updates, [])
        self.assertEqual(run_lookup(FakeConn(gps=gps_row("Activated")), "shadow"), "dev-1")

    def test_enforce_first_connection_stamps_clock_and_accepts(self):
        c = FakeConn(gps=gps_row("Not Activated"))
        self.assertEqual(run_lookup(c, "enforce"), "dev-1")
        self.assertEqual(len(c.updates), 1)

    def test_enforce_expired_trial_is_refused(self):
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        c = FakeConn(gps=gps_row("Not Activated", seen=old))
        self.assertIsNone(run_lookup(c, "enforce"))
        self.assertEqual(c.updates, [])

    def test_enforce_trial_in_window_does_not_restamp(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        c = FakeConn(gps=gps_row("Assigned to Customer", seen=recent))
        self.assertEqual(run_lookup(c, "enforce"), "dev-1")
        self.assertEqual(c.updates, [])

    def test_enforce_bound_and_stopped(self):
        self.assertEqual(run_lookup(FakeConn(gps=gps_row("Assigned to Customer", bound=BOUND)), "enforce"), "dev-1")
        self.assertIsNone(run_lookup(FakeConn(gps=gps_row("Temporarily Stopped", bound=BOUND)), "enforce"))

    def test_enforce_refuses_when_clock_cannot_be_written(self):
        c = FakeConn(gps=gps_row("Not Activated"), fail_update=True)
        self.assertIsNone(run_lookup(c, "enforce"))

    def test_enforce_new_trial_device_gets_registered(self):
        c = FakeConn(gps=None, setup=setup_row("Not Activated"))
        got = run_lookup(c, "enforce")
        self.assertIsNotNone(got)
        self.assertEqual(len(c.updates), 1)

    def test_unknown_imei_refused_in_every_mode(self):
        for mode in ("off", "shadow", "enforce"):
            self.assertIsNone(run_lookup(FakeConn(gps=None, setup=None), mode), mode)


if __name__ == "__main__":
    unittest.main()
