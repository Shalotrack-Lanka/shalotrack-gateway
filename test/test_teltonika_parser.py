"""
Unit tests for parsers/teltonika_parser.py and the Teltonika detection rule.

Pure functions, no sockets, no database:

    python -m unittest discover -s test -p "test_teltonika_parser.py" -v
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parsers import teltonika_parser as p                        # noqa: E402
from protocols import detect_protocol, GT06, GPS103, TELTONIKA   # noqa: E402
from teltonika_fixtures import build_avl_packet, encode_record, login_frame  # noqa: E402

IMEI = "352093081452251"

# The Codec 8 example published in Teltonika's protocol documentation.
PUBLISHED_CODEC8 = bytes.fromhex(
    "000000000000003608010000016B40D8EA30010000000000000000000000000000000105"
    "021503010101425E0F01F10000601A014E0000000000000000010000C7CF"
)

T0 = 1_762_500_000_000   # an arbitrary millisecond timestamp


class PublishedExampleTests(unittest.TestCase):
    def test_crc_matches_the_published_value(self):
        # Independent of any code under test except the CRC routine itself:
        # the packet carries 0xC7CF, so a wrong CRC implementation fails here.
        self.assertEqual(p.crc16_ibm(PUBLISHED_CODEC8[8:-4]), 0xC7CF)

    def test_decodes_the_published_packet(self):
        packet = p.parse_avl_packet(PUBLISHED_CODEC8)
        self.assertEqual(packet.codec, p.CODEC_8)
        self.assertEqual(len(packet.records), 1)
        rec = packet.records[0]
        self.assertEqual(rec.timestamp, datetime(2019, 6, 10, 10, 4, 46, tzinfo=timezone.utc))
        self.assertEqual(rec.priority, 1)
        self.assertEqual((rec.latitude, rec.longitude), (0.0, 0.0))
        self.assertEqual(rec.satellites, 0)
        self.assertFalse(rec.has_fix)
        self.assertEqual(rec.io, {21: 3, 1: 1, 66: 0x5E0F, 241: 0x601A, 78: 0})


class RecordDecodingTests(unittest.TestCase):
    def test_codec8_position_and_io(self):
        rec_bytes = encode_record(
            T0, lat=6.9271, lon=79.8612, altitude=12, angle=169, satellites=9, speed=42,
            io1={239: 1, 240: 1, 21: 4}, io2={66: 12500}, io4={16: 123456}, io8={78: 99},
        )
        rec = p.parse_avl_packet(build_avl_packet([rec_bytes])).records[0]
        self.assertAlmostEqual(rec.latitude, 6.9271, places=6)
        self.assertAlmostEqual(rec.longitude, 79.8612, places=6)
        self.assertEqual((rec.altitude, rec.angle, rec.satellites, rec.speed), (12, 169, 9, 42))
        self.assertTrue(rec.has_fix)
        self.assertIs(rec.ignition, True)
        self.assertIs(rec.movement, True)
        self.assertEqual(rec.io[66], 12500)
        self.assertEqual(rec.io[16], 123456)
        self.assertEqual(rec.io[78], 99)
        self.assertEqual(rec.timestamp, datetime.fromtimestamp(T0 / 1000, tz=timezone.utc))

    def test_negative_coordinates_are_twos_complement(self):
        rec_bytes = encode_record(T0, lat=-33.8688, lon=-70.6693, altitude=-5, satellites=7)
        rec = p.parse_avl_packet(build_avl_packet([rec_bytes])).records[0]
        self.assertAlmostEqual(rec.latitude, -33.8688, places=6)
        self.assertAlmostEqual(rec.longitude, -70.6693, places=6)
        self.assertEqual(rec.altitude, -5)

    def test_codec8e_uses_wide_ids_and_variable_length_group(self):
        rec_bytes = encode_record(
            T0, lat=6.9271, lon=79.8612, satellites=8, speed=10, event_io=0x0100,
            io1={239: 0}, io2={0x0201: 7}, iox={0x0301: b"\xde\xad\xbe\xef"}, extended=True,
        )
        packet = p.parse_avl_packet(build_avl_packet([rec_bytes], codec=0x8E))
        self.assertEqual(packet.codec, p.CODEC_8E)
        rec = packet.records[0]
        self.assertEqual(rec.event_io_id, 0x0100)
        self.assertIs(rec.ignition, False)
        self.assertEqual(rec.io[0x0201], 7)
        self.assertEqual(rec.io[0x0301], b"\xde\xad\xbe\xef")

    def test_several_records_keep_their_order(self):
        recs = [encode_record(T0 + i * 1000, lat=6.9, lon=79.8, satellites=6, speed=i) for i in range(5)]
        parsed = p.parse_avl_packet(build_avl_packet(recs)).records
        self.assertEqual([r.speed for r in parsed], [0, 1, 2, 3, 4])

    def test_no_fix_markers(self):
        no_sats = p.parse_avl_packet(build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=0)])).records[0]
        at_origin = p.parse_avl_packet(build_avl_packet([encode_record(T0, lat=0, lon=0, satellites=5)])).records[0]
        self.assertFalse(no_sats.has_fix)
        self.assertFalse(at_origin.has_fix)


class RejectionTests(unittest.TestCase):
    def good(self):
        return bytearray(build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)]))

    def test_corrupted_byte_fails_the_crc(self):
        frame = self.good()
        frame[20] ^= 0xFF
        with self.assertRaises(p.TeltonikaCrcError):
            p.parse_avl_packet(bytes(frame))

    def test_length_field_that_disagrees_with_the_frame(self):
        frame = self.good()
        frame[7] += 1
        with self.assertRaises(p.TeltonikaParseError):
            p.parse_avl_packet(bytes(frame))

    def test_unsupported_codec_is_reported_as_such(self):
        frame = build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)], codec=0x10)
        with self.assertRaises(p.UnsupportedCodecError) as ctx:
            p.parse_avl_packet(frame)
        self.assertEqual(ctx.exception.codec, 0x10)

    def test_record_count_trailer_must_match(self):
        frame = build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)], count_trailer=2)
        with self.assertRaises(p.TeltonikaParseError):
            p.parse_avl_packet(frame)

    def test_record_that_overruns_the_data_field(self):
        # Claims 3 records but only carries one.
        body = bytes([0x08, 3]) + encode_record(T0, lat=6.9, lon=79.8, satellites=6) + bytes([3])
        frame = b"\x00\x00\x00\x00" + len(body).to_bytes(4, "big") + body + p.crc16_ibm(body).to_bytes(4, "big")
        with self.assertRaises(p.TeltonikaParseError):
            p.parse_avl_packet(frame)

    def test_not_an_avl_packet(self):
        with self.assertRaises(p.TeltonikaParseError):
            p.parse_avl_packet(b"\x00\x0f" + IMEI.encode())


class FramingTests(unittest.TestCase):
    def test_login_then_data_in_one_buffer(self):
        data = build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)])
        frames, rest = p.extract_frames(login_frame(IMEI) + data)
        self.assertEqual(frames, [login_frame(IMEI), data])
        self.assertEqual(rest, b"")

    def test_byte_by_byte_delivery(self):
        stream = login_frame(IMEI) + build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)])
        buffer, frames = b"", []
        for byte in stream:
            buffer += bytes([byte])
            got, buffer = p.extract_frames(buffer)
            frames += got
        self.assertEqual(len(frames), 2)
        self.assertTrue(p.is_login_frame(frames[0]))
        self.assertEqual(buffer, b"")

    def test_two_packets_back_to_back_and_a_partial_third(self):
        one = build_avl_packet([encode_record(T0, lat=6.9, lon=79.8, satellites=6)])
        frames, rest = p.extract_frames(one + one + one[:15])
        self.assertEqual(len(frames), 2)
        self.assertEqual(rest, one[:15])

    def test_oversize_length_is_rejected_before_buffering(self):
        header = b"\x00\x00\x00\x00" + (10_000_000).to_bytes(4, "big")
        with self.assertRaises(p.TeltonikaFrameError):
            p.extract_frames(header)

    def test_zero_length_is_rejected(self):
        with self.assertRaises(p.TeltonikaFrameError):
            p.extract_frames(b"\x00\x00\x00\x00\x00\x00\x00\x00")

    def test_garbage_is_rejected(self):
        with self.assertRaises(p.TeltonikaFrameError):
            p.extract_frames(b"GET / HTTP/1.1\r\n")

    def test_login_with_non_digit_imei_is_rejected(self):
        with self.assertRaises(p.TeltonikaFrameError):
            p.extract_frames(b"\x00\x0f" + b"35209308145225X")

    def test_login_parses(self):
        self.assertEqual(p.parse_login(login_frame(IMEI)), IMEI)
        with self.assertRaises(p.TeltonikaParseError):
            p.parse_login(b"\x00\x0f12345")


class AckTests(unittest.TestCase):
    def test_ack_is_a_four_byte_big_endian_count(self):
        self.assertEqual(p.ack(0), b"\x00\x00\x00\x00")
        self.assertEqual(p.ack(2), b"\x00\x00\x00\x02")
        self.assertEqual(p.ack(300), b"\x00\x00\x01\x2c")


class DetectionTests(unittest.TestCase):
    ALL = frozenset({GT06, GPS103, TELTONIKA})

    def test_teltonika_login_is_detected_when_enabled(self):
        self.assertEqual(detect_protocol(login_frame(IMEI), enabled=self.ALL), TELTONIKA)

    def test_teltonika_is_off_when_not_enabled(self):
        self.assertIsNone(detect_protocol(login_frame(IMEI), enabled=frozenset({GT06, GPS103})))

    def test_default_set_leaves_teltonika_off(self):
        # Deploying a release must not switch a new family on by itself.
        self.assertIsNone(detect_protocol(login_frame(IMEI)))

    def test_existing_families_unchanged(self):
        self.assertEqual(detect_protocol(b"\x78\x78\x0d\x01", enabled=self.ALL), GT06)
        self.assertEqual(detect_protocol(b"##,imei:359586015829802,A;", enabled=self.ALL), GPS103)
        self.assertEqual(detect_protocol(b"imei:359586015829802,tracker", enabled=self.ALL), GPS103)

    def test_disabling_an_existing_family_closes_it_off(self):
        self.assertIsNone(detect_protocol(b"##,imei:1;", enabled=frozenset({GT06})))

    def test_lookalikes_are_not_teltonika(self):
        self.assertIsNone(detect_protocol(b"\x00\x0fnot-an-imei....", enabled=self.ALL))
        self.assertIsNone(detect_protocol(b"\x00\x10" + IMEI.encode(), enabled=self.ALL))
        self.assertIsNone(detect_protocol(b"", enabled=self.ALL))


if __name__ == "__main__":
    unittest.main()
