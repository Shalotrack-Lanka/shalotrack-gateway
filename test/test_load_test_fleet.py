"""
Checks on the load-test tooling itself, so a bad simulator cannot produce
numbers nobody should trust:
  * its packets pass the gateway's own parsers (CRC, coordinates, speed);
  * it refuses production hosts and unknown public hosts;
  * the seeder only treats loopback/private hosts as local;
  * the SLO verdict flips when latency or errors are bad.

    python -m unittest discover -s test -p "test_load_test_fleet.py" -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import load_test_fleet as lt                                   # noqa: E402
from bench_seed import host_is_local, imei_for                 # noqa: E402
from parsers import v5_parser                                  # noqa: E402
from parsers import gps103_parser as g103                      # noqa: E402


class PacketTests(unittest.TestCase):
    def test_crc_matches_the_published_x25_test_vector(self):
        # CRC-16/X-25 of "123456789" is 0x906E (catalogue of CRC algorithms). Checking
        # against a fixed value, not against our own function, so a wrong polynomial fails.
        self.assertEqual(lt.crc16_x25(b"123456789"), 0x906E)

    def test_gt06_frames_are_well_formed_with_valid_crc(self):
        imei = imei_for(7)
        for frame in (lt.gt06_login(imei, 1), lt.gt06_location(2, 6.9, 79.86, 30, 90),
                      lt.gt06_heartbeat(3, True)):
            self.assertEqual(frame[:2], b"\x78\x78")
            self.assertEqual(frame[-2:], b"\r\n")
            self.assertEqual(len(frame), frame[2] + 5)
            crc = lt.crc16_x25(frame[2:-4])
            self.assertEqual(frame[-4:-2], crc.to_bytes(2, "big"))

    def test_location_decodes_to_what_was_sent(self):
        loc = v5_parser.parse_location_packet(lt.gt06_location(5, 6.912345, 79.861234, 42, 123))
        self.assertAlmostEqual(loc["latitude"], 6.912345, places=4)
        self.assertAlmostEqual(loc["longitude"], 79.861234, places=4)
        self.assertEqual(loc["speed"], 42)

    def test_heartbeat_ignition_bit(self):
        self.assertTrue(v5_parser.parse_heartbeat_packet(lt.gt06_heartbeat(1, True))["ignition_status"])
        self.assertFalse(v5_parser.parse_heartbeat_packet(lt.gt06_heartbeat(1, False))["ignition_status"])

    def test_gps103_position_is_a_valid_fix_the_gateway_accepts(self):
        imei = imei_for(2500)
        data = g103.parse_tracker(lt.gps103_position(imei, 6.9, 79.86, 12.5).decode())
        self.assertEqual(data["imei"], imei)
        self.assertTrue(data["valid_fix"])
        self.assertAlmostEqual(data["latitude"], 6.9, places=2)
        self.assertAlmostEqual(data["longitude"], 79.86, places=2)

    def test_gps103_login_and_heartbeat_use_the_real_wire_forms(self):
        imei = imei_for(2500)
        self.assertEqual(lt.gps103_login(imei), f"##,imei:{imei},A;".encode())
        # through the gateway's OWN framer: a heartbeat must come out as one complete message
        messages, rest = g103.extract_messages(lt.gps103_heartbeat(imei))
        self.assertEqual(messages, [imei.encode()])
        self.assertEqual(rest, b"")
        self.assertEqual(g103.parse_heartbeat(messages[0].decode()), {"imei": imei})


class SafetyTests(unittest.TestCase):
    def test_production_names_are_always_refused(self):
        for host in ("gateway.shalotrack.com", "GATEWAY.ShaloTrack.com", "x.shalotrack.internal"):
            for allow in (False, True):
                with self.assertRaises(SystemExit, msg=f"{host} allow={allow}"):
                    lt.refuse_unsafe_target(host, allow)

    def test_public_hosts_need_the_flag(self):
        with self.assertRaises(SystemExit):
            lt.refuse_unsafe_target("47.131.72.131", False)
        lt.refuse_unsafe_target("47.131.72.131", True)          # an explicit opt-in is honoured

    def test_loopback_and_private_are_fine(self):
        for host in ("127.0.0.1", "localhost", "10.0.3.237", "192.168.1.20"):
            lt.refuse_unsafe_target(host, False)

    def test_seeder_only_trusts_local_databases(self):
        self.assertTrue(host_is_local("postgresql://u:p@127.0.0.1:5433/db"))
        self.assertTrue(host_is_local("postgresql://u:p@10.0.4.5:5432/db"))
        self.assertFalse(host_is_local("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres"))
        self.assertFalse(host_is_local("postgresql://u:p@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"))

    def test_test_imeis_cannot_collide_with_real_ranges(self):
        self.assertEqual(imei_for(1), "355172100000001")
        self.assertEqual(len(imei_for(25000)), 15)


class VerdictTests(unittest.TestCase):
    def cfg(self):
        return SimpleNamespace(devices=10, scenario="typical", gt06_count=8, slo_p99_ms=100,
                               slo_max_error_pct=0.5, json_out=None)

    def stats(self, lat_ms, errors=0):
        s = lt.Stats()
        s.peak_connected = 10
        s.sent = 1000
        for v in lat_ms:
            s.observe("gt06_position", v)
        if errors:
            s.errors["timeout"] = errors
        return s

    def test_pass(self):
        self.assertEqual(lt.summarise(self.cfg(), self.stats([5] * 100), 60), 0)

    def test_slow_acks_fail(self):
        self.assertEqual(lt.summarise(self.cfg(), self.stats([5] * 90 + [500] * 10), 60), 1)

    def test_errors_fail(self):
        self.assertEqual(lt.summarise(self.cfg(), self.stats([5] * 100, errors=20), 60), 1)

    def test_nothing_connected_fails(self):
        s = self.stats([5] * 10)
        s.peak_connected = 0
        self.assertEqual(lt.summarise(self.cfg(), s, 60), 1)

    def test_percentile(self):
        self.assertEqual(lt.pct(list(range(1, 101)), 50), 51)
        self.assertEqual(lt.pct(list(range(1, 101)), 99), 99)
        self.assertTrue(lt.pct([], 50) != lt.pct([], 50))       # NaN


if __name__ == "__main__":
    unittest.main()
