import json
from datetime import datetime, timezone

from database import managed_connection


async def create_event(
        device_id,
        event_type,
        severity,
        vehicle_id=None,
        latitude=None,
        longitude=None,
        raw_packet_id=None,
        description=None,
        metadata=None
):
    # asyncpg binds parameters strictly by type — unlike psycopg2, it will
    # not silently stringify an IntEnum/Enum for a text column. EventType
    # and Severity are both Enums upstream (Severity is an IntEnum), so
    # normalise them to plain str here, once, for every caller.
    event_type_value = event_type.value if hasattr(event_type, "value") else event_type
    severity_value = str(severity.value) if hasattr(severity, "value") else str(severity)

    async with managed_connection() as conn:
        await conn.execute(
            """
            INSERT INTO "DeviceEvents" (
                "DeviceId",
                "VehicleId",
                "EventType",
                "Severity",
                "Latitude",
                "Longitude",
                "RawPacketId",
                "Description",
                "Metadata",
                "CreatedAt"
            ) VALUES
            (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
            )
            """,
            device_id,
            vehicle_id,
            event_type_value,
            severity_value,
            latitude,
            longitude,
            raw_packet_id,
            description,
            json.dumps(metadata) if metadata else None,
            datetime.now(timezone.utc)
        )