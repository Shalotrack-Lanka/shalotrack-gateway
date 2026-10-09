from datetime import datetime, timedelta, timezone

# Sri Lanka is a fixed UTC+5:30 offset, year-round -- no daylight saving time
# to account for, so a simple constant offset is safe and doesn't depend on
# the container having a full IANA timezone database installed.
SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)
SRI_LANKA_TZ = timezone(SRI_LANKA_OFFSET)

def log(message):
    local_time = datetime.now(SRI_LANKA_TZ)
    prefix = f"[{local_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}]"
    print(f"{prefix} {message}")