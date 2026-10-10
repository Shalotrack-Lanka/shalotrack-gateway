"""
Safe conversion of Python floats to values asyncpg can bind to Postgres
`numeric` columns.

WHY THIS EXISTS (2026-10-10 production incident)
------------------------------------------------
asyncpg binds a Python float to a `numeric` column using its exact IEEE-754
binary expansion, not its short repr — 7.025998333333333 is stored as
7.02599833333333290141808902262710034847259521484375 (50+ digits). Postgres
accepts that (numeric has no precision limit), but Npgsql / C# `decimal`
holds ~28-29 significant digits and throws System.OverflowException the first
time the row is read back — which took down the Android dashboard and the
Fleet portal. psycopg2 serialised floats via repr(), so the old threaded
gateway never had this problem.

RULE: every float that goes into a `numeric` column MUST pass through
clean_decimal() (or clean_coordinates()) first. Do it at the DB-write
boundary (the *_service.py functions), not in the packet handler, so a new
caller cannot forget.
"""

import math
from decimal import Decimal

from utils.logger import log


def clean_decimal(value, places=6):
    """
    Round, then build the Decimal from the STRING form so asyncpg receives a
    short clean value. Returns None for None. Raises ValueError for NaN/inf —
    those must never reach a numeric column (C# decimal cannot represent them
    either).
    """
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite numeric value rejected: {value!r}")
    return Decimal(str(round(value, places)))


def clean_coordinates(latitude, longitude):
    """
    Return (lat, lon) as clean Decimals, or (None, None) if either is missing,
    non-finite, or outside physically possible bounds.

    Never clamps: a missing position is visibly missing, a clamped one looks
    valid and is wrong. (This app has an SOS feature that shows live position.)
    """
    if latitude is None or longitude is None:
        return None, None
    try:
        lat_ok = -90 <= latitude <= 90 and math.isfinite(latitude)
        lon_ok = -180 <= longitude <= 180 and math.isfinite(longitude)
    except TypeError:
        return None, None
    if not (lat_ok and lon_ok):
        log(f"🚨 Dropping invalid coordinates (lat={latitude!r} lon={longitude!r})")
        return None, None
    return clean_decimal(latitude), clean_decimal(longitude)