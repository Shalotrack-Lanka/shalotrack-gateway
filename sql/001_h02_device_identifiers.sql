-- H02 short wire-ID to canonical IMEI mapping.
-- Run against the same PostgreSQL database used by ShaloTrack Gateway.
-- A mapping does not activate a device: DeviceRepository still checks
-- SetupShalotrackDevices using the canonical IMEI.

CREATE TABLE IF NOT EXISTS "H02DeviceIdentifiers" (
    "TrackerId" TEXT PRIMARY KEY,
    "ImeiNumber" TEXT NOT NULL UNIQUE,
    "IsActive" BOOLEAN NOT NULL DEFAULT TRUE,
    "Notes" TEXT,
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT "CK_H02DeviceIdentifiers_TrackerId_NotBlank" CHECK (BTRIM("TrackerId") <> ''),
    CONSTRAINT "CK_H02DeviceIdentifiers_ImeiNumber_NotBlank" CHECK (BTRIM("ImeiNumber") <> '')
);

-- The seed does not overwrite an existing tracker-ID assignment.
INSERT INTO "H02DeviceIdentifiers" ("TrackerId", "ImeiNumber", "Notes")
VALUES ('4208298730', '866126037773336',
        'LK208 tracker wire identifier verified from the physical unit')
ON CONFLICT DO NOTHING;

-- Verify before reconnecting:
-- SELECT "TrackerId", "ImeiNumber", "IsActive"
-- FROM "H02DeviceIdentifiers" WHERE "TrackerId" = '4208298730';
