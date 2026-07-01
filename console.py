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


def start_console():
    print("\n===== ShaloTrack Command Console =====")
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
                send_where(imei)

            elif action == "status":
                send_status(imei)

            elif action == "version":
                send_version(imei)

            elif action == "params":
                send_params(imei)

            elif action == "imei":
                send_imei(imei)

            elif action == "reset":
                send_reset(imei)

            elif action == "relay_on":
                send_relay_on(imei)

            elif action == "relay_off":
                send_relay_off(imei)

            else:
                print("Unknown command")

        except Exception as ex:

            print(ex)