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
# Each connection = 1 OS thread (~8MB stack).
# t3.small (2GB RAM): safe up to ~200 devices.
# t3.medium (4GB RAM): safe up to ~400 devices.
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "500"))

# Seconds to wait for first byte before treating a connection as an
# NLB health check probe. Real devices always send login immediately.
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "1.0"))

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

# ================================================================
# OTel
# ================================================================
# Maximum milliseconds to wait for OTel export before giving up.
# Critical: prevents OTel from blocking gateway threads when the
# collector is slow or unreachable (e.g. Tempo OOM crash).
OTEL_EXPORT_TIMEOUT_MILLIS = int(os.getenv("OTEL_EXPORT_TIMEOUT_MILLIS", "2000"))

# How often to push metrics to the collector (milliseconds).
OTEL_METRIC_INTERVAL_MILLIS = int(os.getenv("OTEL_METRIC_INTERVAL_MILLIS", "10000"))