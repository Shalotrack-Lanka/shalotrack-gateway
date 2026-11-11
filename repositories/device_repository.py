from database import get_db_connection, release_db_connection, managed_connection
from utils.logger import log
from services import device_access
import uuid


class DeviceRepository:

    @staticmethod
    async def get_device_by_imei(
        imei: str,
        protocol_type: str = "GT06",
        default_model: str = "V5",
    ) -> str | None:
        """
        protocol_type / default_model only matter on the auto-registration path
        (first login of a newly Activated device): they are written to
        GpsDevices.ProtocolType and, when SetupShalotrackDevices.DeviceCategory
        is empty, GpsDevices.DeviceModel. Defaults keep the V5 behaviour.

        Single-connection lookup — checks GpsDevices and SetupShalotrackDevices
        in one borrowed connection to minimise pool pressure during login bursts.

        Flow:
        1. Check GpsDevices — already registered, return immediately (1 connection total)
        2. Check SetupShalotrackDevices WHERE Status = 'Activated' (same connection)
           - Not found or not Activated → reject
           - Activated → auto-create GpsDevices record (second connection via managed_connection)
        3. IMEI not in either table → unknown device, rejected
        """
        # In 'off' mode the query is byte-for-byte what it always was, so deploying this release can never
        # depend on the new columns. Only 'shadow' and 'enforce' read them.
        trial_on = device_access.MODE != "off"
        extra_s = ', s."BoundAt", s."FirstSeenAt"' if trial_on else ""
        extra = ', "BoundAt", "FirstSeenAt"' if trial_on else ""

        conn = await get_db_connection()
        try:
            # Step 1 — already in GpsDevices?
            # A device that already has a GpsDevices row is still refused when its
            # SetupShalotrackDevices row says it is no longer Activated (e.g. a faulty
            # device that was replaced). No setup row at all (legacy/demo device) keeps
            # the old behaviour and is allowed.
            result = await conn.fetchrow(
                '''
                SELECT g."DeviceId", s."Status"{extra_s}
                FROM "GpsDevices" g
                LEFT JOIN "SetupShalotrackDevices" s ON s."ImeiNumber" = g."ImeiNumber"
                WHERE g."ImeiNumber" = $1
                LIMIT 1
                '''.format(extra_s=extra_s),
                imei
            )
            if result:
                if result["Status"] is None:
                    return str(result["DeviceId"])  # legacy/demo device: no setup row, allowed as before
                if not await DeviceRepository._admit(conn, imei, result):
                    return None
                return str(result["DeviceId"])

            # Step 2 — check SetupShalotrackDevices, Activated only
            setup = await conn.fetchrow(
                """
                SELECT "ImeiNumber", "DeviceCategory", "SimNumber", "Status"{extra}
                FROM "SetupShalotrackDevices"
                WHERE "ImeiNumber" = $1
                LIMIT 1
                """.format(extra=extra),
                imei
            )

            if not setup:
                log(f"🚫 IMEI {imei} not found in SetupShalotrackDevices — unknown device")
                return None

            if not await DeviceRepository._admit(conn, imei, setup):
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
                    setup["DeviceCategory"] or default_model,
                    protocol_type,
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
    async def _admit(conn, imei: str, row) -> bool:
        """
        Apply the login rule (services/device_access.py) to a SetupShalotrackDevices row.
        Returns True if the device may connect. Only 'enforce' mode changes behaviour.
        """
        status = row["Status"]
        mode = device_access.MODE

        if mode == "off":
            if not device_access.old_rule(status):
                log(f"🚫 IMEI {imei} Status = '{status}' — rejected")
                return False
            return True

        decision = device_access.decide(status, row["BoundAt"], row["FirstSeenAt"])
        old_allows = device_access.old_rule(status)

        if mode == "shadow":
            if decision.allowed != old_allows:
                log(
                    f"🧪 TRIAL-SHADOW IMEI {imei} Status='{status}' reason={decision.reason}: "
                    f"new rule would {'ACCEPT' if decision.allowed else 'REFUSE'}, "
                    f"current rule {'accepts' if old_allows else 'refuses'}"
                )
            if not old_allows:
                log(f"🚫 IMEI {imei} Status = '{status}' — rejected")
            return old_allows

        # enforce
        if not decision.allowed:
            log(f"🚫 IMEI {imei} Status = '{status}' — rejected ({decision.reason})")
            return False

        if decision.stamp_first_seen:
            # First connection of a trial device starts its clock. COALESCE makes concurrent first logins
            # agree on one start time. If the stamp cannot be written the device is REFUSED: accepting
            # without a clock would make the trial endless.
            try:
                await conn.execute(
                    '''
                    UPDATE "SetupShalotrackDevices"
                    SET "FirstSeenAt" = COALESCE("FirstSeenAt", NOW())
                    WHERE "ImeiNumber" = $1
                    ''',
                    imei,
                )
            except Exception as e:
                log(f"❌ Could not start trial clock for {imei}: {e} — refusing")
                return False
            log(f"🆕 IMEI {imei} first connection — {device_access.TRIAL_HOURS}h trial started")
        elif decision.reason == "trial":
            log(f"⏳ IMEI {imei} on trial (not yet bound)")
        return True

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