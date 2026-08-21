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

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))

MAX_CONNECTIONS = int(os.environ.get("MAX_CONNECTIONS", 500))
_connection_semaphore = threading.Semaphore(MAX_CONNECTIONS)


def handle_device(conn, addr):
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

    log(f"🚀 TCP Server listening on port {PORT}")
    log(f"⚙️  Connection timeout : {CONNECTION_TIMEOUT}s")
    log(f"⚙️  Max connections    : {MAX_CONNECTIONS}")

    while True:
        conn, addr = server.accept()
        client_thread = threading.Thread(
            target=handle_device,
            args=(conn, addr),
            daemon=True
        )
        client_thread.start()


if __name__ == "__main__":
    start_server()