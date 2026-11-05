"""
Contract test against the REAL gateway code (not the fake): boots command_api.CommandAPIHandler
from the gateway repo in a subprocess, with the real device registry and the real health
service (no database, so the real /health honestly reports "down").

Skipped unless the gateway repo is available:
    GATEWAY_REPO=../shalotrack-gateway  GATEWAY_PYTHON=<python with the gateway's requirements>
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from gwconsole import present
from gwconsole.gateway_client import GatewayClient, GatewayError

ROOT = Path(__file__).resolve().parent.parent
REPO = Path(os.environ.get("GATEWAY_REPO", ROOT.parent / "shalotrack-gateway")).resolve()
PYTHON = os.environ.get("GATEWAY_PYTHON", sys.executable)
KEY = "contract-key"

SERVER = textwrap.dedent("""
    import asyncio, os, sys, threading
    from http.server import ThreadingHTTPServer
    sys.path.insert(0, os.getcwd())
    import command_api
    from services import device_registry
    command_api.COMMAND_API_KEY = sys.argv[1]
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    command_api._loop = loop
    class Conn: pass
    device_registry.register_device("355172106043787", "112.134.1.9", "dev-1", Conn(), "GT06")
    device_registry.register_device("868123456789012", "112.134.2.7", "dev-2", Conn(), "GPS103")
    device_registry.mark_packet_received()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), command_api.CommandAPIHandler)
    print(srv.server_address[1], flush=True)
    srv.serve_forever()
""")


@pytest.fixture(scope="module")
def real_gateway():
    if not (REPO / "command_api.py").exists():
        pytest.skip("gateway repo not found (set GATEWAY_REPO)")
    proc = subprocess.Popen([PYTHON, "-c", SERVER, KEY], cwd=REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    if not line.isdigit():
        err = proc.stderr.read()[-800:]
        proc.kill()
        pytest.skip(f"could not start the real gateway handler with {PYTHON}: {err}")
    yield f"http://127.0.0.1:{line}"
    proc.terminate()
    proc.wait(timeout=5)


def test_real_health_report_is_understood(real_gateway):
    report = GatewayClient(real_gateway, KEY).health()  # 503 "down" (no DB) must still parse
    assert report["status"] in ("ok", "degraded", "down")
    assert isinstance(report["reasons"], list) and "checks" in report
    rows = present.check_rows(report)  # every key the UI reads exists in the real report
    assert {r["Check"] for r in rows} >= {"Event loop (serves device sockets)", "Database", "Packet flow"}
    assert present.health_headline(report)[0] in ("ok", "degraded", "down")
    assert report["devices_connected"] == 2


def test_real_devices_are_parsed(real_gateway):
    devices = GatewayClient(real_gateway, KEY).devices()
    assert {d.imei: d.protocol for d in devices} == {"355172106043787": "GT06", "868123456789012": "GPS103"}
    assert all(d.last_seen.tzinfo is not None for d in devices)
    assert [r["State"] for r in present.device_rows(devices, devices[0].last_seen)] == ["active", "active"]


def test_real_command_list_is_fully_classified(real_gateway):
    """If the gateway adds a command, this tells you it is not yet classified in the UI."""
    commands = GatewayClient(real_gateway, KEY).commands()
    assert "relay_off" in commands
    groups = present.group_commands(commands)
    assert groups["other"] == [], f"unclassified commands, add them to gwconsole/present.py: {groups['other']}"
    assert set(present.HIGH_IMPACT) <= set(commands)


def test_real_gateway_rejects_wrong_key(real_gateway):
    with pytest.raises(GatewayError) as e:
        GatewayClient(real_gateway, "wrong").health()
    assert e.value.kind == "unauthorized"
