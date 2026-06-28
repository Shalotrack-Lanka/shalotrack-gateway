#to decode the geofence mode
def decode_geofence(value):

    if not value:
        return None

    parts = value.split(",")

    if len(parts) < 8:
        return value

    return {
        "enabled": parts[1] == "ON",
        "radius": int(parts[2]),
        "latitude": float(parts[3]),
        "longitude": float(parts[4]),
        "trigger": parts[6],
        "index": int(parts[7])
    }