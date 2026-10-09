import asyncio
import os

from config import CONNECTION_TIMEOUT, MAX_CONNECTIONS, PROBE_TIMEOUT
from database import init_pool, close_pool
from services.packet_handler import process_packet
from services.device_registry import (
    get_device,
    unregister_device,
    update_last_seen,
)
from services.tracking_service import set_device_offline
from utils.logger import log
from console import start_console
from utils.packet_buffer import extract_packets
from command_api import start_command_api_thread

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))

# ---------------------------------------------------------------------------
# Connection cap — asyncio equivalent of the old threading.Semaphore.
# A plain counter + lock, since asyncio.Semaphore has no non-blocking
# "try acquire" the way threading.Semaphore does.
# ---------------------------------------------------------------------------
_connection_count = 0
_connection_lock = asyncio.Lock()


async def _try_acquire_slot() -> bool:
    global _connection_count
    async with _connection_lock:
        if _connection_count >= MAX_CONNECTIONS:
            return False
        _connection_count += 1
        return True


async def _release_slot() -> None:
    global _connection_count
    async with _connection_lock:
        _connection_count -= 1


async def handle_device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """
    Handle one GPS device connection for its entire lifetime.

    NLB health-check probe suppression:
    The AWS NLB sends a bare TCP connect every 10 seconds to check gateway
    health. These probes connect, send no data, and close. asyncio streams
    have no MSG_PEEK equivalent, so detection works by reading with a short
    timeout: a real device always sends a login packet immediately, a probe
    sends nothing. Whatever is read this way is the first real data and is
    fed straight into the packet buffer — nothing is thrown away.
    """
    addr = writer.get_extra_info("peername") or ("unknown", 0)

    if not await _try_acquire_slot():
        log(f"⚠️ Connection cap ({MAX_CONNECTIONS}) reached — rejecting {addr}")
        writer.close()
        return

    imei = None
    buffer = b""

    try:
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=PROBE_TIMEOUT)
        except asyncio.TimeoutError:
            return  # probe — no data within PROBE_TIMEOUT
        if not data:
            return  # probe — connection closed with no data

        log(f"✅ Device connected: {addr}")
        buffer = data

        while True:
            packets, buffer = extract_packets(buffer)

            for packet in packets:
                log(f"RAW DATA: {packet.hex()}")
                packet_imei = await process_packet(packet, writer, addr)

                if packet_imei:
                    imei = packet_imei
                    update_last_seen(imei)

            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=CONNECTION_TIMEOUT)
            except asyncio.TimeoutError:
                log(f"⏱ Connection timeout after {CONNECTION_TIMEOUT}s — {addr}")
                break

            if not data:
                break

            buffer += data

    except (ConnectionResetError, asyncio.IncompleteReadError) as ex:
        log(f"❌ Device error: {ex}")

    except Exception as ex:
        log(f"❌ Device error: {ex}")

    finally:
        if imei:
            device = get_device(imei)
            if device:
                await set_device_offline(device["device_id"])
                unregister_device(imei)
                log("📴 Device marked Offline")

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        await _release_slot()
        log(f"🔌 Device disconnected: {addr}")


async def main() -> None:
    await init_pool()

    loop = asyncio.get_running_loop()
    start_command_api_thread(loop)
    loop.run_in_executor(None, start_console, loop)

    # backlog: OS-level queue for connections accepted but not yet handed to
    # handle_device. The old threading version explicitly called
    # server.listen(512) -- asyncio.start_server()'s default backlog is only
    # 100, which drops connections under a reconnect storm (e.g. every
    # device reconnecting at once after a network blip or gateway restart)
    # well before MAX_CONNECTIONS ever gets a chance to reject them cleanly.
    server = await asyncio.start_server(handle_device, HOST, PORT, backlog=2048)

    log(f"🚀 TCP Server listening on port {PORT}")
    log(f"⚙️  Connection timeout : {CONNECTION_TIMEOUT}s")
    log(f"⚙️  Max connections    : {MAX_CONNECTIONS}")
    log(f"⚙️  Probe timeout      : {PROBE_TIMEOUT}s")

    try:
        async with server:
            await server.serve_forever()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())