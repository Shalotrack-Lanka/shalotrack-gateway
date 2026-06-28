#to decode the network configuration
def decode_network_configuration(values):

    return {
        "apn": values.get("APN"),
        "apn_username": values.get("APNUSER"),
        "apn_password": values.get("APNPWD"),
        "server_ip": values.get("IP"),
        "server_domain": values.get("DOMAIN"),
        "server_port": values.get("PORT"),
        "dns": values.get("DNS"),
        "protocol": values.get("PROTOCOL"),
    }