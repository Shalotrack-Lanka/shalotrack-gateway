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
# Database
# ================================================================
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "5"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "150"))

# ================================================================
# Business logic thresholds
# ================================================================
LOW_BATTERY_THRESHOLD = int(os.getenv("LOW_BATTERY_THRESHOLD", "2"))
OVERSPEED_THRESHOLD = int(os.getenv("OVERSPEED_THRESHOLD", "80"))