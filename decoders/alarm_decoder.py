#to decode the alarm configurations
def decode_alarm_configuration(values):

    alm1 = int(values.get("ALM1", "00"), 16)
    alm2 = int(values.get("ALM2", "00"), 16)
    alm3 = int(values.get("ALM3", "00"), 16)

    return {
        "alarm_group_1": decode_alarm_group_1(alm1),
        "alarm_group_2": decode_alarm_group_2(alm2),
        "alarm_group_3": decode_alarm_group_3(alm3)
    }

# decoding the alarm groups
def decode_alarm_group_1(value):

    return {
        "sos_alarm": bool(value & 0x01),
        "power_cut_alarm": bool(value & 0x02),
        "shock_alarm": bool(value & 0x04),
        "low_battery_alarm": bool(value & 0x08),
        "overspeed_alarm": bool(value & 0x10),
        "geofence_alarm": bool(value & 0x20),
        "move_alarm": bool(value & 0x40),
        "acc_alarm": bool(value & 0x80),
    }

def decode_alarm_group_2(value):

    return {
        "bit0": bool(value & 0x01),
        "bit1": bool(value & 0x02),
        "bit2": bool(value & 0x04),
        "bit3": bool(value & 0x08),
        "bit4": bool(value & 0x10),
        "bit5": bool(value & 0x20),
        "bit6": bool(value & 0x40),
        "bit7": bool(value & 0x80),
    }

def decode_alarm_group_3(value):

    return {
        "bit0": bool(value & 0x01),
        "bit1": bool(value & 0x02),
        "bit2": bool(value & 0x04),
        "bit3": bool(value & 0x08),
        "bit4": bool(value & 0x10),
        "bit5": bool(value & 0x20),
        "bit6": bool(value & 0x40),
        "bit7": bool(value & 0x80),
    }