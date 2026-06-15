connected_devices = {}


def register_device(imei, ip):

    connected_devices[imei] = {
        "ip": ip
    }


def get_device(imei):
    return connected_devices.get(imei)