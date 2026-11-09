"""
Create the benchmark schema and N fake devices (each with its own vehicle) in a
TEST database, for test/load_test_fleet.py.

    BENCH_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5433/postgres \\
        python test/bench_seed.py --devices 5000 --create-schema

Safety (the 28 Oct incident ran a test suite against the production database):
  * the connection string comes ONLY from BENCH_DATABASE_URL, never from the
    gateway's DATABASE_URL or a .env file;
  * a host that is not loopback or a private address is refused unless you pass
    --i-am-sure-this-is-a-test-database (use it for a throw-away Supabase
    project or branch, never for the production project);
  * --create-schema refuses if GpsDevices already holds IMEIs that are not
    load-test IMEIs (prefix 35517210);
  * --reset deletes ONLY rows belonging to load-test IMEIs.

IMEIs are 35517210 + 7 digits (index 1..N). Devices 1..gt06_count are GT06/V5,
the rest GPS103/TK103; load_test_fleet.py uses the same split via --gt06-share.
"""

import argparse
import asyncio
import ipaddress
import os
import sys
import uuid
from urllib.parse import urlparse

import asyncpg

PREFIX = "35517210"
HERE = os.path.dirname(os.path.abspath(__file__))


def imei_for(index: int) -> str:
    return f"{PREFIX}{index:07d}"


def host_is_local(dsn: str) -> bool:
    host = urlparse(dsn).hostname or ""
    if host in ("localhost", ""):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False          # a DNS name (e.g. *.supabase.co) is not assumed local


async def main(args) -> int:
    dsn = os.environ.get("BENCH_DATABASE_URL")
    if not dsn:
        print("Set BENCH_DATABASE_URL (this script never reads DATABASE_URL).")
        return 2
    if not host_is_local(dsn) and not args.i_am_sure:
        print("Refusing: the database host is not local/private. If this is a throw-away TEST\n"
              "database (never production), re-run with --i-am-sure-this-is-a-test-database.")
        return 2

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        if args.create_schema:
            have = await conn.fetchval("SELECT to_regclass('public.\"GpsDevices\"')")
            if have:
                foreign = await conn.fetchval(
                    'SELECT count(*) FROM "GpsDevices" WHERE "ImeiNumber" NOT LIKE $1', PREFIX + "%")
                if foreign:
                    print(f"Refusing: GpsDevices already has {foreign} non-load-test device(s). "
                          "This does not look like a throw-away database.")
                    return 2
            with open(os.path.join(HERE, "bench_schema.sql"), encoding="utf-8") as f:
                await conn.execute(f.read())
            print("Schema ready.")

        if args.reset:
            like = PREFIX + "%"
            ids = "SELECT \"DeviceId\" FROM \"GpsDevices\" WHERE \"ImeiNumber\" LIKE $1"
            for table in ("GpsTrackings", "CurrentLocations", "DeviceStatuses", "DeviceEvents",
                          "RawPackets", "DeviceAssignments", "CommandResponses"):
                await conn.execute(f'DELETE FROM "{table}" WHERE "DeviceId" IN ({ids})', like)
            await conn.execute('DELETE FROM "GpsDevices" WHERE "ImeiNumber" LIKE $1', like)
            await conn.execute('DELETE FROM "SetupShalotrackDevices" WHERE "ImeiNumber" LIKE $1', like)
            print("Load-test rows removed.")

        n = args.devices
        if n:
            gt06_count = round(n * args.gt06_share)
            rows = []
            for i in range(1, n + 1):
                cat = "V5" if i <= gt06_count else "TK103"
                rows.append((imei_for(i), cat, f"9470{i:07d}", "Activated",
                             uuid.uuid4(), uuid.uuid4()))
            await conn.executemany(
                'INSERT INTO "SetupShalotrackDevices"("ImeiNumber","DeviceCategory","SimNumber","Status") '
                'VALUES ($1,$2,$3,$4) ON CONFLICT ("ImeiNumber") DO NOTHING',
                [(r[0], r[1], r[2], r[3]) for r in rows])
            await conn.executemany(
                'INSERT INTO "GpsDevices"("DeviceId","ImeiNumber","SimNumber","DeviceModel","ProtocolType",'
                '"ActivationStatus","CreatedAt","UpdatedAt") VALUES ($1,$2,$3,$4,$5,1,NOW(),NOW()) '
                'ON CONFLICT ("ImeiNumber") DO NOTHING',
                [(r[4], r[0], r[2], r[1], "GT06" if r[1] == "V5" else "GPS103") for r in rows])
            # Re-read ids in case rows already existed, then give each device its own vehicle.
            got = await conn.fetch(
                'SELECT "DeviceId","ImeiNumber" FROM "GpsDevices" WHERE "ImeiNumber" LIKE $1', PREFIX + "%")
            existing = {r["DeviceId"] for r in await conn.fetch(
                'SELECT "DeviceId" FROM "DeviceAssignments" WHERE "Status"=1')}
            todo = [(g["DeviceId"], uuid.uuid4()) for g in got if g["DeviceId"] not in existing]
            await conn.executemany(
                'INSERT INTO "DeviceAssignments"("DeviceId","VehicleId","Status") VALUES ($1,$2,1)', todo)
            print(f"Seeded {n} devices ({gt06_count} GT06/V5, {n - gt06_count} GPS103/TK103), "
                  f"IMEIs {imei_for(1)}..{imei_for(n)}.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--devices", type=int, default=0)
    p.add_argument("--gt06-share", type=float, default=0.8,
                   help="fraction of devices that are GT06/V5; the rest are GPS103/TK103")
    p.add_argument("--create-schema", action="store_true")
    p.add_argument("--reset", action="store_true", help="delete load-test rows first")
    p.add_argument("--i-am-sure-this-is-a-test-database", dest="i_am_sure", action="store_true")
    sys.exit(asyncio.run(main(p.parse_args())))
