"""
Unit tests for the power-state plumbing: the SQL helper and the two parser
rules. The per-family end-to-end behaviour is in test_multiprotocol_server.py
(GT06, GPS103), test_h02_server.py and test_teltonika_server.py.

    python -m unittest discover -s test -p "test_power_status.py" -v
"""

import os
import sys
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from constants.power_status import POWER_CONNECTED, POWER_DISCONNECTED    # noqa: E402
from parsers import h02_parser, teltonika_parser as tp                      # noqa: E402
from services import tracking_service                                       # noqa: E402

DEVICE = "11111111-1111-1111-1111-111111111111"


class SetPowerStatusTests(unittest.IsolatedAsyncioTestCase):
    async def run_helper(self, disconnected):
        conn = AsyncMock()

        @asynccontextmanager
        async def fake():
            yield conn

        with patch.object(tracking_service, "managed_connection", fake):
            await tracking_service.set_power_status(DEVICE, disconnected)
        return conn.execute.await_args

    async def test_disconnected_writes_status_1(self):
        call = await self.run_helper(True)
        self.assertEqual(call.args[1], DEVICE)
        self.assertEqual(call.args[3], POWER_DISCONNECTED)

    async def test_connected_writes_status_0(self):
        call = await self.run_helper(False)
        self.assertEqual(call.args[3], POWER_CONNECTED)

    async def test_only_power_columns_are_updated_on_conflict(self):
        sql = (await self.run_helper(True)).args[0]
        update = sql.split("DO UPDATE SET")[1]
        self.assertIn('"PowerStatus"', update)
        for untouched in ("IgnitionStatus", "BatteryLevel", "GpsSignal", "IsOnline", "MovementStatus"):
            self.assertNotIn(untouched, update)

    def test_values_match_the_api_enum(self):
        self.assertEqual((POWER_CONNECTED, POWER_DISCONNECTED), (0, 1))


class H02PowerBitTests(unittest.TestCase):
    def test_cleared_bit_19_is_a_power_cut(self):
        self.assertTrue(h02_parser.power_cut(0xFFF7FBFF))

    def test_set_bit_19_is_normal(self):
        self.assertFalse(h02_parser.power_cut(0xFFFFFBFF))

    def test_other_cleared_bits_do_not_look_like_a_power_cut(self):
        self.assertFalse(h02_parser.power_cut(0xFFFFFBFD))      # SOS only
        self.assertFalse(h02_parser.power_cut(0xFFFFFFFE))      # vibration only

    def test_power_cut_is_independent_of_the_first_alarm_match(self):
        self.assertEqual(h02_parser.decode_status(0xFFF7FBFD)[1], "SOS")
        self.assertTrue(h02_parser.power_cut(0xFFF7FBFD))


class TeltonikaUnplugTests(unittest.TestCase):
    def rec(self, io):
        from datetime import datetime, timezone
        return tp.AvlRecord(datetime.now(timezone.utc), 0, 0.0, 0.0, 0, 0, 0, 0, 0, io)

    def test_unplug_1_is_a_power_cut(self):
        self.assertIs(self.rec({252: 1}).power_cut, True)

    def test_unplug_0_is_power_present(self):
        self.assertIs(self.rec({252: 0}).power_cut, False)

    def test_not_reported_is_none(self):
        self.assertIsNone(self.rec({239: 1}).power_cut)

    def test_variable_length_value_is_not_a_flag(self):
        self.assertIsNone(self.rec({252: b"\x01"}).power_cut)


if __name__ == "__main__":
    unittest.main()
