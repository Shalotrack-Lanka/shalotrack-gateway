from datetime import datetime, timezone
from database import get_db_connection

class RawPacketRepository:
    @staticmethod
    def save(
        device_id: str,
        protocol_number: str,
        raw_hex: str,
        parsed: bool
    ) -> None:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO "RawPackets"
            (
                "DeviceId",
                "ProtocolNumber",
                "RawHex",
                "PacketLength",
                "ReceivedAt",
                "Parsed"
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                device_id,
                protocol_number,
                raw_hex,
                len(raw_hex) // 2,
                datetime.now(timezone.utc),
                parsed
            )
        )

        conn.commit()

        cursor.close()
        conn.close()