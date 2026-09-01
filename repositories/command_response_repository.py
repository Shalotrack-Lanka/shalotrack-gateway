import json
from database import managed_connection
from models.command_response import CommandResponse


class CommandResponseRepository:

    @staticmethod
    def save(response: CommandResponse):
        with managed_connection() as conn:
            cursor = conn.cursor()
            # Strip null bytes — V5 device responses contain 0x00 terminators
            # which PostgreSQL rejects in text columns
            raw = response.raw_response.replace('\x00', '') if response.raw_response else ''
            cursor.execute(
                """
                INSERT INTO "CommandResponses"
                ("DeviceId", "Command", "RawResponse", "ParsedData")
                VALUES (%s, %s, %s, %s)
                """,
                (
                    response.device_id,
                    response.command,
                    raw,
                    json.dumps(response.parsed_data)
                )
            )
            conn.commit()
            cursor.close()