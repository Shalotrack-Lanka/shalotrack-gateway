import asyncio
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from utils.logger import log
from services.command_service import (
    send_where, send_status, send_version, send_imei, send_params,
    send_gprsset, send_url, send_position, send_fence_query,
    send_moving_query, send_speed_query, send_sos_query,
    send_timer_query, send_apn_query, send_server_query,
    send_reset, send_relay_on, send_relay_off,
    send_timer, send_distance, send_speed_alarm, send_moving_alarm,
    send_fence_circle, send_sos_add, send_sos_delete,
    send_apn, send_server, send_batalm, send_poweralm,
    UnsupportedCommandError,
)
from services.device_registry import get_all_devices
from services.health_service import get_health

COMMAND_API_PORT = int(os.environ.get("COMMAND_API_PORT", 9001))

# SECURITY: this HTTP endpoint can command a physical GPS/relay device —
# relay_off cuts a vehicle's engine relay, server/apn can redirect a device's
# reporting target. It is reachable by anything in the VPC, not just the C#
# API. A security-group CIDR rule is not an auth control, so every request
# must present this shared secret. Same shared-secret pattern already used
# for Admin<->API (AdminSyncKeyMiddleware / AdminSync:Key) — same idea here.
COMMAND_API_KEY = os.environ.get("COMMAND_API_KEY", "")

# Simple commands — no extra parameters needed
SIMPLE_COMMANDS = {
    "where":         send_where,
    "status":        send_status,
    "version":       send_version,
    "imei":          send_imei,
    "params":        send_params,
    "gprsset":       send_gprsset,
    "url":           send_url,
    "position":      send_position,
    "fence_query":   send_fence_query,
    "moving_query":  send_moving_query,
    "speed_query":   send_speed_query,
    "sos_query":     send_sos_query,
    "timer_query":   send_timer_query,
    "apn_query":     send_apn_query,
    "server_query":  send_server_query,
    "reset":         send_reset,
    "relay_on":      send_relay_on,
    "relay_off":     send_relay_off,
    "sos_delete":    send_sos_delete,
}

# Set once by start_command_api_thread(). The HTTP server runs on its own
# OS thread (BaseHTTPRequestHandler is synchronous); command_service
# functions are coroutines that touch the device's asyncio socket, which
# lives on the main event loop. run_coroutine_threadsafe bridges the two.
_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro, timeout: float = 5.0):
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=timeout)


class CommandAPIHandler(BaseHTTPRequestHandler):

    # A client that connects and then goes quiet must not hold a handler thread
    # forever. Applies to socket reads only; a command waiting on the device
    # (_run_async, up to 5 s) is not affected.
    timeout = 10

    def log_message(self, format, *args):
        log(f"[CommandAPI] {self.address_string()} — {format % args}")

    def _send_json(self, status: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        # Constant-time comparison (hmac.compare_digest) so a byte-by-byte
        # timing difference can't be used to guess the key. COMMAND_API_KEY
        # is required at startup (see start_command_api below), so an empty
        # expected key here would mean that guard was bypassed — treat it as
        # "deny everything" rather than "allow everything".
        provided = self.headers.get("X-Command-Api-Key", "")
        return bool(COMMAND_API_KEY) and hmac.compare_digest(provided, COMMAND_API_KEY)

    def do_GET(self):
        if not self._authorized():
            self._send_json(401, {"error": "Unauthorized"})
            return

        if self.path == "/health":
            # Real health, not a constant: event loop, database, pool, capacity and
            # packet flow (see services/health_service.py). Still behind the key --
            # this port is VPC-reachable, and VPC reachability is not authentication.
            # 503 only when "down"; "degraded" is 200 so a poller still gets the body.
            report = get_health(_loop)
            self._send_json(503 if report["status"] == "down" else 200, report)

        elif self.path == "/devices":
            devices = get_all_devices()
            result = [
                {
                    "imei": imei,
                    "device_id": info["device_id"],
                    "ip": info["ip"],
                    "protocol": info.get("protocol", "GT06"),
                    "connected_at": info["connected_at"].isoformat(),
                    "last_seen": info["last_seen"].isoformat(),
                }
                for imei, info in devices.items()
            ]
            self._send_json(200, {"connected_devices": result, "count": len(result)})

        elif self.path == "/commands":
            all_commands = list(SIMPLE_COMMANDS.keys()) + [
                "timer", "distance", "speed_alarm", "moving_alarm",
                "fence_circle", "sos_add", "apn", "server",
                "batalm", "poweralm"
            ]
            self._send_json(200, {"supported_commands": all_commands})

        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"error": "Unauthorized"})
            return

        if self.path != "/command":
            self._send_json(404, {"error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "Empty request body"})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        imei = body.get("imei", "").strip()
        command = body.get("command", "").strip().lower()
        params = body.get("params", {})

        if not imei:
            self._send_json(400, {"error": "Missing 'imei' field"})
            return
        if not command:
            self._send_json(400, {"error": "Missing 'command' field"})
            return

        try:
            # Simple commands — no params needed
            if command in SIMPLE_COMMANDS:
                _run_async(SIMPLE_COMMANDS[command](imei))

            # Parametrised commands
            elif command == "timer":
                _run_async(send_timer(imei, int(params["t1"]), int(params["t2"])))

            elif command == "distance":
                _run_async(send_distance(imei, int(params["meters"])))

            elif command == "speed_alarm":
                _run_async(send_speed_alarm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    interval=int(params.get("interval", 20)),
                    limit_kmh=int(params.get("limit_kmh", 100)),
                    sms=bool(params.get("sms", True))
                ))

            elif command == "moving_alarm":
                _run_async(send_moving_alarm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    radius_m=int(params.get("radius_m", 300)),
                    sms=bool(params.get("sms", True))
                ))

            elif command == "fence_circle":
                _run_async(send_fence_circle(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    lat=float(params["lat"]),
                    lon=float(params["lon"]),
                    radius_100m=int(params["radius_100m"]),
                    trigger=params.get("trigger", ""),
                    sms=bool(params.get("sms", True))
                ))

            elif command == "sos_add":
                _run_async(send_sos_add(
                    imei,
                    phone1=params.get("phone1", ""),
                    phone2=params.get("phone2", ""),
                    phone3=params.get("phone3", "")
                ))

            elif command == "apn":
                _run_async(send_apn(
                    imei,
                    apn_name=params["apn_name"],
                    user=params.get("user", ""),
                    pwd=params.get("pwd", "")
                ))

            elif command == "server":
                _run_async(send_server(
                    imei,
                    domain_or_ip=params["domain_or_ip"],
                    port=int(params["port"]),
                    use_domain=bool(params.get("use_domain", True)),
                    udp=bool(params.get("udp", False))
                ))

            elif command == "batalm":
                _run_async(send_batalm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    sms=bool(params.get("sms", True))
                ))

            elif command == "poweralm":
                _run_async(send_poweralm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    sms=bool(params.get("sms", True))
                ))

            else:
                self._send_json(400, {
                    "error": f"Unknown command '{command}'",
                    "hint": "GET /commands for full list"
                })
                return

            log(f"[CommandAPI] ✅ Sent '{command}' to IMEI {imei}")
            self._send_json(200, {
                "success": True,
                "imei": imei,
                "command": command,
                "message": f"Command '{command}' sent to device"
            })

        except UnsupportedCommandError as ex:
            self._send_json(501, {"success": False, "error": str(ex)})
        except KeyError as ex:
            self._send_json(400, {"error": f"Missing required param: {ex}"})
        except TimeoutError:
            log(f"[CommandAPI] ❌ Timed out sending '{command}' to {imei}")
            self._send_json(503, {"success": False, "error": "Gateway busy — command timed out"})
        except Exception as ex:
            log(f"[CommandAPI] ❌ Failed '{command}' → {imei}: {ex}")
            if "offline" in str(ex).lower():
                self._send_json(503, {"success": False, "error": "Device offline"})
            else:
                self._send_json(500, {"success": False, "error": str(ex)})


def start_command_api():
    if not COMMAND_API_KEY:
        # Fail closed, not open: refuse to serve this endpoint at all rather
        # than start it unauthenticated. The main TCP device-ingestion server
        # (tcp_server.py, port 9000) runs on a separate thread/loop and is
        # unaffected — losing "send commands to devices" is far better than
        # letting anyone on the network issue them.
        log("🚨 FATAL: COMMAND_API_KEY is not set. Refusing to start the Command API "
            "(it would otherwise accept relay_off/server/apn commands from anyone "
            "who can reach this port, unauthenticated). GPS ingestion is unaffected.")
        return

    # Threading, not single-threaded: a deep /health check can take a few seconds
    # when the database is slow, and one slow or stalled client must not block
    # admin commands (or the next health poll) queued behind it.
    server = ThreadingHTTPServer(("0.0.0.0", COMMAND_API_PORT), CommandAPIHandler)
    log(f"🌐 Command API listening on port {COMMAND_API_PORT}")
    server.serve_forever()


def start_command_api_thread(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop
    thread = threading.Thread(target=start_command_api, daemon=True)
    thread.start()
    return thread