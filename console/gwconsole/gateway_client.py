"""
Read-only client for the gateway's Command API.

Read-only is enforced by construction, not by convention:
  * the only HTTP verb in this module is GET, and
  * only the paths in READ_PATHS can be requested (POST /command does not exist here).

The shared secret is sent in X-Command-Api-Key and is never placed in an exception,
a log line or a return value. Failures are reduced to a small set of `kind`s so the UI
can say what is wrong without echoing hostnames or response bodies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

READ_PATHS = frozenset({"/health", "/devices", "/commands"})
MAX_BODY_BYTES = 2 * 1024 * 1024  # a fleet of thousands of devices is still well under this
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 6.0  # the gateway's deep /health can take ~3 s when the DB is slow


class GatewayError(Exception):
    """kind is one of: unreachable, timeout, unauthorized, bad_response, http_error."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}{': ' + detail if detail else ''}")
        self.kind = kind


@dataclass(frozen=True)
class Device:
    imei: str
    device_id: str
    ip: str
    protocol: str
    connected_at: datetime
    last_seen: datetime


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    parsed = datetime.fromisoformat(value)
    # The gateway sends aware UTC. If a naive value ever appears, treat it as UTC
    # rather than guessing a local zone.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class GatewayClient:
    def __init__(self, base_url: str, api_key: str, session: requests.Session | None = None):
        self._base = base_url.rstrip("/")
        self._headers = {"X-Command-Api-Key": api_key, "Accept": "application/json"}
        self._session = session or requests.Session()

    # -- the single network primitive ---------------------------------------------
    def _get(self, path: str) -> tuple[int, dict]:
        if path not in READ_PATHS:
            raise ValueError("path is not on the read-only allow-list")
        try:
            response = self._session.get(
                self._base + path,
                headers=self._headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=False,  # a redirect would resend the key to another host
                stream=True,
            )
        except requests.Timeout:
            raise GatewayError("timeout") from None
        except requests.RequestException as exc:
            # Class name only: the message can embed the host and resolver details.
            raise GatewayError("unreachable", type(exc).__name__) from None

        try:
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise GatewayError("bad_response", "response too large")
                chunks.append(chunk)
            raw = b"".join(chunks)
        except requests.RequestException as exc:
            raise GatewayError("unreachable", type(exc).__name__) from None
        finally:
            response.close()

        status = response.status_code
        if status in (401, 403):
            raise GatewayError("unauthorized")
        if 300 <= status < 400:
            raise GatewayError("bad_response", "unexpected redirect")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GatewayError("bad_response", f"HTTP {status}, body is not JSON") from None
        if not isinstance(body, dict):
            raise GatewayError("bad_response", "JSON body is not an object")
        return status, body

    # -- endpoints -----------------------------------------------------------------
    def health(self) -> dict:
        """The gateway answers 503 with a full body when it is 'down'; that is a result, not an error."""
        status, body = self._get("/health")
        if status not in (200, 503) or body.get("status") not in ("ok", "degraded", "down"):
            raise GatewayError("bad_response", f"HTTP {status}, unexpected health body")
        return body

    def devices(self) -> list[Device]:
        status, body = self._get("/devices")
        if status != 200 or not isinstance(body.get("connected_devices"), list):
            raise GatewayError("http_error", f"HTTP {status}")
        out: list[Device] = []
        for row in body["connected_devices"]:
            try:
                out.append(
                    Device(
                        imei=str(row["imei"]),
                        device_id=str(row.get("device_id", "")),
                        ip=str(row.get("ip", "")),
                        protocol=str(row.get("protocol", "GT06")),
                        connected_at=_parse_time(row["connected_at"]),
                        last_seen=_parse_time(row["last_seen"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # One malformed row must not blank the whole table; it is dropped, and
                # the caller can see the count mismatch.
                continue
        return out

    def commands(self) -> list[str]:
        status, body = self._get("/commands")
        items = body.get("supported_commands")
        if status != 200 or not isinstance(items, list):
            raise GatewayError("http_error", f"HTTP {status}")
        return [c for c in items if isinstance(c, str)]
