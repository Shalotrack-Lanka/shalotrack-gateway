import json
from database import managed_connection
from models.command_response import CommandResponse


class CommandResponseRepository:

    @staticmethod
    async def save(response: CommandResponse):
        async with managed_connection() as conn:
            # Strip null bytes — V5 device responses contain 0x00 terminators
            # which PostgreSQL rejects in text columns
            raw = response.raw_response.replace('\x00', '') if response.raw_response else ''
            await conn.execute(
                """
                INSERT INTO "CommandResponses"
                ("DeviceId", "Command", "RawResponse", "ParsedData")
                VALUES ($1, $2, $3, $4)
                """,
                response.device_id,
                response.command,
                raw,
                json.dumps(response.parsed_data)
            )