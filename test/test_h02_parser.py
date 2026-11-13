"""
Unit tests for parsers/h02_parser.py and the H02 detection rule.

Pure functions, no sockets, no database:

    python -m unittest discover -s test -p "test_h02_parser.py" -v

Anchoring: every sample frame below is a real capture published in the
reference implementation's test suite (Traccar H02ProtocolDecoderTest). Where
that suite also states the expected position, the expected values here are
those, so a decoder bug cannot hide behind a test written from the same
misunderstanding. The binary frame and the text frame of device 2020916012
are two independent encodings of the SAME position and must agree.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                    # noqa: E402
from parsers import h02_parser as p                              # noqa: E402
from protocols import detect_protocol, GT06, GPS103, TELTONIKA, H02   # noqa: E402

V1 = "*HQ,4210051415,V1,164549,A,0956.3869,N,08406.7068,W,000.00,000,221215,FFFFFBFF#"
BIN_SHORT = bytes.fromhex("2441060116601245431311165035313006004318210e000000fffffbffff0024")
BIN_BOGOTA = bytes.fromhex(
    "24410600082621532131081504419390060740418306000000fffffbfdff0015060000002c02dc0c000000001f")
BIN_LONG_ID = bytes.fromhex(
    "2435248308419329301047591808172627335900074412294E024138FEFFFFFFFF01120064BA73005ECC")
BIN_FIXLESS = bytes.fromhex("2410307310010503162209022212874500113466574C014028fffffbffff0000")
TEXT_FIXLESS_TWIN = "*TH,2020916012,V4,S17,130305,050316,A,2212.8745,N,11346.6574,E,14.28,028,220902,FFFFFBFF#"


def parse(text):
    return p.parse_frame(text.encode() if isinstance(text, str) else text)


class TextPositionTests(unittest.TestCase):
    def test_plain_v1_matches_the_published_expectation(self):
        m = parse(V1)
        self.assertEqual((m.device_id, m.msg_type, m.kind, m.fix), ("4210051415", "V1", p.POSITION, True))
        self.assertAlmostEqual(m.latitude, 9.93978, places=5)
        self.assertAlmostEqual(m.longitude, -84.11178, places=5)        # W is negative
        self.assertEqual(m.timestamp, datetime(2015, 12, 22, 16, 45, 49, tzinfo=timezone.utc))
        self.assertEqual(m.status, 0xFFFFFBFF)

    def test_minutes_only_longitude_below_one_degree(self):
        # "9.9068,W" = 0 deg 9.9068 min west.
        m = parse("*HQ,1234567890,V8,112806,A,3832.7803,N,9.9068,W,64.14,67,310826,FFFFF9FF,214,3,18106,9261854,20,21,126,0#")
        self.assertAlmostEqual(m.latitude, 38.54634, places=5)
        self.assertAlmostEqual(m.longitude, -0.16511, places=5)
        self.assertEqual(m.timestamp, datetime(2026, 8, 31, 11, 28, 6, tzinfo=timezone.utc))

    def test_dashed_coordinates_and_southern_western_hemispheres(self):
        m = parse("*HQ,1451316409,V1,030149,A,-23-29.0095,S,-46-51.5852,W,2.4,065,070315,FFFFFFFF#")
        self.assertAlmostEqual(m.latitude, -23.48349, places=5)
        self.assertAlmostEqual(m.longitude, -46.85975, places=5)
        self.assertEqual(m.timestamp, datetime(2015, 3, 7, 3, 1, 49, tzinfo=timezone.utc))

    def test_coordinates_without_a_decimal_point(self):
        m = parse("*HQ,5905101893,V1,105759,A,37573392,S,145037022,E,000.00,173,280122,FF7FFBFF,,,9059e2c,8232,4#")
        self.assertAlmostEqual(m.latitude, -(37 + 57.3392 / 60), places=6)
        self.assertAlmostEqual(m.longitude, 145 + 3.7022 / 60, places=6)

    def test_three_digit_minutes_prefix_is_split_correctly(self):
        # "701.8915" = 7 deg 01.8915 min.
        m = parse("*HQ,354188045498669,V1,195200,A,701.8915,S,3450.3399,W,0.00,205.70,050213,ffffffff,000243,000000,000000#")
        self.assertAlmostEqual(m.latitude, -(7 + 1.8915 / 60), places=6)
        self.assertAlmostEqual(m.longitude, -(34 + 50.3399 / 60), places=6)
        self.assertAlmostEqual(m.heading, 205.70)

    def test_battery_digit_before_the_date_is_skipped(self):
        m = parse("*HQ,4210209006,V1,054048,A,2828.2297,N,07733.4332,E,000.5,047,4,080918,EEE7FBDF,4261193,0#")
        self.assertEqual(m.timestamp, datetime(2018, 9, 8, 5, 40, 48, tzinfo=timezone.utc))
        self.assertEqual(m.status, 0xEEE7FBDF)

    def test_speed_is_converted_from_knots(self):
        m = parse("*HQ,353588020068342,V1,084436,A,3257.01525,N,00655.03865,W,57.78,40,011216,FFFBFFFF,25c,a, 154,b04c#")
        self.assertAlmostEqual(m.speed, 57.78 * 1.852, places=2)
        self.assertEqual(m.heading, 40.0)

    def test_speed_is_taken_as_kmh_when_configured(self):
        original = p.H02_SPEED_IN_KMH
        p.H02_SPEED_IN_KMH = True
        try:
            self.assertEqual(parse(V1.replace("000.00,000", "050.00,090")).speed, 50.0)
        finally:
            p.H02_SPEED_IN_KMH = original

    def test_command_response_wrapper_still_yields_the_position(self):
        m = parse(TEXT_FIXLESS_TWIN)
        self.assertEqual(m.msg_type, "V4")
        self.assertAlmostEqual(m.latitude, 22.21457, delta=1e-5)         # published value is rounded
        self.assertAlmostEqual(m.longitude, 113.77762, delta=1e-5)
        self.assertEqual(m.timestamp, datetime(2002, 9, 22, 5, 3, 16, tzinfo=timezone.utc))

    def test_vp1_variant_has_a_position_but_no_device_time(self):
        m = parse("*hq,356327080425330,VP1,A,2702.7245,S,15251.9311,E,0.48,0.0000,080917#")
        self.assertTrue(m.fix)
        self.assertAlmostEqual(m.latitude, -27.04541, places=5)
        self.assertIsNone(m.timestamp)            # the handler falls back to server time

    def test_numeric_coding_scheme_in_the_validity_slot_means_a_fix(self):
        m = parse("*HQ,353505221264507,V2,100220,0,5238.26259,N,00507.33983,E,0.25,0,280917,FFFFFFFF,cc,28,  db,d75b#")
        self.assertTrue(m.fix)
        self.assertAlmostEqual(m.latitude, 52 + 38.26259 / 60, places=6)

    def test_validity_v_and_blank_mean_no_fix_and_no_coordinates(self):
        for validity in ("V", ""):
            m = parse(f"*HQ,355488020119695,V1,050418,{validity},2827.61232,N,07703.84822,E,0.00,0,031015,FFFEFBFF#")
            self.assertFalse(m.fix, validity)
            self.assertIsNone(m.latitude)
            self.assertEqual(m.kind, p.POSITION)       # still a position-type report, still alive

    def test_zeroed_and_empty_fix_reports_are_alive_but_have_no_position(self):
        for frame in (
            "*HQ,353505510948929,V1,,V,,N,,E,0.00,0,,FFFFF7FF,f0,a,11a0,c0c6#",
            "*HQ,353588020068342,V1,000000,V,0.0000,0,0.0000,0,0.00,0.00,000000,ffffffff,000106,000002,000203,004c87,16#",
        ):
            m = parse(frame)
            self.assertFalse(m.fix)
            self.assertIsNone(m.latitude)

    def test_every_published_position_variant_decodes(self):
        for frame in (
            "*HQ,0000000000,V19,220606,V,4915.0978,N,01634.6187,E,000.00,000,220426,,,8942001180368594259F,BFFFFBFF#",
            "*HQ,3177718238,V6,002926,V,3514.4088,N,9733.2842,W,0.00,0.00,151222,FFF7FBFF,310,260,32936,13641,8944501311217563382F,#",
            "*HQ,9180271064,V5,091233,V,2348.8912,N,09021.3302,E,000.00,000,051219,FFFFBBFF,470,01,21019,2033,2921283#",
            "*HQ,353505221264507,VI1,075146,0,5238.25900,N,00507.33429,E,0.54,0,250917,FFFFFFFF,cc,28,  db,d75b#",
            "*HQ,4209951296,V19,214452,A,5201.0178,N,01830.5029,E,000.00,000,200417,,195.63.13.195,89480610500392633029,BFFFFBFF#",
            "*HQ,865205035331981,V1,132926,A,1935.3933,N,07920.4134,E,  3.34,342,280519,FFFFFFFF#",
            "*HQ,123456789012345,V1,155850,A,5214.5346,N,2117.4683,E,0.00,270.90,131012,ffffffff,000000,000000,000000,000000#",
        ):
            m = parse(frame)
            self.assertEqual(m.kind, p.POSITION, frame)
            self.assertIsNotNone(m.timestamp, frame)
        self.assertTrue(parse("*HQ,865205035331981,V1,132926,A,1935.3933,N,07920.4134,E,  3.34,342,280519,FFFFFFFF#").fix)

    def test_maker_code_is_kept_case_and_all(self):
        self.assertEqual(parse(TEXT_FIXLESS_TWIN).prefix, "TH")
        self.assertEqual(parse("*hq,356327080425330,VP1,A,2702.7245,S,15251.9311,E,0.48,0.0000,080917#").prefix, "HQ")


class OtherTextMessagesTests(unittest.TestCase):
    def test_heartbeat(self):
        m = parse("*HQ,135790246811220,HTBT,100#")
        self.assertEqual((m.kind, m.msg_type, m.device_id), (p.HEARTBEAT, "HTBT", "135790246811220"))
        self.assertEqual(parse("*HQ,135790246811220,HTBT#").kind, p.HEARTBEAT)
        self.assertEqual(parse("*HQ,135790246811220,V0,100#").kind, p.HEARTBEAT)

    def test_cell_tower_and_status_reports_are_alive_only(self):
        for frame in (
            "*HQ,355488020882405,V3,095426,74001,01,010278,045142,128,-92,02DE,0,X,090517,FFFFFBFF#",
            "*HQ,4109179024,NBR,103732,722,310,0,6,8106,32010,23,8101,22007,25,8106,12010,23,8106,22105,22,8101,22012,16,8106,42010,5,100217,FFFFFBFF,5#",
            "*HQ,1700086468,LINK,180902,15,0,84,0,0,240517,FFFFFBFF#",
            "*HQ,356327080425330,XT,1,100#",
        ):
            m = parse(frame)
            self.assertEqual(m.kind, p.OTHER, frame)
            self.assertFalse(m.fix)
            self.assertTrue(m.known)

    def test_unknown_type_is_other_and_flagged_unknown(self):
        m = parse("*HQ,4109198974,ZZ9,1,2#")
        self.assertEqual((m.kind, m.known), (p.OTHER, False))

    def test_unknown_v_type_is_still_tried_as_a_position_but_flagged(self):
        m = parse("*HQ,9001000002,V9,213945,A,3542.2043,N,38.6508,W,0.00,170,221025,FBFFF9FF,0,0,0,0,22,31,126,0#")
        self.assertEqual((m.kind, m.known, m.fix), (p.POSITION, False, True))

    def test_header_without_a_type_is_alive_only(self):
        self.assertEqual(parse("*HQ,4109198974,#").kind, p.OTHER)


class ReplyTests(unittest.TestCase):
    NOW = datetime(2026, 11, 13, 4, 5, 6, tzinfo=timezone.utc)

    def test_v1_is_acknowledged_with_server_utc_time(self):
        self.assertEqual(p.build_reply(parse(V1), self.NOW), b"*HQ,4210051415,V4,V1,20261113040506#")

    def test_v1_without_a_fix_is_acknowledged_too(self):
        m = parse("*HQ,353505510948929,V1,,V,,N,,E,0.00,0,,FFFFF7FF,f0,a,11a0,c0c6#")
        self.assertEqual(p.build_reply(m, self.NOW), b"*HQ,353505510948929,V4,V1,20261113040506#")

    def test_nbr_is_acknowledged(self):
        m = parse("*HQ,4109179024,NBR,103732,722,310,0,6,8106,32010,23,100217,FFFFFBFF,5#")
        self.assertEqual(p.build_reply(m, self.NOW), b"*HQ,4109179024,V4,NBR,20261113040506#")

    def test_heartbeat_is_echoed_up_to_the_type(self):
        self.assertEqual(p.build_reply(parse("*HQ,135790246811220,HTBT,100#")), b"*HQ,135790246811220,HTBT#")
        self.assertEqual(p.build_reply(parse("*hq,135790246811220,V0,100#")), b"*hq,135790246811220,V0#")

    def test_other_messages_and_binary_frames_get_no_reply(self):
        self.assertIsNone(p.build_reply(parse("*HQ,356327080425330,XT,1,100#")))
        self.assertIsNone(p.build_reply(parse("*HQ,355488020882405,V3,095426,74001,01,010278,045142,128,-92,02DE,0,X,090517,FFFFFBFF#")))
        self.assertIsNone(p.build_reply(parse(BIN_SHORT)))


class StatusWordTests(unittest.TestCase):
    def test_normal_status_words(self):
        self.assertEqual(p.decode_status(0xFFFFFBFF), (False, None))     # idle: bit 10 clear
        self.assertEqual(p.decode_status(0xFFFFFFFF), (True, None))      # bit 10 set: ignition on

    def test_a_cleared_bit_is_an_alarm(self):
        self.assertEqual(p.decode_status(0xFFFFFBFE)[1], "VIBRATION")    # bit 0
        self.assertEqual(p.decode_status(0xFFFFFBFD)[1], "SOS")          # bit 1
        self.assertEqual(p.decode_status(0xFFFBFBFF)[1], "SOS")          # bit 18
        self.assertEqual(p.decode_status(0xFFFFFBFB)[1], "OVERSPEED")    # bit 2
        self.assertEqual(p.decode_status(0xFFF7FBFF)[1], "POWER_CUT")    # bit 19

    def test_first_matching_alarm_wins(self):
        self.assertEqual(p.decode_status(0xFFFFFBF8)[1], "VIBRATION")


class BinaryTests(unittest.TestCase):
    def test_matches_the_published_expectation(self):
        m = parse(BIN_BOGOTA)
        self.assertEqual((m.form, m.device_id, m.fix), (p.BINARY, "4106000826", True))
        self.assertAlmostEqual(m.latitude, 4.69898, places=5)
        self.assertAlmostEqual(m.longitude, -74.06972, places=5)         # W: east flag clear
        self.assertEqual(m.timestamp, datetime(2015, 8, 31, 21, 53, 21, tzinfo=timezone.utc))

    def test_binary_and_text_encodings_of_the_same_position_agree(self):
        text, binary = parse(TEXT_FIXLESS_TWIN), parse(BIN_FIXLESS)
        self.assertAlmostEqual(binary.latitude, text.latitude, places=6)
        self.assertAlmostEqual(binary.longitude, text.longitude, places=6)
        self.assertEqual(binary.timestamp, text.timestamp)
        self.assertAlmostEqual(binary.speed, text.speed, delta=0.6)      # 14 vs 14.28 knots
        self.assertEqual(binary.heading, text.heading)
        self.assertFalse(binary.fix)                                     # valid flag clear in this frame

    def test_short_and_long_id_forms(self):
        self.assertEqual(parse(BIN_SHORT).device_id, "4106011660")
        long_form = parse(BIN_LONG_ID)
        self.assertEqual(long_form.device_id, "352483084193293")         # first 15 digits of 8 bytes
        self.assertTrue(long_form.fix)
        self.assertAlmostEqual(long_form.latitude, 26.4556, places=4)

    def test_status_word_is_read(self):
        self.assertEqual(parse(BIN_BOGOTA).status, 0xFFFFFBFD)

    def test_non_bcd_digits_are_rejected(self):
        bad = bytearray(BIN_SHORT)
        bad[7] = 0xAB                                                    # inside the time field
        with self.assertRaises(p.H02ParseError):
            parse(bytes(bad))

    def test_impossible_date_is_rejected(self):
        bad = bytearray(BIN_SHORT)
        bad[10], bad[11] = 0x32, 0x13                                    # day 32, month 13
        with self.assertRaises(p.H02ParseError):
            parse(bytes(bad))

    def test_minutes_of_60_or_more_are_rejected(self):
        bad = bytearray(BIN_BOGOTA)
        bad[13] = 0x61                                                   # latitude minutes 61.9390
        with self.assertRaises(p.H02ParseError):
            parse(bytes(bad))
        bad = bytearray(BIN_BOGOTA)
        bad[18] = 0x46                                                   # longitude minutes 64.1830
        with self.assertRaises(p.H02ParseError):
            parse(bytes(bad))

    def test_truncated_binary_is_rejected(self):
        with self.assertRaises(p.H02ParseError):
            parse(BIN_SHORT[:20])


class MalformedTextTests(unittest.TestCase):
    def test_garbage_is_rejected_not_guessed(self):
        for frame in (b"hello#", b"*#", b"*HQ#", b"*HQ,#", b"*H,123,V1#", b"*HQ,abc,V1,x#",
                      b"*HQ,,V1,173212,A,2225.78879,S,02829.19021,E,0.00,0,290418,FFFFFBFF#",
                      b"*HQ,\xff\xfe,V1#", b"*HQ,123456789,V1#"):
            with self.assertRaises(p.H02ParseError, msg=frame):
                p.parse_frame(frame)

    def test_minutes_of_60_or_more_are_not_a_position(self):
        m = parse("*HQ,4210051415,V1,164549,A,0960.0000,N,08406.7068,W,000.00,000,221215,FFFFFBFF#")
        self.assertFalse(m.fix)

    def test_impossible_date_gives_no_timestamp(self):
        m = parse("*HQ,4210051415,V1,164549,A,0956.3869,N,08406.7068,W,000.00,000,321315,FFFFFBFF#")
        self.assertIsNone(m.timestamp)

    def test_decoder_never_raises_anything_but_parse_error(self):
        import random
        rng = random.Random(7)
        seeds = [V1.encode(), BIN_SHORT, BIN_LONG_ID, TEXT_FIXLESS_TWIN.encode()]
        for _ in range(3000):
            data = bytearray(rng.choice(seeds))
            for _ in range(rng.randint(1, 6)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            try:
                p.parse_frame(bytes(data))
            except p.H02ParseError:
                pass


class FramingTests(unittest.TestCase):
    def test_text_frames_split_and_crlf_between_them_is_skipped(self):
        frames, rest = p.extract_frames(V1.encode() + b"\r\n" + b"*HQ,135790246811220,HTBT#\n" + b"*HQ,1357")
        self.assertEqual(frames, [V1.encode(), b"*HQ,135790246811220,HTBT#"])
        self.assertEqual(rest, b"*HQ,1357")

    def test_text_frame_split_across_reads(self):
        data = V1.encode()
        frames, rest = p.extract_frames(data[:30])
        self.assertEqual((frames, rest), ([], data[:30]))
        frames, rest = p.extract_frames(rest + data[30:])
        self.assertEqual((frames, rest), ([data], b""))

    def test_single_binary_frame_of_each_known_length(self):
        for blob in (BIN_SHORT, BIN_BOGOTA, BIN_LONG_ID):
            frames, rest = p.extract_frames(blob)
            self.assertEqual((frames, rest), ([blob], b""), len(blob))

    def test_two_coalesced_short_binary_frames(self):
        frames, rest = p.extract_frames(BIN_SHORT + BIN_SHORT)
        self.assertEqual((frames, rest), ([BIN_SHORT, BIN_SHORT], b""))

    def test_binary_frame_followed_by_text(self):
        frames, rest = p.extract_frames(BIN_SHORT + V1.encode())
        self.assertEqual(frames, [BIN_SHORT, V1.encode()])

    def test_partial_binary_frame_waits(self):
        frames, rest = p.extract_frames(BIN_BOGOTA[:40])
        self.assertEqual((frames, rest), ([], BIN_BOGOTA[:40]))
        frames, rest = p.extract_frames(BIN_BOGOTA[:20])
        self.assertEqual((frames, rest), ([], BIN_BOGOTA[:20]))

    def test_unframeable_binary_data_is_an_error(self):
        with self.assertRaises(p.H02FrameError):
            p.extract_frames(b"$" + b"\x11" * 80)

    def test_unexpected_start_byte_is_an_error(self):
        with self.assertRaises(p.H02FrameError):
            p.extract_frames(b"GET / HTTP/1.1\r\n\r\n")
        with self.assertRaises(p.H02FrameError):
            p.extract_frames(V1.encode() + b"\x00\x01")

    def test_unterminated_text_is_capped(self):
        with self.assertRaises(p.H02FrameError):
            p.extract_frames(b"*HQ," + b"1" * (config.H02_MAX_UNFRAMED_BYTES + 10))
        frames, rest = p.extract_frames(b"*HQ," + b"1" * 100)        # under the cap: just waits
        self.assertEqual(frames, [])


class DetectionTests(unittest.TestCase):
    ALL = frozenset({"GT06", "GPS103", "TELTONIKA", "H02"})

    def test_text_and_binary_are_detected(self):
        for first in (b"*HQ,4210051415,V1", b"*hq,356327080425330,VP1", b"*TH,2020916012,V4", BIN_SHORT):
            self.assertEqual(detect_protocol(first, self.ALL), H02, first[:10])

    def test_other_families_are_unchanged(self):
        self.assertEqual(detect_protocol(b"\x78\x78\x0d\x01", self.ALL), GT06)
        self.assertEqual(detect_protocol(b"##,imei:359586015829802,A;", self.ALL), GPS103)
        self.assertEqual(detect_protocol(b"imei:359586015829802,tracker", self.ALL), GPS103)
        self.assertEqual(detect_protocol(b"\x00\x0f352093081452251", self.ALL), TELTONIKA)

    def test_look_alikes_are_not_h02(self):
        for first in (b"**,imei:359586015829802,E;", b"*H,12345", b"*12,12345", b"*HQ", b"GET /", b"\x00\x01"):
            self.assertIsNone(detect_protocol(first, self.ALL), first)

    def test_h02_is_off_unless_enabled(self):
        self.assertIsNone(detect_protocol(V1.encode(), frozenset({"GT06", "GPS103"})))
        self.assertNotIn("H02", config.ENABLED_PROTOCOLS)               # default stays as before


if __name__ == "__main__":
    unittest.main()
