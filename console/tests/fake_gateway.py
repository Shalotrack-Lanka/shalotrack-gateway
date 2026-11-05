"""
A stand-in for the gateway's Command API with the SAME contract (auth header, /health,
/devices, /commands, 503 body on "down"). Used by the tests and for trying the UI locally:

    python -m tests.fake_gateway --scenario degraded --port 8001

It records every request so tests can prove the console never sends a POST.
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMMANDS = [
    "where", "status", "version", "imei", "params", "gprsset", "url", "position",
    "fence_query", "moving_query", "speed_query", "sos_query", "timer_query",
    "apn_query", "server_query", "reset", "relay_on", "relay_off", "sos_delete",
    "timer", "distance", "speed_alarm", "moving_alarm", "fence_circle", "sos_add",
    "apn", "server", "batalm", "poweralm",
]


def make_devices(n: int = 14) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        idle = [20, 45, 130, 280, 590, 700, 1500, 2600, 5400][i % 9]
        out.append({
            "imei": f"35517210{600000 + i * 37:07d}"[:15],
            "device_id": f"dev-{i:03d}",
            "ip": f"112.134.{10 + i}.{(i * 7) % 250 + 1}",
            "protocol": "GPS103" if i % 5 == 0 else "GT06",
            "connected_at": (now - timedelta(seconds=idle + 3600 * (i + 1))).isoformat(),
            "last_seen": (now - timedelta(seconds=idle)).isoformat(),
        })
    return out


def make_health(scenario: str, device_count: int) -> tuple[int, dict]:
    db = {"ok": True, "ms": 38.2, "error": None, "cached_age_s": 3.1, "pool": {"size": 4, "idle": 3, "max": 10}}
    body = {
        "status": "ok", "reasons": [],
        "checks": {"event_loop": {"ok": True, "ms": 0.4, "error": None}, "database": db},
        "devices_connected": device_count, "last_packet_age_s": 7.5, "uptime_s": 93784,
    }
    if scenario == "degraded":
        db["ms"], db["pool"] = 2100.0, {"size": 10, "idle": 0, "max": 10}
        body.update(status="degraded", reasons=[
            "database slow (2100 ms for SELECT 1)",
            "database pool exhausted (10/10 connections in use)",
        ])
        return 200, body
    if scenario == "down":
        body["checks"]["database"] = {"ok": False, "ms": None, "error": "TimeoutError", "cached_age_s": 0.0}
        body.update(status="down", reasons=["database unreachable (TimeoutError) - location data cannot be saved"])
        return 503, body
    return 200, body


class FakeGateway:
    def __init__(self, api_key: str = "test-key", scenario: str = "ok", devices: list[dict] | None = None,
                 port: int = 0, host: str = "127.0.0.1"):
        self.api_key = api_key
        self.scenario = scenario
        self.devices = make_devices() if devices is None else devices
        self.requests: list[tuple[str, str]] = []  # (method, path)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, status: int, body, raw: bytes | None = None, headers: dict | None = None):
                payload = raw if raw is not None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)

            def _authorized(self) -> bool:
                return self.headers.get("X-Command-Api-Key", "") == outer.api_key

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if not self._authorized():
                    return self._send(401, {"error": "Unauthorized"})
                if outer.scenario == "garbage":
                    return self._send(200, None, raw=b"<html>not json</html>")
                if outer.scenario == "redirect":
                    return self._send(302, {}, headers={"Location": "http://evil.invalid/"})
                if self.path == "/health":
                    status, body = make_health(outer.scenario, len(outer.devices))
                    return self._send(status, body)
                if self.path == "/devices":
                    return self._send(200, {"connected_devices": outer.devices, "count": len(outer.devices)})
                if self.path == "/commands":
                    return self._send(200, {"supported_commands": COMMANDS})
                self._send(404, {"error": "Not found"})

            def do_POST(self):
                outer.requests.append(("POST", self.path))
                self._send(200, {"success": True})

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeGateway":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="ok", choices=["ok", "degraded", "down", "empty"])
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--key", default="dev-key")
    args = ap.parse_args()
    gw = FakeGateway(api_key=args.key, scenario=args.scenario, port=args.port,
                     devices=[] if args.scenario == "empty" else None)
    print(f"fake gateway on {gw.url} scenario={args.scenario} key={args.key}")
    gw.server.serve_forever()
