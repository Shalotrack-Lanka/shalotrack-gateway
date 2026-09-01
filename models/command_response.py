from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CommandResponse:
    device_id: str
    command: str
    raw_response: str
    parsed_data: dict[str, Any]

    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))