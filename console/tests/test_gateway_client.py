import re
import socket
import threading
from pathlib import Path

import pytest

import gwconsole.gateway_client as gc
from gwconsole.gateway_client import GatewayClient, GatewayError
from tests.fake_gateway import FakeGateway, make_devices

KEY = "test-key-123"


@pytest.fixture
def gw():
    g = FakeGateway(api_key=KEY).start()
    yield g
    g.stop()


def client(g, key=KEY):
    return GatewayClient(g.url, key)


# --- happy paths -------------------------------------------------------------------
def test_health_ok(gw):
    report = client(gw).health()
    assert report["status"] == "ok" and report["devices_connected"] == len(gw.devices)


def test_health_down_is_a_result_not_an_error(gw):
    gw.scenario = "down"  # the gateway answers 503 WITH a body
    report = client(gw).health()
    assert report["status"] == "down" and report["reasons"]


def test_health_degraded(gw):
    gw.scenario = "degraded"
    assert client(gw).health()["status"] == "degraded"


def test_devices_parsed_with_aware_times(gw):
    devices = client(gw).devices()
    assert len(devices) == len(gw.devices) == 14
    assert all(d.last_seen.tzinfo and d.connected_at.tzinfo for d in devices)
    assert {d.protocol for d in devices} == {"GT06", "GPS103"}


def test_naive_timestamp_is_treated_as_utc():
    g = FakeGateway(api_key=KEY, devices=[{
        "imei": "355172106000001", "device_id": "d", "ip": "1.2.3.4", "protocol": "GT06",
        "connected_at": "2026-11-05T10:00:00", "last_seen": "2026-11-05T10:01:00"}]).start()
    try:
        d = client(g).devices()[0]
        assert d.last_seen.utcoffset().total_seconds() == 0
    finally:
        g.stop()


def test_one_malformed_row_is_dropped_not_fatal():
    good = make_devices(3)
    bad = {"imei": "x", "connected_at": "garbage", "last_seen": None}
    g = FakeGateway(api_key=KEY, devices=good + [bad, "not-a-dict", {}]).start()
    try:
        assert len(client(g).devices()) == 3
    finally:
        g.stop()


def test_commands(gw):
    cmds = client(gw).commands()
    assert "relay_off" in cmds and "where" in cmds


def test_empty_fleet(gw):
    gw.devices = []
    assert client(gw).devices() == []


# --- failure modes -----------------------------------------------------------------
def test_wrong_key_is_unauthorized_and_key_not_leaked(gw):
    with pytest.raises(GatewayError) as e:
        client(gw, key="wrong").health()
    assert e.value.kind == "unauthorized"
    assert KEY not in str(e.value) and "wrong" not in str(e.value)


def test_unreachable():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    with pytest.raises(GatewayError) as e:
        GatewayClient(f"http://127.0.0.1:{port}", KEY).health()
    assert e.value.kind == "unreachable"
    assert "127.0.0.1" not in str(e.value)  # no host details in the message


def test_timeout(monkeypatch):
    monkeypatch.setattr(gc, "CONNECT_TIMEOUT", 0.3)
    monkeypatch.setattr(gc, "READ_TIMEOUT", 0.3)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)  # accepts the connection, never answers
    stop = threading.Event()
    conns = []

    def hold():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conns.append(srv.accept()[0])
            except OSError:
                pass

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    try:
        with pytest.raises(GatewayError) as e:
            GatewayClient(f"http://127.0.0.1:{srv.getsockname()[1]}", KEY).health()
        assert e.value.kind == "timeout"
    finally:
        stop.set()
        t.join(2)
        for c in conns:
            c.close()
        srv.close()


def test_non_json_body(gw):
    gw.scenario = "garbage"
    with pytest.raises(GatewayError) as e:
        client(gw).health()
    assert e.value.kind == "bad_response"


def test_redirect_is_never_followed(gw):
    gw.scenario = "redirect"
    with pytest.raises(GatewayError) as e:
        client(gw).health()
    assert e.value.kind == "bad_response"
    assert gw.requests == [("GET", "/health")]  # exactly one request: the Location was not chased


def test_oversized_response_rejected(gw, monkeypatch):
    monkeypatch.setattr(gc, "MAX_BODY_BYTES", 200)
    with pytest.raises(GatewayError) as e:
        client(gw).devices()
    assert e.value.kind == "bad_response"


def test_health_with_unknown_status_rejected(gw, monkeypatch):
    monkeypatch.setattr("tests.fake_gateway.make_health", lambda s, n: (200, {"status": "on-fire"}))
    with pytest.raises(GatewayError) as e:
        client(gw).health()
    assert e.value.kind == "bad_response"


# --- read-only by construction -------------------------------------------------------
def test_only_allow_listed_paths(gw):
    c = client(gw)
    for path in ("/command", "/", "/health/../command", "/devices?x=1", "http://evil/health"):
        with pytest.raises(ValueError):
            c._get(path)
    assert gw.requests == []  # nothing was even sent


def test_console_never_issues_a_post(gw):
    c = client(gw)
    c.health(), c.devices(), c.commands()
    assert {m for m, _ in gw.requests} == {"GET"}


def test_module_contains_no_write_verbs():
    """Static guard: nobody can quietly add a POST/PUT/DELETE to the read-only client."""
    src = Path(gc.__file__).read_text()
    assert not re.search(r"\.(post|put|patch|delete|request)\(", src)
    assert not re.search(r"""["'](POST|PUT|PATCH|DELETE)["']""", src)
