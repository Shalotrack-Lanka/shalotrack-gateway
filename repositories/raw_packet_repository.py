from datetime import datetime, timezone
from database import managed_connection


class RawPacketRepository:
    @staticmethod
    async def save(device_id: str, protocol_number: str, raw_hex: str, parsed: bool) -> None:
        async with managed_connection() as conn:
            await conn.execute(
                """
                INSERT INTO "RawPackets"
                ("DeviceId","ProtocolNumber","RawHex","PacketLength","ReceivedAt","Parsed")
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                device_id, protocol_number, raw_hex, len(raw_hex) // 2, datetime.now(timezone.utc), parsed
            )