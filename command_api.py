import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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
)
from services.device_registry import get_all_devices

COMMAND_API_PORT = int(os.environ.get("COMMAND_API_PORT", 9001))

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


class CommandAPIHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        log(f"[CommandAPI] {self.address_string()} — {format % args}")

    def _send_json(self, status: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})

        elif self.path == "/devices":
            devices = get_all_devices()
            result = [
                {
                    "imei": imei,
                    "device_id": info["device_id"],
                    "ip": info["ip"],
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
                SIMPLE_COMMANDS[command](imei)

            # Parametrised commands
            elif command == "timer":
                send_timer(imei, int(params["t1"]), int(params["t2"]))

            elif command == "distance":
                send_distance(imei, int(params["meters"]))

            elif command == "speed_alarm":
                send_speed_alarm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    interval=int(params.get("interval", 20)),
                    limit_kmh=int(params.get("limit_kmh", 100)),
                    sms=bool(params.get("sms", True))
                )

            elif command == "moving_alarm":
                send_moving_alarm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    radius_m=int(params.get("radius_m", 300)),
                    sms=bool(params.get("sms", True))
                )

            elif command == "fence_circle":
                send_fence_circle(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    lat=float(params["lat"]),
                    lon=float(params["lon"]),
                    radius_100m=int(params["radius_100m"]),
                    trigger=params.get("trigger", ""),
                    sms=bool(params.get("sms", True))
                )

            elif command == "sos_add":
                send_sos_add(
                    imei,
                    phone1=params.get("phone1", ""),
                    phone2=params.get("phone2", ""),
                    phone3=params.get("phone3", "")
                )

            elif command == "apn":
                send_apn(
                    imei,
                    apn_name=params["apn_name"],
                    user=params.get("user", ""),
                    pwd=params.get("pwd", "")
                )

            elif command == "server":
                send_server(
                    imei,
                    domain_or_ip=params["domain_or_ip"],
                    port=int(params["port"]),
                    use_domain=bool(params.get("use_domain", True)),
                    udp=bool(params.get("udp", False))
                )

            elif command == "batalm":
                send_batalm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    sms=bool(params.get("sms", True))
                )

            elif command == "poweralm":
                send_poweralm(
                    imei,
                    enabled=bool(params.get("enabled", True)),
                    sms=bool(params.get("sms", True))
                )

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

        except KeyError as ex:
            self._send_json(400, {"error": f"Missing required param: {ex}"})
        except Exception as ex:
            log(f"[CommandAPI] ❌ Failed '{command}' → {imei}: {ex}")
            if "offline" in str(ex).lower():
                self._send_json(503, {"success": False, "error": "Device offline"})
            else:
                self._send_json(500, {"success": False, "error": str(ex)})


def start_command_api():
    server = HTTPServer(("0.0.0.0", COMMAND_API_PORT), CommandAPIHandler)
    log(f"🌐 Command API listening on port {COMMAND_API_PORT}")
    server.serve_forever()


def start_command_api_thread():
    thread = threading.Thread(target=start_command_api, daemon=True)
    thread.start()
    return thread