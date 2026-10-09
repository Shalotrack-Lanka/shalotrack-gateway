"""
Seeds N test devices directly into the sandbox DB — avoids pasting
hundreds of lines of SQL by hand. Uses the sync 'psycopg2' driver
just for this one-off seeding script (the gateway itself uses asyncpg).
"""

import argparse
import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:test@localhost:5433/postgres"


async def main(count: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = [
            (f"35517210{i:07d}", "V5", f"9470{i:07d}", "Activated")
            for i in range(1, count + 1)
        ]
        await conn.executemany(
            """
            INSERT INTO "SetupShalotrackDevices"
                ("ImeiNumber", "DeviceCategory", "SimNumber", "Status")
            VALUES ($1, $2, $3, $4)
            ON CONFLICT ("ImeiNumber") DO NOTHING
            """,
            rows,
        )
        print(f"✅ Seeded {count} test devices (IMEIs 355172100000001..{rows[-1][0]})")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000)
    args = parser.parse_args()
    asyncio.run(main(args.count))