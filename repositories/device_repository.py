from database import get_db_connection
import uuid


class DeviceRepository:
    @staticmethod
    def get_device_by_imei(imei: str) -> str | None:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT "DeviceId"
            FROM "GpsDevices"
            WHERE "ImeiNumber" = %s
            LIMIT 1
            """,
            (imei,)
        )

        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if result:
            return str(result[0])

        return None


    @staticmethod
def register_device(imei: str) -> str:

    conn = get_db_connection()
    cursor = conn.cursor()

    device_id = str(uuid.uuid4())

    cursor.execute(
        """
        INSERT INTO "GpsDevices"
        (
            "DeviceId",
            "ImeiNumber",
            "DeviceModel",
            "ProtocolType",
            "ActivationStatus",
            "CreatedAt",
            "UpdatedAt"
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW(),
            NOW()
        )
        """,
        (
            device_id,
            imei,
            "V5",
            "GT06",
            1
        )
    )

    conn.commit()

    cursor.close()
    conn.close()

    return device_id


    @staticmethod
    def get_vehicle_by_device(device_id: str) -> str | None:

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT "VehicleId"
            FROM "DeviceAssignments"
            WHERE "DeviceId" = %s
            AND "Status" = 1
            LIMIT 1
            """,
            (device_id,)
        )

        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if result:
            return str(result[0])

        return None


    @staticmethod
    def get_device_status(device_id: str):

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                "IsOnline",
                "BatteryLevel",
                "GpsSignal",
                "IgnitionStatus",
                "MovementStatus",
                "PowerStatus"
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

        return {
            "is_online": result[0],
            "battery_level": result[1],
            "gps_signal": result[2],
            "ignition_status": result[3],
            "movement_status": result[4],
            "power_status": result[5]
        }