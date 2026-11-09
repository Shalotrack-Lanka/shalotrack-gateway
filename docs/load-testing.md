# Gateway load testing and capacity

Scope: the Python asyncio gateway (`tcp_server.py`). Not the .NET API, not the admin portal.

## 1. How to test

### Safety rules (read first)
The 28 Oct incident ran a test suite against the production database. The tooling therefore:
- reads only `BENCH_DATABASE_URL` (never `DATABASE_URL` or `.env`);
- refuses any host containing `shalotrack`, and any non-private host without `--allow-remote`;
- `bench_seed.py` refuses a non-local DB without `--i-am-sure-this-is-a-test-database`, and `--reset` deletes only IMEIs starting `35517210`.
Never point it at the production Supabase project. Never load-test `/api/auth/otp/request` (sends real SMS).

### A. Local benchmark (free, repeatable)
```bash
# throw-away Postgres 16 (docker or local), then:
export BENCH_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5433/postgres
python test/bench_seed.py --devices 3000 --create-schema
python test/bench_run_case.py --name out/typical3000 --devices 3000 --scenario typical --duration 120 --ramp 20
```
`bench_run_case.py` starts the gateway, runs the fleet simulator and prints gateway CPU ms/packet, RSS and latency percentiles. Linux only. Open-file hard limit must exceed devices + 500 (`ulimit -Hn`).

Scenarios: `typical` (30% moving / 20% idle / 50% parked), `peak` (70/20/10), `parked`. Heartbeat 60 s (assumed, set `--heartbeat-interval`), TK103 30 s (`--tk103-interval`). `--sync-start` logs everyone in at once (reconnect storm). Exit code 1 when p99 > 2000 ms or errors > 0.5%.

### B. Staging (closest to production, cheap)
1. Throw-away Supabase project/branch at the same compute size as production, with `bench_schema.sql` applied (seed with `--i-am-sure-this-is-a-test-database`).
2. Launch one EC2 of the SAME type as the gateway for a few hours, running the gateway with `DATABASE_URL` pointing at the test project. Do not use the production NLB.
3. Run the simulator from a second EC2 in the same VPC/region (`--allow-remote`, target the private IP). Above ~28k connections per source IP you need several source IPs (`--local-addrs`).
4. Watch: CloudWatch `CPUUtilization`, `CPUCreditBalance`, `CPUSurplusCreditBalance`, memory, and Supabase dashboard (connections, CPU).
Cost: a few dollars. Do this before any capacity decision; the numbers below are from a sandbox, not a t3.

### D. Isolated run on the production gateway EC2 (no new resources, no production data)
Only while the product is not public. `test/prod_host_isolated_run.sh` starts a throw-away Postgres, a test gateway built from the SAME image the production container runs, and the simulator, all on a private Docker network with no published port. Production Supabase, NLB, port 8000 and SSM secrets are not touched; test containers get low CPU weight and the highest OOM score so the real gateway wins under pressure. It aborts if more than 15 real devices are connected, memory/disk is short, or `DATABASE_URL` is set.
1. EC2 console: note `CPUCreditBalance`. Connect with Session Manager, `sudo -i`.
2. Put the kit in `/opt/loadtest` (files: `test/load_test_fleet.py`, `test/bench_seed.py`, `test/bench_schema.sql`, `test/prod_host_isolated_run.sh`).
3. `DRY_RUN=1 I_UNDERSTAND_THIS_RUNS_ON_THE_PRODUCTION_HOST=yes bash /opt/loadtest/test/prod_host_isolated_run.sh` (guards only).
4. Real runs, one at a time, smallest first: `DEVICES=500`, then `1000`, then `1500` (a t3.micro has 1 GB RAM; do not go higher there).
5. Afterwards check `CPUCreditBalance`, `docker ps` (only `shalotrack-gateway` left) and `bash ... --cleanup` if anything remains.
Not covered: Supabase network latency, multi-gateway command routing.

### C. What to look for
- Latency p99 per message type (ACK latency includes the DB write).
- Gateway CPU % of ONE core (single process, it can never use more).
- Reconnect storm: all devices logging in together (after deploy, NLB failover, or a network outage). This is the worst case, test it.
- t3 credit balance falling over a 2-hour soak.

## 2. Measured results (sandbox, local Postgres, NOT a t3, NOT Supabase)

| Case | Devices | Pkts/s | Gateway CPU (1 core) | ms CPU/packet | RSS | p99 login | p99 position ACK | SLO |
|---|---|---|---|---|---|---|---|---|
| typical, DEBUG log | 1000 | 34 | 6.6% | 1.93 | 40 MB | 12 ms | 8 ms | pass |
| typical, INFO log | 1000 | 34 | 6.5% | 1.88 | 40 MB | 13 ms | 7 ms | pass |
| typical | 3000 | 98 | 15.3% | 1.56 | 58 MB | 11 ms | 7 ms | pass |
| peak | 3000 | 137 | 17.2% | 1.26 | 58 MB | 12 ms | 8 ms | pass |
| reconnect storm (all 3000 log in at once) | 3000 | - | CPU-bound | - | 62 MB | 8.9 s | 3.8 s | FAIL (2 timeouts) |

Storm with DB pool 15 vs 60: no improvement (login p99 6.9 s vs 8.4 s). The bottleneck is the single event loop, not the pool.

## 3. Capacity of the current gateway

Facts from the repo and IaC: single process, ~1 core; `MAX_CONNECTIONS` default and SSM = 500 (hard cap); launch template `t3.micro` (README says t3.small, confirm); ASG min 1 / max 3 but only one instance is useful because `gateway.shalotrack.internal` resolves to one instance, so commands cannot reach devices on another.

| Limit | Value | Reached at |
|---|---|---|
| `MAX_CONNECTIONS` = 500 | hard cap, device 501 is refused | **500 devices, today** |
| Open-file limit (check systemd/Docker `nofile`) | often 1024 | ~1000 devices |
| t3.micro CPU credits (baseline 20% of one vCPU, assumed) | sandbox 15% at 3000 devices; apply 2x for slower t3 core = 30% | ~2000 devices sustained without paying for surplus credits |
| Single core, steady state | ~650 pkt/s sandbox = ~20k devices; ~10k with 2x margin | not the first limit |
| Memory (20-40 KB/connection) | 3000 devices = 60 MB | never |
| Reconnect storm | ~3000 logins = 9 s p99 sandbox, worse on real Supabase (5-8 statements per login x network RTT) | ~1500-2000 devices before a restart is visible to users |

Plain answer: after setting `MAX_CONNECTIONS` (and nofile) properly, INFO logging and t3.small, the current design is comfortable to about 2,000 devices and workable to about 3,000-5,000 on steady traffic. The real risk is not steady load, it is restarts and failover.

## 4. When to move to the 25k target architecture

| Devices | Do | Why |
|---|---|---|
| now - 2k | Raise `MAX_CONNECTIONS` to 10000 and nofile to 65535, `LOG_LEVEL=INFO`, t3.small, merge the single-statement V5 heartbeat, alert on `CPUCreditBalance` | Free fixes; today's cap is 500 |
| 2k - 5k | Phase 2: Redis device-to-gateway map + command routing, then run 2+ gateways behind the NLB; shorter TG deregistration delay | Availability and zero-downtime deploys. A restart with 3000 devices is a 10 s login storm; with two gateways half the fleet reconnects |
| 5k - 10k | Batched inserts (queue + worker), bigger Supabase compute, partition `GpsTrackings`, retention/archive | ~5k devices write ~8M rows/day; 25k writes ~41M/day (~1.2B/month) |
| 10k - 25k | Full diagram: ASG of gateways, Kinesis/SQS, Fargate ingest workers, partitioned Supabase + S3 archive, Redis live pub/sub, SignalR | Single-writer-per-packet DB path stops scaling |

Decision rule: build Redis command routing and the second gateway at ~2,500-3,000 devices (or earlier if any outage is unacceptable), the queue/batch ingest at ~5,000, the rest only if you really approach 10k+.

## 5. Limits of these numbers
Sandbox CPU is not a t3; local Postgres is not Supabase (no network RTT, no shared CPU, no Supavisor limits; Supabase transaction pooler default pool size is 15 on small computes); heartbeat interval is an assumption; no carrier NAT or 2G behaviour; single runs, not averaged. Re-run section 1B on real infrastructure before committing money.

## 6. Confirm these on the real system
```bash
aws ec2 describe-instances --filters Name=tag:Name,Values=*gateway* --query 'Reservations[].Instances[].InstanceType'
aws ssm get-parameter --name <path>/MAX_CONNECTIONS      # and DB_POOL_MAX, LOG_LEVEL
ulimit -n   # inside the running container (docker exec ... sh -c 'ulimit -n')
```
Also confirm the Supabase compute size and the T3 credit mode (`aws ec2 describe-instance-credit-specifications`).
