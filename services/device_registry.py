from datetime import datetime, timezone

connected_devices = {}


def register_device(
    imei,
    ip,
    device_id,
    conn
):
    connected_devices[imei] = {
        "device_id": device_id,
        "ip": ip,
        "socket": conn,
        "connected_at": datetime.now(timezone.utc),
        "last_seen": datetime.now(timezone.utc)
    }


def get_imei_by_socket(conn):

    print(f"LOOKUP SOCKET: {id(conn)}")

    for imei, device in connected_devices.items():

        print(f"REGISTERED SOCKET: {id(device['socket'])} -> {imei}")

        if device["socket"] == conn:
            return imei

    return None


def get_device(imei):
    return connected_devices.get(imei)


def unregister_device(imei):
    connected_devices.pop(imei, None)


def update_last_seen(imei):

    device = connected_devices.get(imei)

    if device:
        device["last_seen"] = datetime.now(timezone.utc)


def get_socket(imei):

    device = connected_devices.get(imei)

    if not device:
        return None

    return device["socket"]


def is_online(imei):
    return imei in connected_devices


def get_all_devices():
    return connected_devices