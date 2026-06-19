from services.tracking_service import get_device_by_imei

device_id = get_device_by_imei("355172106043787")

print(f"Device ID for IMEI 355172106043787 (Test Device): {device_id}")