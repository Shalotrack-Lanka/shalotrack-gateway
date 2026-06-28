from decoders.alarm_decoder import decode_alarm_configuration
from decoders.device_settings_decoder import decode_device_settings
from decoders.geofence_decoder import decode_geofence
from decoders.gps_decoder import decode_gps_configuration
from decoders.network_decoder import decode_network_configuration
from decoders.phone_decoder import decode_phone_numbers
from decoders.sim_decoder import decode_sim_information
from decoders.upload_decoder import decode_upload_mode

def decode_information(values: dict):

    configuration = {}

    configuration["upload_mode"] = decode_upload_mode(
        values.get("DYD")
    )

    configuration["geofence"] = decode_geofence(
        values.get("FENCE")
    )

    configuration.update(
        decode_phone_numbers(values)
    )

    configuration["network"] = decode_network_configuration(
        values
    )

    configuration["device_settings"] = decode_device_settings(
        values
    )

    configuration["sim"] = decode_sim_information(
        values
    )

    configuration["gps"] = decode_gps_configuration(
        values
    )

    configuration["alarm_configuration"] = decode_alarm_configuration(
        values
    )

    return configuration