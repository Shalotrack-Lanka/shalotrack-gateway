import os

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "9000"))
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "300"))
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "500"))
LOW_BATTERY_THRESHOLD = int(os.getenv("LOW_BATTERY_THRESHOLD", "2"))
OVERSPEED_THRESHOLD = int(os.getenv("OVERSPEED_THRESHOLD", "80"))