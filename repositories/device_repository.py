from database import get_db_connection, release_db_connection
from utils.logger import log


class DeviceRepository:

    @staticmethod
    def get_device_by_imei(imei: str) -> str | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT "DeviceId" FROM "GpsDevices"
                WHERE "ImeiNumber" = %s AND "ActivationStatus" = 1
                LIMIT 1
                """,
                (imei,)
            )
            result = cursor.fetchone()
            cursor.close()
            return str(result[0]) if result else None
        finally:
            release_db_connection(conn)

    @staticmethod
    def get_vehicle_by_device(device_id: str) -> str | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT "VehicleId" FROM "DeviceAssignments"
                WHERE "DeviceId" = %s AND "Status" = 1
                LIMIT 1
                """,
                (device_id,)
            )
            result = cursor.fetchone()
            cursor.close()
            return str(result[0]) if result else None
        finally:
            release_db_connection(conn)

    @staticmethod
    def get_device_status(device_id: str) -> dict | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT "IsOnline","BatteryLevel","GpsSignal","IgnitionStatus","MovementStatus","PowerStatus"
                FROM "DeviceStatuses" WHERE "DeviceId" = %s LIMIT 1
                """,
                (device_id,)
            )
            result = cursor.fetchone()
            cursor.close()
            if result is None:
                return None
            return {
                "is_online": result[0],
                "battery_level": result[1],
                "gps_signal": result[2],
                "ignition_status": result[3],
                "movement_status": result[4],
                "power_status": result[5],
            }
        finally:
            release_db_connection(conn)