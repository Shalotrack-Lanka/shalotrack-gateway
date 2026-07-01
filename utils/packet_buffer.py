def extract_packets(buffer: bytes):
    """
    Extract complete GT06 packets from a TCP stream buffer.

    Returns:
        packets: list[bytes]
        remaining_buffer: bytes
    """

    packets = []

    while True:

        if len(buffer) < 5:
            break

        # -----------------------
        # Short Packet (7878)
        # -----------------------

        if buffer.startswith(b"\x78\x78"):

            length = buffer[2]

            total = length + 5

            if len(buffer) < total:
                break

            packets.append(buffer[:total])

            buffer = buffer[total:]

            continue

        # -----------------------
        # Long Packet (7979)
        # -----------------------

        elif buffer.startswith(b"\x79\x79"):

            length = int.from_bytes(
                buffer[2:4],
                "big"
            )

            total = length + 6

            if len(buffer) < total:
                break

            packets.append(buffer[:total])

            buffer = buffer[total:]

            continue

        # Invalid data

        else:

            buffer = buffer[1:]

    return packets, buffer