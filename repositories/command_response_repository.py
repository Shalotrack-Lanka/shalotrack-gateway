import json

from database import get_db_connection
from models.command_response import CommandResponse


class CommandResponseRepository:

    @staticmethod
    def save(response: CommandResponse):

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO "CommandResponses"
            (
                "DeviceId",
                "Command",
                "RawResponse",
                "ParsedData"
            )

            VALUES
            (
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                response.device_id,
                response.command,
                response.raw_response,
                json.dumps(response.parsed_data)
            )
        )

        conn.commit()

        cursor.close()
        conn.close()