from database import get_db_connection, release_db_connection, managed_connection
from utils.logger import log
import uuid


class DeviceRepository:

    @staticmethod
    def get_device_by_imei(imei: str) -> str | None:
        """
        Single-connection lookup — checks GpsDevices and SetupShalotrackDevices
        in one borrowed connection to minimise pool pressure during login bursts.

        Flow:
        1. Check GpsDevices — already registered, return immediately (1 connection total)
        2. Check SetupShalotrackDevices WHERE Status = 'Activated' (same connection)
           - Not found or not Activated → reject
           - Activated → auto-create GpsDevices record (second connection via managed_connection)
        3. IMEI not in either table → unknown device, rejected
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            # Step 1 — already in GpsDevices?
            cursor.execute(
                'SELECT "DeviceId" FROM "GpsDevices" WHERE "ImeiNumber" = %s LIMIT 1',
                (imei,)
            )
            result = cursor.fetchone()
            if result:
                cursor.close()
                return str(result[0])

            # Step 2 — check SetupShalotrackDevices, Activated only
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

            if not setup:
                log(f"🚫 IMEI {imei} not found in SetupShalotrackDevices — unknown device")
                return None

            status = setup[3]
            if status != "Activated":
                log(f"🚫 IMEI {imei} Status = '{status}' — rejected")
                return None

        except Exception as e:
            log(f"❌ Device lookup error for {imei}: {e}")
            return None
        finally:
            # Always return connection to pool — even if exception occurred
            release_db_connection(conn)

        # Step 3 — Status = 'Activated', auto-create GpsDevices record
        # Uses a fresh managed_connection — previous one already returned to pool
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