#to decode following device settings
def decode_device_settings(values):

    return {
        "timezone": values.get("GMT"),
        "language": values.get("LANG"),
        "work_mode": values.get("MODE"),
        "acc_mode": values.get("ACC"),
        "sleep_mode": values.get("SLEEP"),
        "led_mode": values.get("LED"),
        "heartbeat_interval": values.get("HEART"),
        "upload_interval": values.get("UPLOAD"),
    }