#!/usr/bin/env bash
# Run ONE isolated load test on the production gateway EC2 (Amazon Linux 2023, Docker).
#
#   * Uses the EXACT image the production container runs (docker inspect), so the
#     numbers describe the code that is really deployed, on the real instance type.
#   * Its own throw-away Postgres container: the production Supabase is never touched.
#     No SSM parameter is read, no production secret is passed to any test container.
#   * No port is published: nothing outside the host can reach the test gateway, and
#     the production container, NLB and port 8000 are not involved.
#   * Test containers have low CPU weight (--cpu-shares 128 vs 1024) and the highest
#     OOM score, so under pressure the kernel gives CPU to, and kills, the TEST first.
#   * Aborts if more than MAX_REAL real devices are connected, memory is short, or the
#     disk is short. Everything is removed on exit (also on Ctrl-C / error).
#
# Run from an AWS Session Manager shell on the gateway instance (no SSH, no open ports):
#
#   sudo -i
#   I_UNDERSTAND_THIS_RUNS_ON_THE_PRODUCTION_HOST=yes DEVICES=1000 \
#       bash /opt/loadtest/test/prod_host_isolated_run.sh
#
# Settings (environment): DEVICES (1000) SCENARIO (typical|peak|parked) DURATION (120 s)
# RAMP (20 s) GT06_SHARE (0.8) LOG_LEVEL (INFO) DB_POOL_MAX (15) MAX_REAL (15)
# KIT (/opt/loadtest) DRY_RUN=1 prints the plan and exits without starting anything.
# Cleanup only:  bash prod_host_isolated_run.sh --cleanup
set -euo pipefail

DEVICES=${DEVICES:-1000}
SCENARIO=${SCENARIO:-typical}
DURATION=${DURATION:-120}
RAMP=${RAMP:-20}
GT06_SHARE=${GT06_SHARE:-0.8}
LOG_LEVEL=${LOG_LEVEL:-INFO}
DB_POOL_MAX=${DB_POOL_MAX:-15}
MAX_REAL=${MAX_REAL:-15}
KIT=${KIT:-/opt/loadtest}
PROD=${PROD:-shalotrack-gateway}
NET=lt-net
OUT="$KIT/out"
PG_IMAGE=${PG_IMAGE:-postgres:16-alpine}
LIMITS=(--cpu-shares 128 --oom-score-adj 900 --ulimit nofile=65535:65535)

die() { echo "ABORT: $*" >&2; exit 2; }

cleanup() {
  docker rm -fv lt-sim lt-gateway lt-pg >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--cleanup" ]; then cleanup; echo "Test containers removed."; exit 0; fi

# --- guards ---------------------------------------------------------------
[ "${I_UNDERSTAND_THIS_RUNS_ON_THE_PRODUCTION_HOST:-}" = "yes" ] \
  || die "set I_UNDERSTAND_THIS_RUNS_ON_THE_PRODUCTION_HOST=yes (this runs on the production host)."
[ -z "${DATABASE_URL:-}" ] || die "DATABASE_URL is set in this shell. Unset it; this test must never see a real database URL."
[ -d "$KIT/test" ] || die "kit not found in $KIT/test (copy docs/load-testing.md's kit files there first)."
[ "$DEVICES" -ge 1 ] 2>/dev/null || die "DEVICES must be a number."

docker inspect "$PROD" >/dev/null 2>&1 || die "production container '$PROD' not found: wrong host?"
[ "$(docker inspect -f '{{.State.Running}}' "$PROD")" = "true" ] || die "production container is not running."
IMAGE=$(docker inspect -f '{{.Image}}' "$PROD")
RESTARTS_BEFORE=$(docker inspect -f '{{.RestartCount}}' "$PROD")

REAL=$(ss -Htn state established '( sport = :8000 )' 2>/dev/null | wc -l)
[ "$REAL" -le "$MAX_REAL" ] || die "$REAL connections on port 8000 (limit $MAX_REAL): real traffic is present, not testing."

MEM_AVAIL_MB=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
MEM_TOTAL_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
NEED_MB=$(( 350 + DEVICES / 8 ))
[ "$MEM_AVAIL_MB" -ge "$NEED_MB" ] || die "only ${MEM_AVAIL_MB} MB memory available, need about ${NEED_MB} MB. Lower DEVICES."
DISK_FREE_MB=$(df -Pm /var/lib/docker | awk 'NR==2 {print $4}')
[ "$DISK_FREE_MB" -ge 2000 ] || die "only ${DISK_FREE_MB} MB free disk, need 2000."

PROTOCOLS="GT06,GPS103"
if ! docker run --rm --entrypoint test "$IMAGE" -f /app/services/gps103_handler.py 2>/dev/null; then
  echo "NOTE: the deployed image has no GPS103 support; testing GT06/V5 only."
  GT06_SHARE=1.0; PROTOCOLS="GT06"
fi

echo "Host: ${MEM_TOTAL_MB} MB RAM (${MEM_AVAIL_MB} available), $(nproc) vCPU, ${REAL} real device connection(s)."
echo "Image under test: $IMAGE   (production container restarts so far: $RESTARTS_BEFORE)"
echo "Plan: $DEVICES devices, scenario=$SCENARIO, ${DURATION}s hold + ${RAMP}s ramp, gt06 share $GT06_SHARE, LOG_LEVEL=$LOG_LEVEL, pool=$DB_POOL_MAX"
[ "${DRY_RUN:-0}" = "1" ] && { echo "DRY_RUN: guards passed, nothing started."; exit 0; }

trap cleanup EXIT INT TERM
cleanup
mkdir -p "$OUT"

# --- throw-away database ---------------------------------------------------
docker network create "$NET" >/dev/null
docker run -d --name lt-pg --network "$NET" "${LIMITS[@]}" --memory 400m --memory-swap 400m \
  -e POSTGRES_PASSWORD=test "$PG_IMAGE" -c shared_buffers=64MB -c max_connections=60 >/dev/null
for _ in $(seq 1 40); do docker exec lt-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec lt-pg pg_isready -U postgres >/dev/null 2>&1 || die "test Postgres did not start."
PG_IP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" lt-pg)
DSN="postgresql://postgres:test@${PG_IP}:5432/postgres"

docker run --rm --network "$NET" "${LIMITS[@]}" --memory 300m -v "$KIT:/kit:ro" \
  -e BENCH_DATABASE_URL="$DSN" --entrypoint python "$IMAGE" \
  /kit/test/bench_seed.py --devices "$DEVICES" --gt06-share "$GT06_SHARE" --create-schema

# --- test gateway (same image as production, test DB, no published port) ----
docker run -d --name lt-gateway --network "$NET" "${LIMITS[@]}" --memory 350m --memory-swap 350m \
  -e DATABASE_URL="$DSN" -e PORT=9000 -e LOG_LEVEL="$LOG_LEVEL" -e DB_POOL_MIN=5 \
  -e DB_POOL_MAX="$DB_POOL_MAX" -e MAX_CONNECTIONS=60000 -e ENABLED_PROTOCOLS="$PROTOCOLS" \
  -e PYTHONUNBUFFERED=1 "$IMAGE" >/dev/null
sleep 6
[ "$(docker inspect -f '{{.State.Running}}' lt-gateway)" = "true" ] \
  || { docker logs lt-gateway 2>&1 | tail -20; die "test gateway did not start."; }
GW_IP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" lt-gateway)
GW_ID=$(docker inspect -f '{{.Id}}' lt-gateway)
CG="/sys/fs/cgroup/system.slice/docker-${GW_ID}.scope"

cpu_usec() { awk '/^usage_usec/ {print $2}' "$CG/cpu.stat" 2>/dev/null || echo ""; }
CPU0=$(cpu_usec)
T0=$(date +%s)

# --- load ------------------------------------------------------------------
set +e
timeout $(( DURATION + RAMP + 180 )) docker run --rm --name lt-sim --network "$NET" "${LIMITS[@]}" \
  --memory 400m -v "$KIT:/kit:ro" -v "$OUT:/out" --entrypoint python "$IMAGE" \
  /kit/test/load_test_fleet.py --host "$GW_IP" --port 9000 --devices "$DEVICES" \
  --gt06-share "$GT06_SHARE" --scenario "$SCENARIO" --duration "$DURATION" --ramp-seconds "$RAMP" \
  --report-every 20 --json-out /out/result.json
SIM_RC=$?
set -e

CPU1=$(cpu_usec)
PEAK=$(cat "$CG/memory.peak" 2>/dev/null || echo "")
ROWS=$(docker exec lt-pg psql -U postgres -Atc \
  "select 'GpsTrackings '||count(*) from \"GpsTrackings\"" 2>/dev/null || true)

echo
echo "================ gateway under test (container cgroup) ================"
if [ -n "$CPU0" ] && [ -n "$CPU1" ] && [ -f "$OUT/result.json" ]; then
  python3 - "$CPU0" "$CPU1" "$T0" "$(date +%s)" "$PEAK" "$OUT/result.json" <<'PY'
import json, sys
c0, c1, t0, t1, peak, path = sys.argv[1:7]
d = json.load(open(path))
cpu_s = (int(c1) - int(c0)) / 1e6
wall = max(1, int(t1) - int(t0))
pk = max(1, d["packets_sent"])
print(f"  gateway CPU     {cpu_s:.1f} s over {wall} s  = {100*cpu_s/wall:.1f}% of one vCPU")
print(f"  CPU per packet  {1000*cpu_s/pk:.2f} ms   ({pk} packets)")
if peak:
    print(f"  memory peak     {int(peak)/1048576:.0f} MB")
PY
else
  echo "  (cgroup CPU counters unavailable; use: docker stats lt-gateway during a run)"
fi
echo "  $ROWS"
echo "  production container restarts during test: $(( $(docker inspect -f '{{.RestartCount}}' "$PROD") - RESTARTS_BEFORE ))"
echo "  real device connections now: $(ss -Htn state established '( sport = :8000 )' | wc -l)"
echo "  full JSON: $OUT/result.json   simulator exit code: $SIM_RC (0 = SLO passed)"
echo
echo "NEXT: in the EC2 console (Monitoring tab) check CPUCreditBalance and CPUSurplusCreditBalance"
echo "      before and after this run; CloudWatch updates every 5 minutes."
exit "$SIM_RC"
