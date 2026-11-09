"""
Run ONE local benchmark case and print gateway CPU / memory next to the latency
numbers: start the gateway process, apply load_test_fleet.py, sample the gateway's
CPU time and RSS from /proc, print a JSON summary. Linux only (/proc).

    BENCH_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/postgres \\
        python test/bench_run_case.py --name x --devices 3000 --scenario peak --duration 120

Use it to compare code changes (CPU ms per packet before/after). It starts the
gateway itself with the settings below, so it never reads a .env or the real
DATABASE_URL. CPU numbers are per-core CPU seconds of the gateway process only:
the load generator and Postgres share the machine, so wall-clock throughput here
says little; CPU-per-packet is what transfers to another machine.
Seed the database first with test/bench_seed.py.
"""
import argparse, asyncio, json, os, resource, signal, subprocess, sys, time
import asyncpg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TCK = os.sysconf("SC_CLK_TCK")


def cpu_s(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TCK


def mem_mb(pid, key):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith(key):
            return int(line.split()[1]) / 1024


async def main(a):
    dsn = os.environ.get("BENCH_DATABASE_URL")
    if not dsn:
        sys.exit("Set BENCH_DATABASE_URL (a TEST database seeded by test/bench_seed.py).")
    from bench_seed import host_is_local
    if not host_is_local(dsn):
        sys.exit("Refusing: BENCH_DATABASE_URL is not a local/private host.")

    c = await asyncpg.connect(dsn)
    for t in ("GpsTrackings", "RawPackets", "DeviceEvents", "DeviceStatuses", "CurrentLocations"):
        await c.execute(f'DELETE FROM "{t}"')
    await c.close()

    env = dict(os.environ, DATABASE_URL=dsn, HOST="127.0.0.1", PORT=str(a.port), LOG_LEVEL=a.log_level,
               DB_POOL_MIN="5", DB_POOL_MAX=str(a.pool), MAX_CONNECTIONS="60000", PYTHONUNBUFFERED="1",
               ENABLED_PROTOCOLS="GT06,GPS103")
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    want = 65000 if hard == resource.RLIM_INFINITY else min(65000, hard)
    if want < a.devices + 500:
        sys.exit(f"Open-file hard limit is {want}; need about {a.devices + 500}. Raise it (ulimit -Hn) first.")
    logf = open(f"{a.name}.gateway.log", "w")
    gw = subprocess.Popen([sys.executable, "tcp_server.py"], cwd=ROOT, env=env, stdout=logf,
                          stderr=subprocess.STDOUT,
                          preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (want, want)))
    await asyncio.sleep(4)
    if gw.poll() is not None:
        sys.exit("gateway did not start: see " + f"{a.name}.gateway.log")

    base, t0 = cpu_s(gw.pid), time.time()
    cmd = [sys.executable, os.path.join(HERE, "load_test_fleet.py"), "--devices", str(a.devices),
           "--scenario", a.scenario, "--duration", str(a.duration), "--ramp-seconds", str(a.ramp),
           "--report-every", "20", "--json-out", f"{a.name}.json"] + (["--sync-start"] if a.sync else [])
    lt = subprocess.Popen(cmd, stdout=open(f"{a.name}.load.txt", "w"), stderr=subprocess.STDOUT)
    while lt.poll() is None:
        await asyncio.sleep(2)
    el, cpu = time.time() - t0, cpu_s(gw.pid) - base
    res = json.load(open(f"{a.name}.json"))
    peak = mem_mb(gw.pid, "VmHWM")
    gw.send_signal(signal.SIGTERM)
    try:
        gw.wait(10)
    except Exception:
        gw.kill()

    n = max(res["packets_sent"], 1)
    out = dict(case=a.name, devices=a.devices, scenario=a.scenario, log_level=a.log_level, pool=a.pool,
               seconds=round(el), packets=res["packets_sent"], packets_per_s=round(n / el, 1),
               gateway_cpu_s=round(cpu, 1), gateway_cpu_pct_of_one_core=round(100 * cpu / el, 1),
               cpu_ms_per_packet=round(1000 * cpu / n, 3), rss_mb_peak=round(peak),
               peak_connected=res["peak_connected"], errors=res["errors"], reconnects=res["reconnects"],
               latency_ms={k: {q: round(v[q], 1) for q in ("p50", "p95", "p99", "max")}
                           for k, v in res["latency_ms"].items()}, slo_pass=res["pass"])
    print(json.dumps(out, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--devices", type=int, required=True)
    p.add_argument("--scenario", default="typical")
    p.add_argument("--duration", type=int, default=120)
    p.add_argument("--ramp", type=int, default=30)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--pool", type=int, default=15, help="gateway DB_POOL_MAX (15 = Supabase default pool size)")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--sync", action="store_true", help="reconnect-storm: everything logs in at once")
    sys.exit(asyncio.run(main(p.parse_args())))
