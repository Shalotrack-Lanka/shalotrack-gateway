import asyncio
import os

from config import CONNECTION_TIMEOUT, MAX_CONNECTIONS, PROBE_TIMEOUT
from database import init_pool, close_pool
from services.packet_handler import process_packet
from services.gps103_handler import process_gps103_message
from services.device_registry import (
    get_device,
    get_imei_by_socket,
    mark_packet_received,
    unregister_device,
    update_last_seen,
)
from protocols import GT06, GPS103, detect_protocol
from services.tracking_service import set_device_offline
from utils.logger import log
from console import start_console
from utils.packet_buffer import extract_packets
from parsers.gps103_parser import extract_messages
from command_api import start_command_api_thread

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 9000))

# One entry per device family sharing this port: (framer, handler).
#   framer(buffer)                  -> (list_of_frames, remaining_buffer)
#   handler(frame, writer, addr)    -> imei once authenticated, else None
# The family is chosen once per connection from its first bytes
# (protocols.detect_protocol). To add a family, add a row here.
PROTOCOL_PIPELINES = {
    GT06:   (extract_packets, process_packet),
    GPS103: (extract_messages, process_gps103_message),
}

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

        # TCP may hand over the first segment in tiny pieces. The shortest
        # protocol signature is 5 bytes ("imei:"), so top up before sniffing
        # rather than misclassify (and close) a legitimate device.
        while len(data) < 5:
            try:
                more = await asyncio.wait_for(reader.read(4096), timeout=PROBE_TIMEOUT)
            except asyncio.TimeoutError:
                break
            if not more:
                break
            data += more

        protocol = detect_protocol(data)
        if protocol is None:
            # Not GT06, not GPS103. Don't guess and don't hold the slot open.
            log(f"🚫 Unrecognised protocol from {addr} — closing. First bytes: {data[:32].hex()}")
            return

        log(f"✅ Device connected: {addr} [{protocol}]")
        frame_extractor, handle_frame = PROTOCOL_PIPELINES[protocol]
        buffer = data

        while True:
            packets, buffer = frame_extractor(buffer)

            for packet in packets:
                # A handler may close the socket mid-batch (rejected login,
                # IMEI mismatch). Don't keep feeding it the rest of the batch.
                if writer.is_closing():
                    break

                log(f"RAW DATA: {packet.hex()}")
                packet_imei = await handle_frame(packet, writer, addr)

                if packet_imei:
                    imei = packet_imei
                    update_last_seen(imei)
                    # Gateway-wide "something valid is arriving" timestamp --
                    # feeds the silence check in services/health_service.py.
                    mark_packet_received()

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
        # Fall back to the registry if a handler registered the device but
        # then raised before returning its IMEI (otherwise the entry leaks).
        cleanup_imei = imei or get_imei_by_socket(writer)
        if cleanup_imei:
            device = get_device(cleanup_imei)
            # Only tear down our OWN registration. If this IMEI already
            # reconnected on a new socket (common on flaky GPRS, before the
            # old connection times out), the entry now belongs to that new
            # connection: marking it offline / unregistering it would knock a
            # healthy device offline.
            if device and device["socket"] is writer:
                await set_device_offline(device["device_id"])
                unregister_device(cleanup_imei)
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