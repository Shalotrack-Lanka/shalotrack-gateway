import socket
import os
import threading

from telemetry import telemetry as tel
from config import CONNECTION_TIMEOUT
from services.packet_handler import process_packet
from services.device_registry import (
    get_device,
    unregister_device,
    update_last_seen
)
from services.tracking_service import set_device_offline
from utils.logger import log
from console import start_console
from utils.packet_buffer import extract_packets
from command_api import start_command_api_thread

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))

MAX_CONNECTIONS = int(os.environ.get("MAX_CONNECTIONS", 500))
_connection_semaphore = threading.Semaphore(MAX_CONNECTIONS)

# ---------------------------------------------------------------------------
# NLB health check probe suppression
#
# The AWS NLB sends a bare TCP connect every 10 seconds to port 9000 to
# check gateway health. These probes connect, send no data, and close.
# They flood the logs with connect/disconnect noise and consume a thread
# slot for their brief lifetime.
#
# The peek-based detection reads the first byte without consuming it.
# If no data arrives within PROBE_TIMEOUT seconds, it's a probe — close
# silently. Real GPS devices always send a login packet immediately.
# ---------------------------------------------------------------------------
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "1.0"))

# Known NLB probe source IPs (VPC internal) — add more if needed
_PROBE_IPS = {"10.0.3.173", "10.0.4.173"}


def _is_probe(conn, addr) -> bool:
    """
    Detect NLB health check probes before spawning a handler thread.
    Returns True if the connection sends no data within PROBE_TIMEOUT.
    """
    ip = addr[0]

    # Fast path — known probe IPs
    if ip in _PROBE_IPS:
        conn.settimeout(PROBE_TIMEOUT)
        try:
            data = conn.recv(1, socket.MSG_PEEK)
            if not data:
                return True
        except socket.timeout:
            return True
        except Exception:
            return True
        return False

    # Unknown IP — peek briefly
    conn.settimeout(PROBE_TIMEOUT)
    try:
        data = conn.recv(1, socket.MSG_PEEK)
        if not data:
            return True
    except socket.timeout:
        # No data in PROBE_TIMEOUT — treat as probe
        return True
    except Exception:
        return True

    conn.settimeout(None)  # Reset for real device handling
    return False


def handle_device(conn, addr):
    """Handle one GPS device connection for its entire lifetime."""

    if not _connection_semaphore.acquire(blocking=False):
        log(f"⚠️ Connection cap ({MAX_CONNECTIONS}) reached — rejecting {addr}")
        conn.close()
        return

    log(f"✅ Device connected: {addr}")
    tel.active_connections.add(1)

    imei = None
    buffer = b""

    try:
        conn.settimeout(CONNECTION_TIMEOUT)

        while True:
            data = conn.recv(4096)
            if not data:
                break

            buffer += data
            packets, buffer = extract_packets(buffer)

            for packet in packets:
                log(f"RAW DATA: {packet.hex()}")
                packet_imei = process_packet(packet, conn, addr)

                if packet_imei:
                    imei = packet_imei
                    update_last_seen(imei)

    except socket.timeout:
        log(f"⏱ Connection timeout after {CONNECTION_TIMEOUT}s — {addr}")

    except Exception as ex:
        log(f"❌ Device error: {ex}")

    finally:
        if imei:
            device = get_device(imei)
            if device:
                set_device_offline(device["device_id"])
                unregister_device(imei)
                log("📴 Device marked Offline")

        conn.close()
        tel.active_connections.add(-1)
        _connection_semaphore.release()
        log("🔌 Device disconnected")


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(512)

    threading.Thread(target=start_console, daemon=True).start()
    start_command_api_thread()

    log(f"🚀 TCP Server listening on port {PORT}")
    log(f"⚙️  Connection timeout : {CONNECTION_TIMEOUT}s")
    log(f"⚙️  Max connections    : {MAX_CONNECTIONS}")
    log(f"⚙️  Probe timeout      : {PROBE_TIMEOUT}s")

    while True:
        conn, addr = server.accept()

        # Silently discard NLB health check probes
        if _is_probe(conn, addr):
            conn.close()
            continue

        client_thread = threading.Thread(
            target=handle_device,
            args=(conn, addr),
            daemon=True
        )
        client_thread.start()


if __name__ == "__main__":
    start_server()