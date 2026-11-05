"""Drive the real app.py with Streamlit's AppTest against the fake gateway."""

import time
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from gwconsole.auth import Identity
from tests.fake_gateway import FakeGateway

APP = str(Path(__file__).resolve().parent.parent / "app.py")
KEY = "k-123"
CF_ENV = {
    "CONSOLE_AUTH": "cloudflare_access",
    "CF_ACCESS_TEAM_DOMAIN": "shalotrack.cloudflareaccess.com",
    "CF_ACCESS_AUD": "a" * 64,
    "ALLOWED_EMAILS": "ops@shalotrack.com",
}


@pytest.fixture(autouse=True)
def fresh_caches():
    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def gw():
    g = FakeGateway(api_key=KEY).start()
    yield g
    g.stop()


def env(monkeypatch, gw_url, **extra):
    base = {"GATEWAY_URL": gw_url, "COMMAND_API_KEY": KEY, "CONSOLE_AUTH": "none",
            "CONSOLE_ALLOW_INSECURE_NO_AUTH": "1"}
    base.update(extra)
    for k in list(CF_ENV) + ["CONSOLE_ALLOW_INSECURE_NO_AUTH", "ALLOWED_EMAIL_DOMAINS", "REFRESH_SECONDS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def run(**kw) -> AppTest:
    return AppTest.from_file(APP, default_timeout=20).run(**kw)


def texts(elements):
    return " | ".join(e.value for e in elements)


def test_healthy_dashboard_renders_and_is_read_only(monkeypatch, gw):
    env(monkeypatch, gw.url)
    at = run()
    assert not at.exception
    assert "Gateway healthy" in texts(at.success)
    labels = {m.label: m.value for m in at.metric}
    assert labels["Devices connected"] == "14" and labels["Connected"] == "14"
    assert {m for m, _ in gw.requests} == {"GET"}
    assert "Operations console" in texts(at.sidebar.caption)


def test_degraded_shows_reasons(monkeypatch, gw):
    gw.scenario = "degraded"
    env(monkeypatch, gw.url)
    at = run()
    assert "Gateway degraded" in texts(at.warning)
    assert "pool exhausted" in texts(at.markdown)


def test_down_is_red(monkeypatch, gw):
    gw.scenario = "down"
    env(monkeypatch, gw.url)
    at = run()
    assert "Gateway down" in texts(at.error)


def test_unreachable_gateway_explains_without_leaking(monkeypatch):
    env(monkeypatch, "http://127.0.0.1:9")  # discard port: connection refused
    at = run()
    assert not at.exception
    page = texts(at.error) + texts(at.main.caption)  # main area only; the sidebar names the gateway host on purpose
    assert "cannot connect" in page and "may still be connecting" in page
    assert KEY not in page and "127.0.0.1" not in page and "ConnectionError" not in page


def test_wrong_key_reported_as_rejected_key(monkeypatch, gw):
    env(monkeypatch, gw.url, COMMAND_API_KEY="nope")
    at = run()
    assert "rejected" in texts(at.error) and "nope" not in texts(at.error)


def test_bad_config_shows_generic_error_and_never_reads_gateway(monkeypatch, gw):
    env(monkeypatch, gw.url, COMMAND_API_KEY="")
    at = run()
    assert "not configured correctly" in texts(at.error)
    assert gw.requests == []


def test_cloudflare_mode_without_identity_is_denied_before_any_gateway_read(monkeypatch, gw):
    env(monkeypatch, gw.url, **CF_ENV)
    at = run()
    assert "Access denied" in texts(at.error)
    assert gw.requests == []  # the gateway is not touched until the viewer is authenticated
    assert not at.metric


def test_cloudflare_mode_with_verified_identity_renders(monkeypatch, gw):
    env(monkeypatch, gw.url, **CF_ENV)
    at = AppTest.from_file(APP, default_timeout=20)
    at.session_state["identity"] = Identity("ops@shalotrack.com", int(time.time()) + 600)
    at.run()
    assert not at.exception and "Gateway healthy" in texts(at.success)
    assert "ops@shalotrack.com" in texts(at.sidebar.markdown)


def test_expired_identity_is_not_trusted(monkeypatch, gw):
    env(monkeypatch, gw.url, **CF_ENV)
    at = AppTest.from_file(APP, default_timeout=20)
    at.session_state["identity"] = Identity("ops@shalotrack.com", int(time.time()) - 5)
    at.run()
    assert "Access denied" in texts(at.error) and gw.requests == []


def test_no_auth_mode_refuses_non_loopback_bind(monkeypatch, gw):
    env(monkeypatch, gw.url)
    previous = st.get_option("server.address")
    st._config.set_option("server.address", "0.0.0.0")
    try:
        at = run()
    finally:
        st._config.set_option("server.address", previous)
    assert "Refusing to run without authentication" in texts(at.error)
    assert gw.requests == []


def test_device_filter_narrows_table(monkeypatch, gw):
    env(monkeypatch, gw.url)
    at = AppTest.from_file(APP, default_timeout=20).run()
    at.text_input(key="dev_search").set_value("GPS103-nothing").run()
    assert "No devices match" in texts(at.info)


def test_many_viewers_share_one_gateway_read(monkeypatch, gw):
    env(monkeypatch, gw.url)
    for _ in range(5):  # five browser sessions inside the cache window
        run()
    gets = [p for _, p in gw.requests]
    assert gets.count("/health") == 1 and gets.count("/devices") == 1 and gets.count("/commands") == 1
