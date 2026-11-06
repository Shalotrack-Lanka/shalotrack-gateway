"""
Decides whether a packet's raw hex is written to the RawPackets table.

Why: every packet of every type used to be stored forever. At fleet scale that
table grows faster than GpsTrackings and doubles the number of writes per
packet. See RAW_PACKET_MODE in config.py.

`always=True` is for packets that are rare and valuable for debugging or
forensics (login, alarms/SOS, information/config, command replies, unknown
packets). Routine traffic (position, status, heartbeat) is sampled.
"""

import random

import config


def should_store_raw(always: bool = False) -> bool:
    mode = config.RAW_PACKET_MODE
    if mode == "off":
        return False
    if mode == "all":
        return True
    # "selective" (and any unrecognised value falls back to it)
    if always:
        return True
    return random.random() < config.RAW_PACKET_SAMPLE_RATE
