#to decode the sos and center number
def decode_phone_numbers(value):
    return{
        "sos_number": value.get("SOS"),
        "center_number": value.get("CENTER")
    }    