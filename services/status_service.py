from services.event_service import create_event
from services.tracking_service import (
    get_device_status,
    get_vehicle_by_device,
    update_device_status
)

from constants.event_types import EventType
from constants.severity import Severity


def process_status(
    device_id,
    battery_level,
    gps_signal,
    ignition_status,
    movement_status,
    power_status
):
    """
    Compare previous device status with the latest status.
    Generate events only when a value changes.
    """

    previous = get_device_status(device_id)

    vehicle_id = get_vehicle_by_device(device_id)

    # -------------------------------------------------------
    # First Status Ever
    # -------------------------------------------------------

    if previous is None:

        update_device_status(
            device_id=device_id,
            battery_level=battery_level,
            gps_signal=gps_signal,
            ignition_status=ignition_status,
            movement_status=movement_status,
            power_status=power_status
        )

        create_event(
            device_id=device_id,
            vehicle_id=vehicle_id,
            event_type=EventType.DEVICE_ONLINE.value,
            severity=Severity.LOW.value,
            description="Device connected."
        )

        print("🟢 DEVICE_ONLINE")

        return

    # -------------------------------------------------------
    # Online
    # -------------------------------------------------------

    if previous["is_online"] is False:

        create_event(
            device_id=device_id,
            vehicle_id=vehicle_id,
            event_type=EventType.DEVICE_ONLINE.value,
            severity=Severity.LOW.value,
            description="Device reconnected."
        )

        print("🟢 DEVICE_ONLINE")

    # -------------------------------------------------------
    # Ignition
    # -------------------------------------------------------

    if previous["ignition_status"] != ignition_status:

        if ignition_status:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=IGNITION_ON,
                severity=LOW,
                description="Ignition turned ON."
            )

            print("🟢 IGNITION_ON")

        else:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=IGNITION_OFF,
                severity=LOW,
                description="Ignition turned OFF."
            )

            print("🔴 IGNITION_OFF")

    # -------------------------------------------------------
    # Movement
    # -------------------------------------------------------

    if previous["movement_status"] != movement_status:

        if movement_status:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=MOVEMENT_STARTED,
                severity=LOW,
                description="Vehicle started moving."
            )

            print("🚗 MOVEMENT_STARTED")

        else:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=MOVEMENT_STOPPED,
                severity=LOW,
                description="Vehicle stopped."
            )

            print("🛑 MOVEMENT_STOPPED")

    # -------------------------------------------------------
    # Low Battery
    # -------------------------------------------------------

    if (
        battery_level <= 2 and
        previous["battery_level"] > 2
    ):

        create_event(
            device_id=device_id,
            vehicle_id=vehicle_id,
            event_type=LOW_BATTERY,
            severity=HIGH,
            description=f"Battery level is {battery_level}."
        )

        print("🔋 LOW_BATTERY")

    # -------------------------------------------------------
    # Power
    # -------------------------------------------------------

    if previous["power_status"] != power_status:

        if power_status == 1:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=POWER_CONNECTED,
                severity=LOW,
                description="External power connected."
            )

            print("🔌 POWER_CONNECTED")

        else:

            create_event(
                device_id=device_id,
                vehicle_id=vehicle_id,
                event_type=POWER_DISCONNECTED,
                severity=HIGH,
                description="External power disconnected."
            )

            print("⚠️ POWER_DISCONNECTED")

    # -------------------------------------------------------
    # Save latest status
    # -------------------------------------------------------

    update_device_status(
        device_id=device_id,
        battery_level=battery_level,
        gps_signal=gps_signal,
        ignition_status=ignition_status,
        movement_status=movement_status,
        power_status=power_status
    )