from database import get_db_connection, release_db_connection, managed_connection
from utils.logger import log
import uuid


class DeviceRepository:

    @staticmethod
    async def get_device_by_imei(imei: str) -> str | None:
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
        conn = await get_db_connection()
        try:
            # Step 1 — already in GpsDevices?
            # A device that already has a GpsDevices row is still refused when its
            # SetupShalotrackDevices row says it is no longer Activated (e.g. a faulty
            # device that was replaced). No setup row at all (legacy/demo device) keeps
            # the old behaviour and is allowed.
            result = await conn.fetchrow(
                '''
                SELECT g."DeviceId", s."Status"
                FROM "GpsDevices" g
                LEFT JOIN "SetupShalotrackDevices" s ON s."ImeiNumber" = g."ImeiNumber"
                WHERE g."ImeiNumber" = $1
                LIMIT 1
                ''',
                imei
            )
            if result:
                if result["Status"] is not None and result["Status"] != "Activated":
                    log(f"🚫 IMEI {imei} Status = '{result['Status']}' — rejected (already registered)")
                    return None
                return str(result["DeviceId"])

            # Step 2 — check SetupShalotrackDevices, Activated only
            setup = await conn.fetchrow(
                """
                SELECT "ImeiNumber", "DeviceCategory", "SimNumber", "Status"
                FROM "SetupShalotrackDevices"
                WHERE "ImeiNumber" = $1
                LIMIT 1
                """,
                imei
            )

            if not setup:
                log(f"🚫 IMEI {imei} not found in SetupShalotrackDevices — unknown device")
                return None

            status = setup["Status"]
            if status != "Activated":
                log(f"🚫 IMEI {imei} Status = '{status}' — rejected")
                return None

        except Exception as e:
            log(f"❌ Device lookup error for {imei}: {e}")
            return None
        finally:
            # Always return connection to pool — even if exception occurred
            await release_db_connection(conn)

        # Step 3 — Status = 'Activated', auto-create GpsDevices record
        # Uses a fresh managed_connection — previous one already returned to pool
        log(f"📋 IMEI {imei} is Activated — auto-registering into GpsDevices")
        device_id = str(uuid.uuid4())

        try:
            async with managed_connection() as conn:
                await conn.execute(
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
                    VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                    ON CONFLICT ("ImeiNumber") DO NOTHING
                    """,
                    device_id,
                    imei,
                    setup["SimNumber"],
                    setup["DeviceCategory"] or "V5",
                    "GT06",
                    1,
                )

                # Re-fetch in case ON CONFLICT DO NOTHING fired
                row = await conn.fetchrow(
                    'SELECT "DeviceId" FROM "GpsDevices" WHERE "ImeiNumber" = $1',
                    imei
                )

            if row:
                log(f"✅ GpsDevices record created — IMEI: {imei} → DeviceId: {row['DeviceId']}")
                return str(row["DeviceId"])
            return None

        except Exception as e:
            log(f"❌ GpsDevices auto-registration error for {imei}: {e}")
            return None

    @staticmethod
    async def get_vehicle_by_device(device_id: str) -> str | None:
        conn = await get_db_connection()
        try:
            result = await conn.fetchrow(
                """
                SELECT "VehicleId" FROM "DeviceAssignments"
                WHERE "DeviceId" = $1 AND "Status" = 1
                LIMIT 1
                """,
                device_id
            )
            return str(result["VehicleId"]) if result else None
        except Exception as e:
            log(f"❌ get_vehicle_by_device error: {e}")
            return None
        finally:
            await release_db_connection(conn)

    @staticmethod
    async def get_device_status(device_id: str) -> dict | None:
        conn = await get_db_connection()
        try:
            result = await conn.fetchrow(
                """
                SELECT "IsOnline","BatteryLevel","GpsSignal",
                       "IgnitionStatus","MovementStatus","PowerStatus"
                FROM "DeviceStatuses"
                WHERE "DeviceId" = $1 LIMIT 1
                """,
                device_id
            )
            if result is None:
                return None
            return {
                "is_online": result["IsOnline"],
                "battery_level": result["BatteryLevel"],
                "gps_signal": result["GpsSignal"],
                "ignition_status": result["IgnitionStatus"],
                "movement_status": result["MovementStatus"],
                "power_status": result["PowerStatus"],
            }
        except Exception as e:
            log(f"❌ get_device_status error: {e}")
            return None
        finally:
            await release_db_connection(conn)