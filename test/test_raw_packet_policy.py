"""Unit tests for the RawPackets storage policy (services/raw_packet_policy.py)."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                              # noqa: E402
from services.raw_packet_policy import should_store_raw    # noqa: E402


class RawPacketPolicyTests(unittest.TestCase):
    def test_all_mode_keeps_everything(self):
        with patch.object(config, "RAW_PACKET_MODE", "all"), patch.object(config, "RAW_PACKET_SAMPLE_RATE", 0.0):
            self.assertTrue(should_store_raw(False))
            self.assertTrue(should_store_raw(True))

    def test_off_mode_keeps_nothing(self):
        with patch.object(config, "RAW_PACKET_MODE", "off"):
            self.assertFalse(should_store_raw(True))
            self.assertFalse(should_store_raw(False))

    def test_selective_always_keeps_important_packets(self):
        with patch.object(config, "RAW_PACKET_MODE", "selective"), patch.object(config, "RAW_PACKET_SAMPLE_RATE", 0.0):
            self.assertTrue(should_store_raw(True))

    def test_selective_samples_routine_packets_at_the_configured_rate(self):
        with patch.object(config, "RAW_PACKET_MODE", "selective"):
            with patch.object(config, "RAW_PACKET_SAMPLE_RATE", 0.0):
                self.assertFalse(any(should_store_raw(False) for _ in range(500)))
            with patch.object(config, "RAW_PACKET_SAMPLE_RATE", 1.0):
                self.assertTrue(all(should_store_raw(False) for _ in range(500)))
            with patch.object(config, "RAW_PACKET_SAMPLE_RATE", 0.5), patch("services.raw_packet_policy.random.random", side_effect=[0.1, 0.9]):
                self.assertTrue(should_store_raw(False))
                self.assertFalse(should_store_raw(False))

    def test_unknown_mode_falls_back_to_selective(self):
        with patch.object(config, "RAW_PACKET_MODE", "banana"), patch.object(config, "RAW_PACKET_SAMPLE_RATE", 0.0):
            self.assertTrue(should_store_raw(True))
            self.assertFalse(should_store_raw(False))


if __name__ == "__main__":
    unittest.main()
