#to decode sim configurations
def decode_sim_information(values):

    return {
        "iccid": values.get("ICCID"),
        "imsi": values.get("IMSI"),
        "operator": values.get("OP"),
        "phone_number": values.get("PHONE"),
    }

