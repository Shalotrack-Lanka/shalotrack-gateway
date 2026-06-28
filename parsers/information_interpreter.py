def interpret_information(values: dict) -> dict:
    config = {}

    config["alarm_1"] = values.get("ALM1")
    config["alarm_2"] = values.get("ALM2")
    config["alarm_3"] = values.get("ALM3")

    config["status"] = values.get("STA1")

    config["upload_mode"] = values.get("DYD")

    config["sos_number"] = values.get("SOS")

    config["center_number"] = values.get("CENTER")

    config["geofence"] = values.get("FENCE")

    return config