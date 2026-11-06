# Making GpsTrackings idempotent (unique `DeviceId + EventTime`)

Why: a tracker that reconnects can re-send positions the server already stored, which creates duplicate
rows (one was visible in production data: the same `EventTime` twice for one device). Duplicates
also break any future at-least-once pipeline (queue, retries).

**Order matters.** The gateway flag `GPS_TRACKING_IDEMPOTENT` uses `ON CONFLICT ("DeviceId","EventTime")`,
which fails if the unique index does not exist.

1. **Deploy the gateway first** with `GPS_TRACKING_IDEMPOTENT=false` (the default). Nothing changes.
2. **Check for duplicates** (read-only):
   ```sql
   SELECT "DeviceId", "EventTime", count(*)
   FROM "GpsTrackings" GROUP BY 1, 2 HAVING count(*) > 1;
   ```
3. **Remove them** (keep the earliest row; run in a transaction, off-peak, ideally after a backup):
   ```sql
   BEGIN;
   DELETE FROM "GpsTrackings" a USING "GpsTrackings" b
   WHERE a."DeviceId" = b."DeviceId" AND a."EventTime" = b."EventTime"
     AND a."TrackingId" > b."TrackingId";
   COMMIT;
   ```
4. **Make the existing index unique** in `shalotrack-api` (`Data/ShaloTrackDbContext.cs`), the one that
   already says `IX_GpsTrackings_DeviceId_EventTime`:
   ```csharp
   modelBuilder.Entity<GpsTracking>()
       .HasIndex(g => new { g.DeviceId, g.EventTime })
       .HasDatabaseName("IX_GpsTrackings_DeviceId_EventTime")
       .IsDescending(false, true)
       .IsUnique();                      // <-- add
   ```
   Then `dotnet ef migrations add MakeGpsTrackingsDeviceEventTimeUnique` and check the generated migration
   drops and recreates `IX_GpsTrackings_DeviceId_EventTime` as UNIQUE. The build briefly blocks writes to
   the table (sub-second at ~170k rows; at millions of rows use `CREATE UNIQUE INDEX CONCURRENTLY`
   by hand and skip the EF-generated recreate).
5. **Immediately set `GPS_TRACKING_IDEMPOTENT=true`** (SSM / container env) and redeploy the gateway.
   Between steps 4 and 5, a re-sent duplicate position would raise a unique-violation and that one packet's
   statement fails (the original row is already stored, so no data is lost, but the live update for that
   packet is skipped). Keep that gap short.

Rollback: set the flag back to `false` and drop the unique constraint by reverting the migration.
