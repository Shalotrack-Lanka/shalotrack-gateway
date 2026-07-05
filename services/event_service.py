import json
from datetime import datetime, timezone

from database import get_db_connection

def create_event(
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
    
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
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
                %s,
                %s,
                %s,
                %s,
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
            vehicle_id,
            event_type,
            severity,
            latitude,
            longitude,
            raw_packet_id,
            description,
            json.dumps(metadata) if metadata else None,
            datetime.now(timezone.utc)
        )
    )

    conn.commit()
    cursor.close()
    conn.close()