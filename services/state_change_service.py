from datetime import datetime, timezone

from database import get_db_connection

from models.device_status import DeviceStatus


class StatusRepository:


    @staticmethod
    def find_by_device(device_id: str) -> DeviceStatus | None:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                "DeviceId",
                "IsOnline",
                "BatteryLevel",
                "GpsSignal",
                "IgnitionStatus",
                "MovementStatus",
                "PowerStatus",
                "LastHeartbeat",
                "LastSeen"
            FROM "DeviceStatuses"
            WHERE "DeviceId" = %s
            LIMIT 1
            """,
            (device_id,)
        )

        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if result is None:
            return None

        return DeviceStatus(
            device_id=result[0],
            is_online=result[1],
            battery_level=result[2],
            gps_signal=result[3],
            ignition_status=result[4],
            movement_status=result[5],
            power_status=result[6],
            last_heartbeat=result[7],
            last_seen=result[8]
        )


    @staticmethod
    def save(status: DeviceStatus):

        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)

        cursor.execute(
            """
            INSERT INTO "DeviceStatuses"
            (
                "DeviceId",
                "IsOnline",
                "LastHeartbeat",
                "LastSeen",
                "GpsSignal",
                "BatteryLevel",
                "IgnitionStatus",
                "MovementStatus",
                "PowerStatus",
                "UpdatedAt"
            )

            VALUES
            (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )

            ON CONFLICT ("DeviceId")

            DO UPDATE SET

                "IsOnline" = EXCLUDED."IsOnline",
                "LastHeartbeat" = EXCLUDED."LastHeartbeat",
                "LastSeen" = EXCLUDED."LastSeen",
                "GpsSignal" = EXCLUDED."GpsSignal",
                "BatteryLevel" = EXCLUDED."BatteryLevel",
                "IgnitionStatus" = EXCLUDED."IgnitionStatus",
                "MovementStatus" = EXCLUDED."MovementStatus",
                "PowerStatus" = EXCLUDED."PowerStatus",
                "UpdatedAt" = EXCLUDED."UpdatedAt"
            """,
            (
                status.device_id,
                status.is_online,
                status.last_heartbeat,
                status.last_seen,
                status.gps_signal,
                status.battery_level,
                status.ignition_status,
                status.movement_status,
                status.power_status,
                now
            )
        )

        conn.commit()

        cursor.close()
        conn.close()


    @staticmethod
    def set_online(device_id: str):

        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)

        cursor.execute(
            """
            UPDATE "DeviceStatuses"

            SET

                "IsOnline" = TRUE,
                "LastSeen" = %s,
                "UpdatedAt" = %s

            WHERE "DeviceId" = %s
            """,
            (
                now,
                now,
                device_id
            )
        )

        conn.commit()

        cursor.close()
        conn.close()


    @staticmethod
    def set_offline(device_id: str):

        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)

        cursor.execute(
            """
            UPDATE "DeviceStatuses"

            SET

                "IsOnline" = FALSE,
                "UpdatedAt" = %s

            WHERE "DeviceId" = %s
            """,
            (
                now,
                device_id
            )
        )

        conn.commit()

        cursor.close()
        conn.close()