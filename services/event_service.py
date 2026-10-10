import json
from datetime import datetime, timezone

from database import managed_connection
from utils.numeric import clean_coordinates


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
    # asyncpg binds parameters strictly by type. EventType is TEXT in the real
    # schema, so normalising it to a plain str is correct. Severity is SMALLINT
    # in the real schema (confirmed via information_schema against production,
    # 2026-10-10) — it must stay an int, not be stringified. An earlier version
    # of this function stringified both, which broke every login/alarm/info
    # event write in production because asyncpg refused to bind a str to a
    # smallint column. Do not "fix" this back to str without checking the real
    # column type first.
    event_type_value = event_type.value if hasattr(event_type, "value") else event_type
    severity_value = int(severity.value) if hasattr(severity, "value") else int(severity)

    # Latitude/Longitude are `numeric` columns. A raw Python float is bound by
    # asyncpg as its 50+ digit binary expansion, which Postgres stores happily
    # and the C# API (decimal, ~28 digits) crashes on when it reads the row
    # back — the 2026-10-10 dashboard outage. Clean at the write boundary so no
    # caller (alarm handler today, anything tomorrow) can forget.
    # If coordinates are missing/invalid we still record the event with a NULL
    # position: an alarm (e.g. SOS) must never be dropped over a bad GPS fix.
    latitude, longitude = clean_coordinates(latitude, longitude)

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