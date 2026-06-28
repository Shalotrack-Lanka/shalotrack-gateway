# to decode the uplaod mode
def decode_upload_mode(value):
    upload_modes = {
        "00": "Always Upload", 
        "01": "Smart Upload",
        "02": "Sleep Upload", 
        "03": "ACC Trigger Upload" 
    }

    return upload_modes.get(value, value)