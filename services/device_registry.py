connected_devices = {}


def register_device(
    imei,
    ip,
    device_id
):

    connected_devices[imei] = {

        "device_id": device_id,

        "ip": ip

    }


def get_device(imei):

    return connected_devices.get(
        imei
    )


def unregister_device(imei):

    if imei in connected_devices:

        del connected_devices[imei]