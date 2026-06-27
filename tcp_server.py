import socket
import os
import threading

from services.packet_handler import process_packet
from services.device_registry import (
    get_device,
    unregister_device
)

from services.tracking_service import (
    set_device_offline
)

from utils.logger import log

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))


def handle_device(conn, addr):

    log(f"✅ Device connected: {addr}")

    imei = None

    try:

        conn.settimeout(60)

        while True:

            data = conn.recv(1024)

            if not data:
                break

            log(f"RAW DATA: {data.hex()}")

            imei = process_packet(
                data,
                conn,
                addr
            )

    except socket.timeout:

        log("⏱ Connection timeout")

    except Exception as ex:

        log(f"❌ Device error: {ex}")

    finally:

        if imei:

            device = get_device(imei)

            if device:

                set_device_offline(
                    device["device_id"]
                )

                unregister_device(
                    imei
                )

                log("📴 Device marked Offline")

        conn.close()

        log("🔌 Device disconnected")


def start_server():

    server = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    server.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    server.bind(
        (
            HOST,
            PORT
        )
    )

    server.listen(5)

    log(
        f"🚀 TCP Server listening on port {PORT}"
    )

    while True:

        conn, addr = server.accept()

        thread = threading.Thread(
            target=handle_device,
            args=(conn, addr)
        )

        thread.daemon = True

        thread.start()


if __name__ == "__main__":

    start_server()