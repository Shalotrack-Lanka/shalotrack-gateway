from database import get_db_connection, release_db_connection, managed_connection
from utils.logger import log
import uuid


class DeviceRepository:

    @staticmethod
    def get_device_by_imei(imei: str) -> str | None:
        """
        Three-step allowlist check:
        1. Already in GpsDevices → return immediately
        2. In SetupShalotrackDevices with Status='Activated' → auto-create GpsDevices record
        3. Neither → reject

        Every connection is guaranteed to return to the pool via try/finally.
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
        except Exception as e:
            log(f"❌ GpsDevices lookup error for {imei}: {e}")
            return None
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
        except Exception as e:
            log(f"❌ SetupShalotrackDevices lookup error for {imei}: {e}")
            return None
        finally:
            release_db_connection(conn)

        # Not in setup table — unknown device
        if not setup:
            log(f"🚫 IMEI {imei} not found in SetupShalotrackDevices — unknown device")
            return None

        status = setup[3]

        # Found but not activated
        if status != "Activated":
            log(f"🚫 IMEI {imei} Status = '{status}' — rejected")
            return None

        # Step 2b — Activated, auto-create GpsDevices record
        log(f"📋 IMEI {imei} is Activated — auto-registering into GpsDevices")
        device_id = str(uuid.uuid4())

        try:
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
                        setup[2],
                        setup[1] or "V5",
                        "GT06",
                        1,
                    )
                )
                conn.commit()

                # Re-fetch in case ON CONFLICT DO NOTHING fired
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

        except Exception as e:
            log(f"❌ GpsDevices auto-registration error for {imei}: {e}")
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
        except Exception as e:
            log(f"❌ get_vehicle_by_device error: {e}")
            return None
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
        except Exception as e:
            log(f"❌ get_device_status error: {e}")
            return None
        finally:
            release_db_connection(conn)