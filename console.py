import asyncio
import sys
import time
from services.command_service import (
    send_where,
    send_status,
    send_version,
    send_params,
    send_imei,
    send_reset,
    send_relay_on,
    send_relay_off
)


def start_console(loop: asyncio.AbstractEventLoop):
    """
    Blocking console loop — runs in a background thread (via
    loop.run_in_executor), not on the event loop itself, since input()
    blocks. Commands are async (they write to a device's socket), so each
    one is scheduled onto the event loop and we block this thread until
    it completes.
    """

    def run(coro, timeout: float = 5.0):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)

    print("\n===== ShaloTrack Command Console =====")

    if not sys.stdin.isatty():
        print("Headless environment detected. Console input disabled.")
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            print("Shutting down gateway background process...")
        return

    print("Commands:")
    print("where <imei>")
    print("status <imei>")
    print("version <imei>")
    print("params <imei>")
    print("imei <imei>")
    print("reset <imei>")
    print("relay_on <imei>")
    print("relay_off <imei>")
    print("exit")

    while True:
        try:
            command = input("> ").strip()
            if not command:
                continue

            if command == "exit":
                break

            parts = command.split()

            if len(parts) != 2:
                print("Invalid command")
                continue

            action, imei = parts

            if action == "where":
                run(send_where(imei))
            elif action == "status":
                run(send_status(imei))
            elif action == "version":
                run(send_version(imei))
            elif action == "params":
                run(send_params(imei))
            elif action == "imei":
                run(send_imei(imei))
            elif action == "reset":
                run(send_reset(imei))
            elif action == "relay_on":
                run(send_relay_on(imei))
            elif action == "relay_off":
                run(send_relay_off(imei))
            else:
                print("Unknown command")

        except Exception as ex:
            print(ex)