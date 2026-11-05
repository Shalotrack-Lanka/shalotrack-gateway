"""
ShaloTrack Gateway Console: read-only operations view of the TCP gateway.

Run behind Cloudflare Access (see README). The page never sends anything to a device.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import streamlit as st

from gwconsole import present
from gwconsole.auth import AuthError, make_key_client, verify_access_jwt
from gwconsole.config import AUTH_NONE, ConfigError, load_settings
from gwconsole.gateway_client import GatewayClient, GatewayError

log = logging.getLogger("gwconsole")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

st.set_page_config(page_title="ShaloTrack Gateway Console", page_icon="📡", layout="wide")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
STATE_BADGE = {"active": "🟢 active", "quiet": "🟡 quiet", "stale": "🔴 stale"}
ERROR_HINTS = {
    "unreachable": "The console cannot connect to the gateway's Command API (port 8001). "
                   "Check that the gateway instance is running and that this host may reach it.",
    "timeout": "The gateway's Command API did not answer in time.",
    "unauthorized": "The gateway rejected the console's COMMAND_API_KEY.",
    "bad_response": "The gateway answered, but not in the expected format.",
    "http_error": "The gateway answered with an unexpected HTTP status.",
    "internal": "The console hit an internal error while reading the gateway (see server log).",
}


# --- shared, process-wide resources -------------------------------------------------
@st.cache_resource
def get_settings():
    return load_settings()


@st.cache_resource
def get_client() -> GatewayClient:
    s = get_settings()
    return GatewayClient(s.gateway_url, s.api_key)


@st.cache_resource
def get_key_client():
    return make_key_client(get_settings())


@dataclass(frozen=True)
class Fetched:
    """A gateway read, success OR failure, so a failure is cached too and N open tabs
    cannot turn a gateway outage into N timeouts per refresh."""
    ok: bool
    data: object = None
    error: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _safe(name: str, fn) -> Fetched:
    try:
        return Fetched(True, data=fn())
    except GatewayError as exc:
        log.warning("gateway %s read failed: %s", name, exc)
        return Fetched(False, error=exc.kind)
    except Exception:  # noqa: BLE001 - never let a bug take the page down or leak a traceback
        log.exception("unexpected error reading gateway %s", name)
        return Fetched(False, error="internal")


# One shared read per TTL for ALL viewers. The gateway's own /health is also cached, but
# the console must not multiply load on a gateway that is already struggling.
@st.cache_data(ttl=10, show_spinner=False)
def fetch_health() -> Fetched:
    return _safe("health", get_client().health)


@st.cache_data(ttl=5, show_spinner=False)
def fetch_devices() -> Fetched:
    return _safe("devices", get_client().devices)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_commands() -> Fetched:
    return _safe("commands", get_client().commands)


# --- access control -------------------------------------------------------------------
def require_identity(settings) -> str:
    """Return the viewer's email, or stop the script. Fails closed."""
    if settings.auth_mode == AUTH_NONE:
        # Development only. Refuse unless the server is bound to loopback, so this mode
        # can never be reachable from the network (it is also refused inside Docker, where
        # the server must bind 0.0.0.0).
        if (st.get_option("server.address") or "") not in LOOPBACK:
            st.error("Refusing to run without authentication on a non-loopback address.")
            st.stop()
        return "developer@localhost"

    ident = st.session_state.get("identity")
    if ident and time.time() < ident.expires_at:
        return ident.email

    token = st.context.headers.get("Cf-Access-Jwt-Assertion")
    try:
        ident = verify_access_jwt(token, settings, get_key_client())
    except AuthError as exc:
        st.session_state.pop("identity", None)
        log.warning("access denied: %s", exc.reason)
        st.error("Access denied. If your sign-in expired, reload this page to sign in again.")
        st.stop()
    st.session_state["identity"] = ident
    log.info("access granted email=%s", ident.email)
    return ident.email


# --- rendering -------------------------------------------------------------------------
def show_failure(result: Fetched) -> None:
    st.error(ERROR_HINTS.get(result.error, ERROR_HINTS["internal"]))
    st.caption(
        "This only means the management port cannot be read. GPS devices talk to a different "
        "port and may still be connecting normally."
    )


def render_overview(health: Fetched, devices: Fetched, tz: str) -> None:
    if not health.ok:
        show_failure(health)
        return
    report = health.data
    level, headline = present.health_headline(report)
    {"ok": st.success, "degraded": st.warning, "down": st.error}[level](f"**{headline}**")
    for reason in report.get("reasons") or []:
        st.markdown(f"- {reason}")

    db = (report.get("checks") or {}).get("database") or {}
    pool = db.get("pool") if isinstance(db.get("pool"), dict) and {"size", "idle", "max"} <= db["pool"].keys() else None
    db_ms = db.get("ms")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Devices connected", report.get("devices_connected", "n/a"))
    c2.metric("Last valid packet", present.fmt_duration(report.get("last_packet_age_s")) + (
        " ago" if report.get("last_packet_age_s") is not None else ""))
    c3.metric("Gateway uptime", present.fmt_duration(report.get("uptime_s")))
    c4.metric("Database latency", present.fmt_ms(db_ms),
              help=f"Pool: {pool['size'] - pool['idle']} in use / {pool['max']} max" if pool else None)

    st.subheader("Checks")
    st.dataframe(present.check_rows(report), hide_index=True, width="stretch")
    with st.expander("Raw health report"):
        st.json(report)
    st.caption(f"Health read at {present.local_time(health.at, tz)} ({tz}). Shared cache: 10 s.")


def render_devices(devices: Fetched, tz: str) -> None:
    if not devices.ok:
        show_failure(devices)
        return
    rows = present.device_rows(devices.data, datetime.now(timezone.utc))
    counts = present.state_counts(rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Connected", len(rows))
    c2.metric("🟢 Active", counts["active"], help="Packet within the last 10 minutes")
    c3.metric("🟡 Quiet", counts["quiet"], help="Last packet 10 to 30 minutes ago")
    c4.metric("🔴 Stale", counts["stale"], help="Connected, but no packet for over 30 minutes")

    f1, f2, f3 = st.columns([2, 1, 1])
    needle = f1.text_input("Search IMEI or IP", key="dev_search", max_chars=40).strip().lower()
    protocols = sorted({r["Protocol"] for r in rows})
    chosen_protocols = f2.multiselect("Protocol", protocols, default=protocols, key="dev_protocols")
    states = f3.multiselect("State", ["active", "quiet", "stale"],
                            default=["active", "quiet", "stale"], key="dev_states")

    shown = [
        r for r in rows
        if r["Protocol"] in chosen_protocols and r["State"] in states
        and (not needle or needle in r["IMEI"].lower() or needle in r["IP"].lower())
    ]
    table = [{**{k: v for k, v in r.items() if k != "_idle"}, "State": STATE_BADGE[r["State"]]} for r in shown]
    if table:
        st.dataframe(table, hide_index=True, width="stretch")
    else:
        st.info("No devices match." if rows else "No devices are connected to the gateway right now.")
    st.caption(f"Showing {len(shown)} of {len(rows)}. Sorted by longest silence first. "
               f"Read at {present.local_time(devices.at, tz)} ({tz}). Shared cache: 5 s.")


def render_commands(commands: Fetched) -> None:
    st.info(
        "**This console is read-only and cannot send commands.** Device commands are sent from the "
        "Admin portal, which checks the user's role and writes an audit record for every command."
    )
    if not commands.ok:
        show_failure(commands)
        return
    groups = present.group_commands(commands.data)
    titles = {
        "query": "Queries (ask the device to report)",
        "config": "Configuration (change device settings)",
        "control": "Control (act on the vehicle or device)",
        "other": "Other / newly added",
    }
    cols = st.columns(2)
    for i, key in enumerate(["query", "config", "control", "other"]):
        if not groups[key]:
            continue
        with cols[i % 2]:
            st.markdown(f"**{titles[key]}**")
            for name in groups[key]:
                flag = " ⚠ high impact" if name in present.HIGH_IMPACT else ""
                st.markdown(f"- `{name}`{flag}")


def main() -> None:
    try:
        settings = get_settings()
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        st.error("The console is not configured correctly. See the server log.")
        st.stop()

    who = require_identity(settings)

    with st.sidebar:
        st.markdown("### ShaloTrack Gateway")
        st.caption("Operations console · **read-only**")
        st.write(f"Signed in as  \n`{who}`")
        options = sorted({5, 10, 30, 60, settings.refresh_seconds})
        refresh = st.selectbox("Auto-refresh (seconds)", options, index=options.index(settings.refresh_seconds))
        st.caption(f"Gateway: `{urlsplit(settings.gateway_url).hostname}`")

    st.title("Gateway console")

    @st.fragment(run_every=refresh)
    def live() -> None:
        tab_overview, tab_devices, tab_commands = st.tabs(["Overview", "Devices", "Commands"])
        with tab_overview:
            render_overview(fetch_health(), fetch_devices(), settings.timezone)
        with tab_devices:
            render_devices(fetch_devices(), settings.timezone)
        with tab_commands:
            render_commands(fetch_commands())

    live()


main()
