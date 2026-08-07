-- Core relational tables + JSON document log, per data-model.md.
-- WaitingPlace and WaitingPlaceSuggestion are NOT here — see sql/002_vector_index.sql.

CREATE TABLE UberRoute.TripRequest (
    Origin          VARCHAR(256) NOT NULL,
    OriginLat       DOUBLE,
    OriginLng       DOUBLE,
    Destination     VARCHAR(256) NOT NULL,
    DestinationLat  DOUBLE,
    DestinationLng  DOUBLE,
    TargetTime      VARCHAR(5) NOT NULL,
    RequestedAt     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- TripRequestID intentionally has no FOREIGN KEY constraint: IRIS's implicit ID column
-- on a plain CREATE TABLE is not directly referenceable by a FOREIGN KEY without extra
-- setup (verified live against IRIS 2026.1 — the FK form was tried and rejected with
-- SQLCODE -316, "references non-existent field(s)"). Referential integrity is enforced
-- in application code (BP_RouteOrchestrator), not the schema, for this feature's scale.
CREATE TABLE UberRoute.RouteRecommendation (
    TripRequestID           INTEGER NOT NULL,
    RecommendedTime         VARCHAR(5),
    EstimatedFare           NUMERIC(8,2),
    DeltaMinutes             INTEGER,
    WaitingPlaceTriggered    INTEGER DEFAULT 0,
    CreatedAt                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE UberRoute.TripHistory (
    PickupTime      VARCHAR(5),
    DayOfWeek       INTEGER,
    DistanceKm      DOUBLE,
    DemandFactor    DOUBLE,
    FinalPrice      NUMERIC(8,2)
);

-- JSON Document Store usage (constitution Principle II / Data & External Integration
-- Standards): Payload holds the raw request/response JSON for a session, queryable via
-- IRIS's JSON functions (e.g. JSON_TABLE / %FromJSON) without being split into columns.
CREATE TABLE UberRoute.RequestLog (
    SessionID   VARCHAR(64),
    Payload     VARCHAR(32000),
    CreatedAt   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
