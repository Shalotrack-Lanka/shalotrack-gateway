from datetime import datetime, timedelta, timezone

# Sri Lanka is a fixed UTC+5:30 offset, year-round -- no daylight saving time
# to account for, so a simple constant offset is safe and doesn't depend on
# the container having a full IANA timezone database installed.
SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)
SRI_LANKA_TZ = timezone(SRI_LANKA_OFFSET)


def log(message):

    # FIX: was datetime.now(), which used the server's own system clock --
    # confirmed to be UTC on this EC2 instance. Every log line now shows Sri
    # Lanka local time instead, to avoid the exact kind of confusion that
    # prompted this fix (reading UTC timestamps while mentally expecting
    # local time).
    local_time = datetime.now(SRI_LANKA_TZ)

    print(
        f"[{local_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {message}"
    )