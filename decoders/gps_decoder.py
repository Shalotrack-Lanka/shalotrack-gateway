#to decode gps configurations
def decode_gps_configuration(values):

    return {
        "filter": values.get("FILTER"),
        "accuracy": values.get("GPSACC"),
        "interval": values.get("INTERVAL"),
    }

