from datetime import datetime, timedelta, timezone

import pytest

from gwconsole import present
from gwconsole.gateway_client import Device

NOW = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("seconds,text", [
    (None, "n/a"), (0, "0 s"), (59, "59 s"), (60, "1 min"), (3599, "59 min"),
    (3600, "1 h"), (3660, "1 h 1 min"), (86400, "1 d"), (90000, "1 d 1 h"), (-5, "0 s"),
])
def test_fmt_duration(seconds, text):
    assert present.fmt_duration(seconds) == text


@pytest.mark.parametrize("idle,state", [(0, "active"), (600, "active"), (601, "quiet"), (1800, "quiet"), (1801, "stale")])
def test_device_state_boundaries(idle, state):
    assert present.device_state(idle) == state


def _dev(imei, idle, proto="GT06"):
    return Device(imei, "d", "1.1.1.1", proto, NOW - timedelta(hours=2), NOW - timedelta(seconds=idle))


def test_rows_sorted_longest_silence_first_and_counted():
    rows = present.device_rows([_dev("a", 10), _dev("b", 5000), _dev("c", 900)], NOW)
    assert [r["IMEI"] for r in rows] == ["b", "c", "a"]
    assert present.state_counts(rows) == {"active": 1, "quiet": 1, "stale": 1}
    assert rows[0]["Last packet"] == "1 h 23 min ago"


def test_future_last_seen_clamped():
    rows = present.device_rows([_dev("a", -30)], NOW)  # clock skew between gateway and console
    assert rows[0]["State"] == "active" and rows[0]["Last packet"] == "0 s ago"


def test_group_commands_puts_unknown_in_other():
    g = present.group_commands(["where", "relay_off", "timer", "brand_new_cmd"])
    assert g["query"] == ["where"] and g["control"] == ["relay_off"]
    assert g["config"] == ["timer"] and g["other"] == ["brand_new_cmd"]


def test_high_impact_commands_are_flagged():
    assert {"relay_off", "server", "apn"} <= present.HIGH_IMPACT


def test_local_time_colombo():
    assert present.local_time(NOW, "Asia/Colombo") == "2026-11-05 15:30:00"


def test_headline():
    assert present.health_headline({"status": "ok"})[0] == "ok"
    assert present.health_headline({"status": "degraded"})[0] == "degraded"
    assert present.health_headline({"status": "down"})[0] == "down"
    assert present.health_headline({})[0] == "down"  # unknown is treated as the worst case


def test_check_rows_full_and_degenerate():
    report = {"checks": {"event_loop": {"ok": True, "ms": 0.4},
                         "database": {"ok": True, "ms": 40, "pool": {"size": 10, "idle": 0, "max": 10}}},
              "last_packet_age_s": 7}
    rows = {r["Check"]: r for r in present.check_rows(report)}
    assert rows["Connection pool"]["Result"] == "EXHAUSTED"
    assert rows["Packet flow"]["Result"] == "pass"
    # a nearly empty report must not raise
    assert present.check_rows({})[0]["Result"] == "FAIL"
    assert present.check_rows({"checks": {"database": {"ok": None, "skipped": "loop down"}}})[1]["Result"] == "skipped"
    assert present.check_rows({"checks": {"database": {"ok": True, "ms": 1, "pool": {"size": 1}}}})  # partial pool ignored


def test_slow_database_is_flagged_like_the_gateway_does():
    rows = {r["Check"]: r for r in present.check_rows({"checks": {"database": {"ok": True, "ms": 2100.0}}})}
    assert rows["Database"]["Result"] == "slow" and rows["Database"]["Detail"] == "SELECT 1 in 2100 ms"
    fast = {r["Check"]: r for r in present.check_rows({"checks": {"database": {"ok": True, "ms": 38.24}}})}
    assert fast["Database"]["Result"] == "pass" and fast["Database"]["Detail"] == "SELECT 1 in 38.2 ms"


@pytest.mark.parametrize("ms,text", [(None, "n/a"), (0.4, "0.4 ms"), (99.94, "99.9 ms"), (100, "100 ms"), (2100.0, "2100 ms")])
def test_fmt_ms(ms, text):
    assert present.fmt_ms(ms) == text
