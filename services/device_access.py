
"""
Who may connect: the rule that decides whether a device login is accepted.

Today's rule (still what runs when DEVICE_TRIAL_MODE=off):
    accepted only if SetupShalotrackDevices.Status = 'Activated'.

The trial rule (DEVICE_TRIAL_MODE=enforce):
    1. unknown IMEI                                        -> refuse
    2. Temporarily Stopped / Pending Repair / Broken       -> refuse (also kicks nothing here; see re-check)
    3. Activated, or bound to a customer's vehicle         -> accept (as today)
    4. anything else (Not Activated, Assigned to Customer,
       not yet bound)                                      -> accept for TRIAL_HOURS from its first
                                                              connection, then refuse until the office
                                                              binds it.

Modes:
    off     exactly the old behaviour; nothing is read or written for the trial.
    shadow  old behaviour decides, the trial rule is only LOGGED next to it ("would accept / would refuse"),
            nothing is written. Use it to see the effect on real traffic before switching on.
    enforce the trial rule decides; the first connection of a trial device stamps FirstSeenAt.

Status values are normalized with strip() to tolerate accidental
leading or trailing whitespace in the database.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MODE = os.getenv("DEVICE_TRIAL_MODE", "off").strip().lower()

if MODE not in ("off", "shadow", "enforce"):
    MODE = "off"

TRIAL_HOURS = int(os.getenv("DEVICE_TRIAL_HOURS", "24"))

STOPPED_STATUSES = frozenset({
    "Temporarily Stopped",
    "Pending Repair",
    "Broken Device",
})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    stamp_first_seen: bool = False


def normalize_status(status: str | None) -> str | None:
    """
    Normalize a database status value.

    Returns None for missing or empty values.
    Removes accidental leading and trailing whitespace.
    """
    if status is None:
        return None

    normalized = status.strip()

    return normalized if normalized else None


def old_rule(status: str | None) -> bool:
    """
    Original activation rule:
    only Activated is accepted.

    Surrounding whitespace is ignored.
    """
    return normalize_status(status) == "Activated"


def decide(
    status: str | None,
    bound_at: datetime | None,
    first_seen_at: datetime | None,
    now: datetime | None = None,
    trial_hours: int = TRIAL_HOURS,
) -> Decision:
    """
    Decide whether a device is allowed to connect under trial rules.

    Rules:
        1. Unknown status -> refuse.
        2. Stopped statuses -> refuse.
        3. Activated -> accept.
        4. Bound to a customer's vehicle -> accept.
        5. Otherwise, allow for the configured trial period.
        6. Refuse after the trial period expires.
    """

    now = now or datetime.now(timezone.utc)

    # Normalize database values before comparing statuses.
    status = normalize_status(status)

    if status is None:
        return Decision(False, "unknown")

    if status in STOPPED_STATUSES:
        return Decision(False, "stopped")

    if status == "Activated":
        return Decision(True, "activated")

    if bound_at is not None:
        return Decision(True, "bound")

    # Not Activated / Assigned to Customer, not bound:
    # apply the trial window.
    if first_seen_at is None:
        return Decision(
            True,
            "trial_started",
            stamp_first_seen=True,
        )

    if first_seen_at.tzinfo is None:
        first_seen_at = first_seen_at.replace(
            tzinfo=timezone.utc
        )

    if now < first_seen_at + timedelta(hours=trial_hours):
        return Decision(True, "trial")

    return Decision(False, "trial_expired")
