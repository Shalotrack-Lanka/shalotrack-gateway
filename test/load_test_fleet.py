"""
Fleet load test for the gateway: thousands of simulated GT06/V5 and GPS103/TK103
devices behaving like real ones, with end-to-end latency numbers.

What each simulated device does (asyncio, one TCP connection per device):

  GT06/V5   login, then position (0x12) on its own tier, heartbeat (0x23) every
            --heartbeat-interval. The tiers match what real V5 units do:
            moving 20 s, ignition-on-stationary 70 s, parked 300 s.
  GPS103    login, heartbeat (the IMEI followed by ';') every --heartbeat-interval, position
            every --tk103-interval (30 s is what the real unit is set to).

Latency is measured as "packet written -> ACK read". The gateway sends its ACK
AFTER the database write, so GT06 position/heartbeat ACK time is the end-to-end
cost of one packet including the database. GPS103 position reports get no ACK
from the gateway, so only GPS103 login and heartbeat are timed.

    # 1) a test database seeded with the same split (80% GT06 by default)
    BENCH_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5433/postgres \\
        python test/bench_seed.py --devices 2000 --create-schema
    # 2) a gateway pointed at THAT database (see docs/load-testing.md)
    # 3) the load
    python test/load_test_fleet.py --devices 2000 --scenario typical --duration 300

SAFETY. Never aim this at production. Test IMEIs (prefix 35517210) are unknown to
the production allow-list, so production would reject them, but a flood of rejected
logins is still an attack on a live service. The script therefore refuses:
  * any host name containing "shalotrack" (production DNS), always;
  * any other non-loopback / non-private host unless --allow-remote is given
    (for a dedicated staging gateway you own).
Exit code is 1 when the run breaches the SLO (see --slo-p99-ms / --slo-max-error-pct).
"""

import argparse
import asyncio
import ipaddress
import json
import random
import resource
import statistics
import sys
import time
from datetime import datetime, timezone

PREFIX = "35517210"

# share of devices that are (moving, ignition-on-stationary, parked)
SCENARIOS = {
    "typical": (0.30, 0.20, 0.50),   # an ordinary working day
    "peak":    (0.70, 0.20, 0.10),   # rush hour: most vehicles moving
    "parked":  (0.00, 0.10, 0.90),   # night: mostly parked (connection-count test)
}
TIER_SECONDS = {"moving": 20, "idle": 70, "parked": 300}   # real V5 position intervals


# ---------------------------------------------------------------------------
# packet builders (GT06 framing identical to test/load_test_concurrent.py)
# ---------------------------------------------------------------------------

def crc16_x25(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return (crc ^ 0xFFFF) & 0xFFFF


def _gt06(body: bytes) -> bytes:
    length = len(body) + 2
    crc = crc16_x25(bytes([length]) + body)
    return b"\x78\x78" + bytes([length]) + body + crc.to_bytes(2, "big") + b"\x0D\x0A"


def gt06_login(imei: str, serial: int) -> bytes:
    return _gt06(bytes([0x01]) + bytes.fromhex(imei.zfill(16)) + (serial & 0xFFFF).to_bytes(2, "big"))


def gt06_location(serial: int, lat: float, lon: float, speed: int, heading: int) -> bytes:
    now = datetime.now(timezone.utc)
    dt = bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])
    course = (heading & 0x03FF) | 0x0400 | 0x1000
    payload = (dt + bytes([0x0C]) + int(abs(lat) * 1_800_000).to_bytes(4, "big")
               + int(abs(lon) * 1_800_000).to_bytes(4, "big") + bytes([speed & 0xFF])
               + course.to_bytes(2, "big"))
    return _gt06(bytes([0x12]) + payload + (serial & 0xFFFF).to_bytes(2, "big"))


def gt06_heartbeat(serial: int, ignition: bool) -> bytes:
    terminal = 0x41 | (0x02 if ignition else 0)          # GPS tracking on, activated, ignition bit
    body = bytes([0x23, terminal, 0x04, 0x04, 0x00, 0x01]) + (serial & 0xFFFF).to_bytes(2, "big")
    return _gt06(body)


def gps103_login(imei: str) -> bytes:
    return f"##,imei:{imei},A;".encode()


def gps103_heartbeat(imei: str) -> bytes:
    # The unit sends the IMEI followed by ';'. Without the ';' the gateway's framer
    # (correctly) waits for more bytes and never replies.
    return f"{imei};".encode()


def gps103_position(imei: str, lat: float, lon: float, speed: float) -> bytes:
    now = datetime.now(timezone.utc)
    ddmm = lambda v, w: f"{int(abs(v)):0{w}d}{(abs(v) % 1) * 60:07.4f}"
    return (f"imei:{imei},tracker,{now:%y%m%d%H%M%S},,F,{now:%H%M%S}.000,A,"
            f"{ddmm(lat, 2)},N,{ddmm(lon, 3)},E,{speed:.2f},90;").encode()


async def read_gt06_ack(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readexactly(3)
    return head + await reader.readexactly(head[2] + 2)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.lat = {}          # op -> [ms]  (whole run)
        self.window = {}       # op -> [ms]  (since the last progress line)
        self.errors = {}       # kind -> count
        self.sent = 0
        self.window_sent = 0
        self.connected = 0
        self.peak_connected = 0
        self.reconnects = 0

    def observe(self, op: str, ms: float):
        self.lat.setdefault(op, []).append(ms)
        self.window.setdefault(op, []).append(ms)

    def error(self, kind: str):
        self.errors[kind] = self.errors.get(kind, 0) + 1

    def packet(self):
        self.sent += 1
        self.window_sent += 1


def pct(values, q):
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))]


# ---------------------------------------------------------------------------
# one simulated device
# ---------------------------------------------------------------------------

async def device(idx, cfg, stats, stop, t_start, local_addr):
    imei = f"{PREFIX}{idx:07d}"
    is_gt06 = idx <= cfg.gt06_count
    rnd = random.Random(idx)
    r = rnd.random()
    m, i_, _p = SCENARIOS[cfg.scenario]
    tier = "moving" if r < m else "idle" if r < m + i_ else "parked"
    pos_every = TIER_SECONDS[tier] if is_gt06 else cfg.tk103_interval
    lat, lon = 6.90 + rnd.random() * 0.1, 79.85 + rnd.random() * 0.1
    serial = 1

    await asyncio.sleep(cfg.ramp_seconds * idx / cfg.devices if cfg.ramp_seconds else 0)

    while not stop.is_set():
        writer = None
        try:
            t0 = time.monotonic()
            kw = {"local_addr": (local_addr, 0)} if local_addr else {}
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cfg.host, cfg.port, **kw), cfg.timeout)
            if is_gt06:
                writer.write(gt06_login(imei, serial)); serial += 1
                await writer.drain()
                ack = await asyncio.wait_for(read_gt06_ack(reader), cfg.timeout)
                if ack[3] != 0x01:
                    raise RuntimeError("bad login ack")
            else:
                writer.write(gps103_login(imei))
                await writer.drain()
                ack = await asyncio.wait_for(reader.read(64), cfg.timeout)
                if not ack.startswith(b"LOAD"):
                    raise RuntimeError("bad login reply")
            stats.observe("login", (time.monotonic() - t0) * 1000)
            stats.connected += 1
            stats.peak_connected = max(stats.peak_connected, stats.connected)

            # first packet at a random offset inside each period: real fleets are not in lock-step
            jit = (lambda p: 0.0) if cfg.sync_start else (lambda p: rnd.random() * p)
            next_pos = time.monotonic() + jit(pos_every)
            next_hb = time.monotonic() + jit(cfg.heartbeat_interval)

            try:
                while not stop.is_set():
                    now = time.monotonic()
                    due = min(next_pos, next_hb)
                    if due > now:
                        try:
                            await asyncio.wait_for(stop.wait(), due - now)
                            break
                        except asyncio.TimeoutError:
                            pass
                    send_pos = time.monotonic() >= next_pos
                    t1 = time.monotonic()
                    if send_pos:
                        speed = 0 if tier == "parked" else (rnd.randint(20, 60) if tier == "moving" else 0)
                        if tier == "moving":
                            lat += 0.0002 * rnd.random(); lon += 0.0002 * rnd.random()
                        if is_gt06:
                            writer.write(gt06_location(serial, lat, lon, speed, rnd.randint(0, 359)))
                        else:
                            writer.write(gps103_position(imei, lat, lon, float(speed)))
                        next_pos += pos_every
                        op = "gt06_position"
                    else:
                        if is_gt06:
                            writer.write(gt06_heartbeat(serial, tier != "parked"))
                        else:
                            writer.write(gps103_heartbeat(imei))
                        next_hb += cfg.heartbeat_interval
                        op = "gt06_heartbeat" if is_gt06 else "gps103_heartbeat"
                    serial += 1
                    await writer.drain()
                    stats.packet()
                    if is_gt06:
                        await asyncio.wait_for(read_gt06_ack(reader), cfg.timeout)
                        stats.observe(op, (time.monotonic() - t1) * 1000)
                    elif op == "gps103_heartbeat":
                        await asyncio.wait_for(reader.read(64), cfg.timeout)   # "ON"
                        stats.observe(op, (time.monotonic() - t1) * 1000)
            finally:
                stats.connected -= 1
        except asyncio.TimeoutError:
            stats.error("timeout")
        except (ConnectionError, asyncio.IncompleteReadError):
            stats.error("connection_reset")
        except OSError as ex:
            stats.error(f"os_error:{ex.errno}")
        except Exception as ex:                                   # noqa: BLE001
            stats.error(type(ex).__name__)
        finally:
            if writer is not None:
                writer.close()
        if stop.is_set():
            return
        stats.reconnects += 1
        await asyncio.sleep(1 + rnd.random() * 4)                 # devices retry within seconds


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def refuse_unsafe_target(host: str, allow_remote: bool):
    if "shalotrack" in host.lower():
        sys.exit("Refusing: that host name is production. Load tests run against a dedicated "
                 "staging gateway and a test database only.")
    try:
        ip = ipaddress.ip_address(host)
        local = ip.is_loopback or ip.is_private
    except ValueError:
        local = host == "localhost"
    if not local and not allow_remote:
        sys.exit("Refusing: host is not loopback/private. For a dedicated staging gateway you own, "
                 "add --allow-remote.")


def raise_fd_limit(needed: int):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = min(hard, needed + 256) if hard != resource.RLIM_INFINITY else needed + 256
    if soft < want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < needed + 64:
        sys.exit(f"Open-file limit is {soft}; need about {needed + 64}. Run `ulimit -n {needed + 1024}` first.")


async def progress(cfg, stats, stop, t_start):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), cfg.report_every)
            return
        except asyncio.TimeoutError:
            pass
        gt = [v for k in ("gt06_position", "gt06_heartbeat") for v in stats.window.get(k, [])]
        el = time.monotonic() - t_start
        print(f"[{el:6.0f}s] connected {stats.connected:6d}  sent {stats.window_sent / cfg.report_every:7.1f}/s  "
              f"gt06 ack p50 {pct(gt, 50):7.1f} p95 {pct(gt, 95):7.1f} p99 {pct(gt, 99):7.1f} ms  "
              f"errors {sum(stats.errors.values())}  reconnects {stats.reconnects}", flush=True)
        stats.window.clear()
        stats.window_sent = 0


async def run(cfg):
    stats, stop, t_start = Stats(), asyncio.Event(), time.monotonic()
    addrs = [a for a in (cfg.local_addrs or "").split(",") if a] or [None]
    tasks = [asyncio.create_task(device(i, cfg, stats, stop, t_start, addrs[i % len(addrs)]))
             for i in range(1, cfg.devices + 1)]
    prog = asyncio.create_task(progress(cfg, stats, stop, t_start))
    try:
        await asyncio.sleep(cfg.duration)
    finally:
        stop.set()
        await asyncio.gather(*tasks, prog, return_exceptions=True)
    return stats, time.monotonic() - t_start


def summarise(cfg, stats, elapsed):
    rows, out = [], {}
    for op in ("login", "gt06_position", "gt06_heartbeat", "gps103_heartbeat"):
        v = stats.lat.get(op, [])
        if v:
            out[op] = {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99), "max": max(v),
                       "mean": statistics.fmean(v)}
            rows.append(f"  {op:18s} n={len(v):8d}  p50 {pct(v, 50):8.1f}  p95 {pct(v, 95):8.1f}  "
                        f"p99 {pct(v, 99):8.1f}  max {max(v):8.1f} ms")
    total_err = sum(stats.errors.values())
    err_pct = 100 * total_err / max(1, stats.sent + stats.peak_connected)
    acks = [x for k in ("gt06_position", "gt06_heartbeat") for x in stats.lat.get(k, [])]
    p99 = pct(acks, 99) if acks else float("nan")
    ok = (not acks or p99 <= cfg.slo_p99_ms) and err_pct <= cfg.slo_max_error_pct and stats.peak_connected > 0
    print("\n" + "=" * 78)
    print(f"RESULT  devices={cfg.devices} scenario={cfg.scenario} duration={elapsed:.0f}s "
          f"gt06_share={cfg.gt06_count}/{cfg.devices}")
    print("=" * 78)
    print("\n".join(rows) or "  (no acknowledged packets)")
    print(f"  peak connected     {stats.peak_connected}")
    print(f"  packets sent       {stats.sent}  ({stats.sent / max(elapsed, 1):.1f}/s average)")
    print(f"  errors             {total_err}  ({err_pct:.2f}%)  {stats.errors or ''}")
    print(f"  reconnects         {stats.reconnects}")
    print(f"  SLO                gt06 ack p99 <= {cfg.slo_p99_ms} ms and errors <= {cfg.slo_max_error_pct}%  ->  "
          f"{'PASS' if ok else 'FAIL'}")
    if cfg.json_out:
        with open(cfg.json_out, "w", encoding="utf-8") as f:
            json.dump({"devices": cfg.devices, "scenario": cfg.scenario, "duration_s": elapsed,
                       "peak_connected": stats.peak_connected, "packets_sent": stats.sent,
                       "errors": stats.errors, "reconnects": stats.reconnects, "latency_ms": out,
                       "pass": ok}, f, indent=2)
    return 0 if ok else 1


def parse():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--devices", type=int, default=500)
    p.add_argument("--gt06-share", type=float, default=0.8,
                   help="fraction GT06/V5 (must equal the share used with bench_seed.py)")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="typical")
    p.add_argument("--duration", type=float, default=120, help="seconds to hold the load")
    p.add_argument("--ramp-seconds", type=float, default=30,
                   help="spread the initial logins over this many seconds (0 = reconnect storm)")
    p.add_argument("--heartbeat-interval", type=float, default=60)
    p.add_argument("--tk103-interval", type=float, default=30)
    p.add_argument("--sync-start", action="store_true", help="all devices send at the same instants")
    p.add_argument("--timeout", type=float, default=10, help="seconds before an ACK counts as a timeout")
    p.add_argument("--report-every", type=float, default=10)
    p.add_argument("--local-addrs", help="comma-separated local IPs to spread connections over "
                   "(one source IP gives only ~28,000 ports to one destination)")
    p.add_argument("--slo-p99-ms", type=float, default=2000)
    p.add_argument("--slo-max-error-pct", type=float, default=0.5)
    p.add_argument("--json-out")
    p.add_argument("--allow-remote", action="store_true")
    cfg = p.parse_args()
    cfg.gt06_count = round(cfg.devices * cfg.gt06_share)
    return cfg


if __name__ == "__main__":
    cfg = parse()
    refuse_unsafe_target(cfg.host, cfg.allow_remote)
    raise_fd_limit(cfg.devices)
    try:
        stats, elapsed = asyncio.run(run(cfg))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(summarise(cfg, stats, elapsed))
