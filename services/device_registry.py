connected_devices = {}

def register_device(
    imei,
    ip,
    device_id
):

    connected_devices[imei] = {
        "ip": ip,
        "device_id": device_id
    }


def get_device(imei):
    return connected_devices.get(imei)