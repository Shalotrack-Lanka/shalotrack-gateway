import socket
import os
import threading

from services.packet_handler import process_packet
from utils.logger import log

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))


def handle_device(conn, addr):

    log(f"✅ Device connected: {addr}")

    try:

        conn.settimeout(60)

        while True:

            data = conn.recv(1024)

            if not data:
                break

            log(f"RAW DATA: {data.hex()}")

            process_packet(
                data,
                conn,
                addr
            )

    except socket.timeout:

        log("⏱ Connection timeout")

    except Exception as ex:

        log(f"❌ Device error: {ex}")

    finally:

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

    server.bind((HOST, PORT))

    server.listen(5)

    log(f"🚀 TCP Server listening on port {PORT}")

    while True:

        conn, addr = server.accept()

        client_thread = threading.Thread(
            target=handle_device,
            args=(conn, addr)
        )

        client_thread.daemon = True

        client_thread.start()


if __name__ == "__main__":
    start_server()