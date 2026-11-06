import os
from datetime import datetime, timedelta, timezone

# Sri Lanka is a fixed UTC+5:30 offset, year-round -- no daylight saving time
# to account for, so a simple constant offset is safe and doesn't depend on
# the container having a full IANA timezone database installed.
SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)
SRI_LANKA_TZ = timezone(SRI_LANKA_OFFSET)

# LOG_LEVEL (env): DEBUG (default, identical output to before) | INFO | WARNING | ERROR.
# Read here, not from config.py, so this module stays importable on its own.
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_THRESHOLD = _LEVELS.get(os.getenv("LOG_LEVEL", "DEBUG").strip().upper(), 10)


def log(message, level="INFO"):
    """Print a timestamped line. `level` is DEBUG / INFO / WARNING / ERROR."""
    if _LEVELS.get(level, 20) < _THRESHOLD:
        return
    local_time = datetime.now(SRI_LANKA_TZ)
    prefix = f"[{local_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}]"
    print(f"{prefix} {message}")


def debug(message):
    """Per-packet detail. Hidden when LOG_LEVEL is INFO or higher."""
    log(message, "DEBUG")
