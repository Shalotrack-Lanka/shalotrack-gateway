"""
Unit tests for protocol detection and the GPS103 (TK103A/B) parser.

Pure logic, no database, no network. Run from the repo root:

    python -m unittest discover -s test -p "test_gps103*.py" -v
    python -m unittest test.test_gps103_parser -v

Sample messages marked [traccar] are published fixtures from Traccar's open
source gps103 decoder tests. Messages marked [synthetic] were built here to
cover Sri Lanka (UTC+5:30) and the midnight date rollover.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from protocols import GT06, GPS103, detect_protocol          # noqa: E402
from parsers import gps103_parser as p                       # noqa: E402


class DetectProtocolTests(unittest.TestCase):
    def test_gt06_short_and_long_frames(self):
        self.assertEqual(detect_protocol(b"\x78\x78\x0d\x01"), GT06)
        self.assertEqual(detect_protocol(b"\x79\x79\x00\x20\x94"), GT06)

    def test_gps103_login_and_bare_data(self):
        self.assertEqual(detect_protocol(b"##,imei:359586015829802,A;"), GPS103)
        self.assertEqual(detect_protocol(b"imei:359586015829802,tracker,,,L,;"), GPS103)

    def test_unknown_and_empty_are_not_guessed(self):
        self.assertIsNone(detect_protocol(b""))
        self.assertIsNone(detect_protocol(b"GET / HTTP/1.1\r\n"))
        self.assertIsNone(detect_protocol(b"\x00\x01\x02\x03\x04"))
        self.assertIsNone(detect_protocol(b"*HQ,1234567890,V1,"))   # H02 family: not supported


class FramerTests(unittest.TestCase):
    def test_splits_on_semicolon_and_strips_whitespace(self):
        msgs, rest = p.extract_messages(b"##,imei:359586015829802,A;\r\n359586015829802;")
        self.assertEqual(msgs, [b"##,imei:359586015829802,A", b"359586015829802"])
        self.assertEqual(rest, b"")

    def test_partial_message_stays_buffered(self):
        msgs, rest = p.extract_messages(b"imei:359586015829802,tracker,0809")
        self.assertEqual(msgs, [])
        self.assertEqual(rest, b"imei:359586015829802,tracker,0809")

        msgs, rest = p.extract_messages(rest + b"231429,,F,;")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(rest, b"")

    def test_two_messages_one_segment(self):
        msgs, _ = p.extract_messages(b"359586015829802;359586015829802;")
        self.assertEqual(len(msgs), 2)

    def test_unterminated_flood_raises_so_the_connection_is_dropped(self):
        with self.assertRaises(p.FrameOverflowError):
            p.extract_messages(b"A" * (p.GPS103_MAX_UNFRAMED_BYTES + 1))

    def test_buffer_exactly_at_the_limit_is_still_allowed(self):
        msgs, rest = p.extract_messages(b"A" * p.GPS103_MAX_UNFRAMED_BYTES)
        self.assertEqual((msgs, len(rest)), ([], p.GPS103_MAX_UNFRAMED_BYTES))


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(p.classify(b"##,imei:359586015829802,A")[0], p.LOGIN)          # [traccar]
        self.assertEqual(p.classify(b"359586015829802")[0], p.HEARTBEAT)                # [traccar]
        self.assertEqual(p.classify(b"imei:359586015829802,tracker,,,L,")[0], p.TRACKER)
        self.assertEqual(p.classify(b"hello")[0], p.UNKNOWN)
        self.assertEqual(p.classify(b"\xff\xfe\x00")[0], p.UNKNOWN)

    def test_login_imei(self):
        self.assertEqual(p.parse_login("##,imei:359586015829802,A")["imei"], "359586015829802")
        with self.assertRaises(p.Gps103ParseError):
            p.parse_login("##,imei:12,A")


class TrackerParseTests(unittest.TestCase):
    def test_normal_fix_northern_eastern_sri_lanka(self):
        # [synthetic] Colombo ~ 6°56'N 79°51'E, device clock UTC+5:30
        msg = "imei:359586015829802,tracker,261013143012,,F,090012.000,A,0656.2000,N,07951.2000,E,0.00,90.5;"
        r = p.parse_tracker(msg.rstrip(";"))
        self.assertTrue(r["valid_fix"])
        self.assertIsNone(r["alarm_name"])
        self.assertAlmostEqual(r["latitude"], 6 + 56.2 / 60, places=6)
        self.assertAlmostEqual(r["longitude"], 79 + 51.2 / 60, places=6)
        self.assertEqual(r["heading"], 90.5)
        # local 14:30, UTC 09:00 -> +5:30 offset -> 2026-10-13 09:00:12 UTC
        self.assertEqual(r["timestamp"], datetime(2026, 10, 13, 9, 0, 12, tzinfo=timezone.utc))

    def test_southwestern_hemisphere_signs(self):
        # [traccar] fixture is 10°N / 67°W; flipping N->S must flip the sign
        msg = "imei:864035052942928,,241214083746,100%,F,123746.000,A,1012.63368,N,06757.59559,W,0.00,340.61"
        r = p.parse_tracker(msg)
        self.assertGreater(r["latitude"], 0)
        self.assertLess(r["longitude"], 0)
        self.assertAlmostEqual(r["longitude"], -(67 + 57.59559 / 60), places=6)
        south = msg.replace(",N,", ",S,")
        self.assertLess(p.parse_tracker(south)["latitude"], 0)

    def test_speed_knots_to_kmh_and_kmh_override(self):
        msg = "imei:359586015829802,tracker,261013143012,,F,090012.000,A,0656.2000,N,07951.2000,E,10.00,0"
        self.assertAlmostEqual(p.parse_tracker(msg)["speed"], 18.52, places=2)

        original = p.GPS103_SPEED_IN_KMH
        try:
            p.GPS103_SPEED_IN_KMH = True
            self.assertEqual(p.parse_tracker(msg)["speed"], 10.0)
        finally:
            p.GPS103_SPEED_IN_KMH = original

    def test_midnight_rollover_resolves_previous_utc_day(self):
        # [synthetic] 00:10 local on 14 Oct (+5:30) is 18:40 UTC on 13 Oct
        msg = "imei:359586015829802,tracker,261014001000,,F,184000.000,A,0656.2000,N,07951.2000,E,0.00,0"
        ts = p.parse_tracker(msg)["timestamp"]
        self.assertEqual(ts, datetime(2026, 10, 13, 18, 40, 0, tzinfo=timezone.utc))

    def test_sos_alarm(self):
        # [traccar]
        msg = "imei:359586015829802,help me,0809231429,13554900601,F,062947.294,A,2234.4026,N,11354.3277,E,0.00,"
        r = p.parse_tracker(msg)
        self.assertEqual(r["alarm_name"], "SOS")
        self.assertTrue(r["valid_fix"])
        self.assertIn("SOS", p.HIGH_SEVERITY_ALARMS)
        self.assertEqual(p.build_help_me_ack("359586015829802"), b"**,imei:359586015829802,E;")

    def test_other_alarm_keywords(self):
        base = "imei:359586015829802,{},0809231429,13554900601,F,062947.294,A,2234.4026,N,11354.3277,E,0.00,"
        expected = {
            "low battery": "LOW_BATTERY", "speed": "OVERSPEED", "stockade": "GEOFENCE",
            "move": "MOVE", "ac alarm": "POWER_CUT", "door alarm": "DOOR_OPEN",
            "acc on": "ACC_ON", "acc off": "ACC_OFF",
        }
        for keyword, name in expected.items():
            self.assertEqual(p.parse_tracker(base.format(keyword))["alarm_name"], name, keyword)

    def test_keyword_matching_is_case_insensitive(self):
        msg = "imei:359586015829802,HELP ME,0809231429,,F,062947.294,A,2234.4026,N,11354.3277,E,0.00,"
        self.assertEqual(p.parse_tracker(msg)["alarm_name"], "SOS")

    def test_unknown_keyword_is_flagged_not_dropped(self):
        msg = "imei:359586015829802,zz9,0809231429,,F,062947.294,A,2234.4026,N,11354.3277,E,0.00,"
        r = p.parse_tracker(msg)
        self.assertFalse(r["known_keyword"])
        self.assertIsNone(r["alarm_name"])
        self.assertTrue(r["valid_fix"])

    def test_ignition_from_keyword_and_flag(self):
        on = "imei:864180036029895,acc on,180508145653,,F,065645.000,A,4729.1497,N,01904.2342,E,0.00,0,,1,,0.00%,,"   # [traccar]
        self.assertTrue(p.parse_tracker(on)["ignition"])
        off = "imei:865328023776874,acc off,150619152221,,F,072218.000,A,5439.8489,N,02518.5945,E,0.00,,,1,1,0.0,0.0,23.0,"  # [traccar]
        self.assertFalse(p.parse_tracker(off)["ignition"])        # keyword wins over the flag
        flag = "imei:359586015829802,tracker,261013143012,,F,090012.000,A,0656.2000,N,07951.2000,E,0.00,0,,1,0"
        self.assertTrue(p.parse_tracker(flag)["ignition"])
        none = "imei:359586015829802,tracker,261013143012,,F,090012.000,A,0656.2000,N,07951.2000,E,0.00,0"
        self.assertIsNone(p.parse_tracker(none)["ignition"])

    def test_lbs_only_has_no_position(self):
        for msg in (
            "imei:123451234512345,L,",                                          # [traccar]
            "imei:864895030279986,ac alarm,180404174252,,L,,,296a,,51f7,,,",    # [traccar]
        ):
            r = p.parse_tracker(msg)
            self.assertFalse(r["valid_fix"])
            self.assertIsNone(r["latitude"])
            self.assertIsNone(r["longitude"])

    def test_alarm_without_fix_still_reports_the_alarm(self):
        r = p.parse_tracker("imei:864895030279986,ac alarm,180404174252,,L,,,296a,,51f7,,,")
        self.assertEqual(r["alarm_name"], "POWER_CUT")

    def test_void_gps_flag_is_not_a_fix(self):
        msg = "imei:359586015829802,tracker,261013143012,,F,090012.000,V,0656.2000,N,07951.2000,E,0.00,0"
        self.assertFalse(p.parse_tracker(msg)["valid_fix"])

    def test_malformed_coordinates_are_not_a_fix(self):
        for lat, lon in (("abc", "07951.2000"), ("0656.2000", ""), ("0699.9999", "07951.2000"), ("", "")):
            msg = f"imei:359586015829802,tracker,261013143012,,F,090012.000,A,{lat},N,{lon},E,0.00,0"
            self.assertFalse(p.parse_tracker(msg)["valid_fix"], (lat, lon))

    def test_bad_timestamp_gives_none_not_an_exception(self):
        msg = "imei:359586015829802,tracker,garbage,,F,090012.000,A,0656.2000,N,07951.2000,E,0.00,0"
        r = p.parse_tracker(msg)
        self.assertTrue(r["valid_fix"])
        self.assertIsNone(r["timestamp"])

    def test_rejects_non_tracker_and_bad_imei(self):
        with self.assertRaises(p.Gps103ParseError):
            p.parse_tracker("hello")
        with self.assertRaises(p.Gps103ParseError):
            p.parse_tracker("imei:abc,tracker,,,L,")

    def test_short_message_does_not_crash(self):
        r = p.parse_tracker("imei:359586015829802")
        self.assertFalse(r["valid_fix"])


if __name__ == "__main__":
    unittest.main()