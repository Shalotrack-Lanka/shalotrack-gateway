from services.command_service import (
    send_where,
    reboot,
    cut_engine,
    resume_engine
)


def start_console():
    print("\n===== ShaloTrack Command Console =====")
    print("Commands:")
    print("where <imei>")
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

            elif action == "reset":
                reboot(imei)

            elif action == "relay_on":
                resume_engine(imei)

            elif action == "relay_off":
                cut_engine(imei)

            else:
                print("Unknown command")

        except Exception as ex:

            print(ex)