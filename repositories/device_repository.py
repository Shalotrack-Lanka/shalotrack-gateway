from database import get_db_connection, release_db_connection, managed_connection
from utils.logger import log
import uuid


class DeviceRepository:

    @staticmethod
    def get_device_by_imei(imei: str) -> str | None:
        """
        Look up a device by IMEI. Three-step process:

        1. Check GpsDevices — already registered, return immediately.
        2. Check SetupShalotrackDevices WHERE Status = 'Activated' only.
           - 'Not Activated'        → rejected
           - 'Temporarily Stopped'  → rejected
           - 'Cancelled'            → rejected
           - 'Activated'            → auto-create GpsDevices record, return DeviceId
        3. IMEI not in either table → unknown device, rejected.
        """

        # Step 1 — already in GpsDevices?
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT "DeviceId" FROM "GpsDevices" WHERE "ImeiNumber" = %s LIMIT 1',
                (imei,)
            )
            result = cursor.fetchone()
            cursor.close()
            if result:
                return str(result[0])
        finally:
            release_db_connection(conn)

        # Step 2 — check SetupShalotrackDevices, Activated only
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT "ImeiNumber", "DeviceCategory", "SimNumber", "Status"
                FROM "SetupShalotrackDevices"
                WHERE "ImeiNumber" = %s
                LIMIT 1
                """,
                (imei,)
            )
            setup = cursor.fetchone()
            cursor.close()
        finally:
            release_db_connection(conn)

        # Not in setup table at all — unknown device
        if not setup:
            log(f"🚫 IMEI {imei} not found in SetupShalotrackDevices — unknown device")
            return None

        status = setup[3]

        # In setup table but not activated
        if status != "Activated":
            log(f"🚫 IMEI {imei} found in SetupShalotrackDevices but Status = '{status}' — rejected")
            return None

        # Step 2b — Status = 'Activated', auto-create GpsDevices record
        log(f"📋 IMEI {imei} is Activated in SetupShalotrackDevices — auto-registering into GpsDevices")
        device_id = str(uuid.uuid4())

        with managed_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO "GpsDevices"
                (
                    "DeviceId",
                    "ImeiNumber",
                    "SimNumber",
                    "DeviceModel",
                    "ProtocolType",
                    "ActivationStatus",
                    "CreatedAt",
                    "UpdatedAt"
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT ("ImeiNumber") DO NOTHING
                """,
                (
                    device_id,
                    imei,
                    setup[2],           # SimNumber
                    setup[1] or "V5",   # DeviceCategory as DeviceModel
                    "GT06",             # ProtocolType
                    1,                  # ActivationStatus
                )
            )
            conn.commit()

            # Re-fetch in case ON CONFLICT DO NOTHING fired
            # (race condition: two threads registering same IMEI simultaneously)
            cursor.execute(
                'SELECT "DeviceId" FROM "GpsDevices" WHERE "ImeiNumber" = %s',
                (imei,)
            )
            row = cursor.fetchone()
            cursor.close()

        if row:
            log(f"✅ GpsDevices record created — IMEI: {imei} → DeviceId: {row[0]}")
            return str(row[0])

        return None

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
                SELECT "IsOnline","BatteryLevel","GpsSignal",
                       "IgnitionStatus","MovementStatus","PowerStatus"
                FROM "DeviceStatuses"
                WHERE "DeviceId" = %s LIMIT 1
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