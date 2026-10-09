-- Minimal schema for the gateway smoke test. Only the columns the
-- gateway code actually reads/writes — not the full production schema.

CREATE TABLE "SetupShalotrackDevices" (
    "ImeiNumber"     TEXT PRIMARY KEY,
    "DeviceCategory" TEXT,
    "SimNumber"      TEXT,
    "Status"         TEXT
);

CREATE TABLE "GpsDevices" (
    "DeviceId"         UUID PRIMARY KEY,
    "ImeiNumber"       TEXT UNIQUE NOT NULL,
    "SimNumber"        TEXT,
    "DeviceModel"      TEXT,
    "ProtocolType"     TEXT,
    "ActivationStatus" INTEGER,
    "CreatedAt"        TIMESTAMPTZ,
    "UpdatedAt"        TIMESTAMPTZ
);

CREATE TABLE "DeviceAssignments" (
    "DeviceId"  UUID,
    "VehicleId" UUID,
    "Status"    INTEGER
);

CREATE TABLE "DeviceStatuses" (
    "DeviceId"         UUID PRIMARY KEY,
    "IsOnline"         BOOLEAN,
    "LastHeartbeat"    TIMESTAMPTZ,
    "LastSeen"         TIMESTAMPTZ,
    "GpsSignal"        INTEGER,
    "BatteryLevel"     INTEGER,
    "IgnitionStatus"   BOOLEAN,
    "MovementStatus"   BOOLEAN,
    "PowerStatus"      INTEGER,
    "UpdatedAt"        TIMESTAMPTZ
);

CREATE TABLE "GpsTrackings" (
    "Id"           SERIAL PRIMARY KEY,
    "DeviceId"     UUID,
    "Latitude"     DOUBLE PRECISION,
    "Longitude"    DOUBLE PRECISION,
    "Altitude"     DOUBLE PRECISION,
    "Speed"        INTEGER,
    "Heading"      INTEGER,
    "Satellites"   INTEGER,
    "GpsAccuracy"  DOUBLE PRECISION,
    "EventTime"    TIMESTAMP,
    "CreatedAt"    TIMESTAMPTZ
);

CREATE TABLE "CurrentLocations" (
    "DeviceId"       UUID PRIMARY KEY,
    "VehicleId"      UUID,
    "Latitude"       DOUBLE PRECISION,
    "Longitude"      DOUBLE PRECISION,
    "Speed"          INTEGER,
    "Heading"        INTEGER,
    "IgnitionStatus" BOOLEAN,
    "MovementStatus" BOOLEAN,
    "LastUpdate"     TIMESTAMP
);

CREATE TABLE "DeviceEvents" (
    "Id"           SERIAL PRIMARY KEY,
    "DeviceId"     UUID,
    "VehicleId"    UUID,
    "EventType"    TEXT,
    "Severity"     TEXT,
    "Latitude"     DOUBLE PRECISION,
    "Longitude"    DOUBLE PRECISION,
    "RawPacketId"  UUID,
    "Description"  TEXT,
    "Metadata"     TEXT,
    "CreatedAt"    TIMESTAMPTZ
);

CREATE TABLE "RawPackets" (
    "Id"             SERIAL PRIMARY KEY,
    "DeviceId"       UUID,
    "ProtocolNumber" TEXT,
    "RawHex"         TEXT,
    "PacketLength"   INTEGER,
    "ReceivedAt"     TIMESTAMPTZ,
    "Parsed"         BOOLEAN
);

CREATE TABLE "CommandResponses" (
    "Id"           SERIAL PRIMARY KEY,
    "DeviceId"     UUID,
    "Command"      TEXT,
    "RawResponse"  TEXT,
    "ParsedData"   TEXT
);

-- The one test device, pre-activated — this is the IMEI the smoke test script uses.
INSERT INTO "SetupShalotrackDevices" ("ImeiNumber", "DeviceCategory", "SimNumber", "Status")
VALUES ('355172106043787', 'V5', '94700000000', 'Activated');