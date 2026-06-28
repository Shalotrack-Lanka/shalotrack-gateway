def decode_information(values: dict):
    configuration = {}

    #Calling tge decode uplaod mode function below
    configuration["upload_mode"] = decode_upload_mode(
        values.get("DYD")
    )
    #Calling tge decode geofence function below
    configuration["geofence"] = decode_geofence(
        values.get("FENCE")
    )
    
    configuration["sos_number"] = values.get("SOS")
    
    configuration["center_number"] = values.get("CENTER")
    
    #Calling the decode alarm configuration function below
    configuration["alaram_configuration"] = decode_alarm_configuration(
        values
    )

    return configuration

# to decode the uplaod mode
def decode_upload_mode(value):
    upload_modes = {
        "00": "Always Upload", 
        "01": "Smart Upload",
        "02": "Sleep Upload", 
        "03": "ACC Trigger Upload" 
    }

    return upload_modes.get(value, value)
    
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

#to decode the alarm configurations
 