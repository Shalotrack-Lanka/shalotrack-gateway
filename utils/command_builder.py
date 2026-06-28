def build_command(command: str) -> bytes:
    return command.encode("ascii")


def build_where():
    return build_command("WHERE#")


def build_reset():
    return build_command("RESET#")


def build_relay_on():
    return build_command("RELAY,1#")


def build_relay_off():
    return build_command("RELAY,0#")