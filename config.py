import os

# ================================================================
# Network
# ================================================================
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "9000"))

# Seconds before an idle TCP connection is closed.
# V5 stopped-vehicle interval is 300s — set equal to avoid
# unnecessary reconnects on parked vehicles.
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "300"))

# Maximum simultaneous device connections.
# asyncio connections are coroutines, not OS threads — the RAM ceiling
# that applied under the old threading model (one thread, ~8MB stack,
# per device) no longer applies. The DB_POOL_MAX below is now the real
# constraint on concurrent *active* devices.
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "500"))

# Seconds to wait for first byte before treating a connection as an
# NLB health check probe. Real devices always send login immediately.
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "1.0"))

# ================================================================
# GPS103 (TK103A/B) text protocol
# ================================================================
# The GPS103 position report carries speed in KNOTS on every firmware we have
# documentation for (Traccar's decoder passes the field through as knots).
# Some clones send km/h instead. Verify on the first real unit by comparing
# against the vehicle speedometer, and flip this to "true" if it reads low
# by a factor of ~1.85.
GPS103_SPEED_IN_KMH = os.getenv("GPS103_SPEED_IN_KMH", "false").lower() == "true"

# A GPS103 message ends with ';'. If this many bytes arrive with no ';' the
# stream is garbage (or an attack): the connection is dropped instead of
# buffering forever.
GPS103_MAX_UNFRAMED_BYTES = int(os.getenv("GPS103_MAX_UNFRAMED_BYTES", "2048"))

# ================================================================
# Device families
# ================================================================
# Which wire protocols this gateway accepts on its single port. A family that
# is not listed is treated like unknown traffic: the connection is closed
# ("Unrecognised protocol"). Default is exactly what ran before, so deploying
# a new release never switches a new family on by itself. To enable Teltonika:
#     ENABLED_PROTOCOLS=GT06,GPS103,TELTONIKA
# Names are case-insensitive; unknown names are ignored (and logged at start).
_ALL_PROTOCOLS = ("GT06", "GPS103", "TELTONIKA")
ENABLED_PROTOCOLS_REQUESTED = tuple(
    n.strip().upper()
    for n in os.getenv("ENABLED_PROTOCOLS", "GT06,GPS103").split(",")
    if n.strip()
)
ENABLED_PROTOCOLS = frozenset(n for n in ENABLED_PROTOCOLS_REQUESTED if n in _ALL_PROTOCOLS)

# ================================================================
# Teltonika (FMB series, Codec 8 / 8E over TCP)
# ================================================================
# Largest AVL "data field length" accepted. Teltonika devices cap a packet at
# 1280 bytes; anything claiming more is garbage or an attack and the
# connection is dropped instead of buffering it.
TELTONIKA_MAX_PACKET_BYTES = int(os.getenv("TELTONIKA_MAX_PACKET_BYTES", "4096"))

# A record stamped further in the future than this is not stored (a device
# with a broken clock would otherwise freeze the live position at a time that
# never arrives).
TELTONIKA_MAX_FUTURE_SECONDS = int(os.getenv("TELTONIKA_MAX_FUTURE_SECONDS", "600"))

# ================================================================
# Database
# ================================================================
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "5"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "150"))

# ================================================================
# Cost / load controls (all safe defaults: behaviour only changes when an
# env var is set, except RAW_PACKET_MODE, see below)
# ================================================================
# RawPackets stores the hex of packets forever. Storing EVERY packet makes
# that table outgrow GpsTrackings, so by default only the packets that matter
# for debugging are kept:
#   all       -> every packet (old behaviour)
#   selective -> always: login, alarms/SOS, information/config, command
#                replies, unknown packets.  Routine packets (position, status,
#                heartbeat) are sampled at RAW_PACKET_SAMPLE_RATE.
#   off       -> nothing is stored
RAW_PACKET_MODE = os.getenv("RAW_PACKET_MODE", "selective").strip().lower()
try:
    RAW_PACKET_SAMPLE_RATE = min(1.0, max(0.0, float(os.getenv("RAW_PACKET_SAMPLE_RATE", "0.02"))))
except ValueError:
    RAW_PACKET_SAMPLE_RATE = 0.02

# When "true", a position that was already stored (same DeviceId + EventTime,
# e.g. a retransmission after a reconnect) is silently ignored instead of
# creating a duplicate row. REQUIRES the unique index on
# GpsTrackings(DeviceId, EventTime) to exist first, see docs/gps-tracking-idempotency.md.
# Leave "false" until that index is in the database.
GPS_TRACKING_IDEMPOTENT = os.getenv("GPS_TRACKING_IDEMPOTENT", "false").strip().lower() == "true"

# DEBUG (default, same output as before) | INFO | WARNING | ERROR.
# Switch to INFO once there are hundreds of devices: per-packet detail lines
# (about 12 per position packet) are what cost CPU and log-ingestion money.
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").strip().upper()

# ================================================================
# Business logic thresholds
# ================================================================
LOW_BATTERY_THRESHOLD = int(os.getenv("LOW_BATTERY_THRESHOLD", "2"))
OVERSPEED_THRESHOLD = int(os.getenv("OVERSPEED_THRESHOLD", "80"))