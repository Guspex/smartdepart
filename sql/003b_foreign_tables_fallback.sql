-- Native-table fallback for TrafficWeatherReference (tasks.md T014; research.md §10).
-- Only run this if sql/003_foreign_tables.sql's CREATE FOREIGN SERVER/TABLE fails or is
-- unsupported on the target IRIS version. NOT needed on IRIS 2026.1 (verified live —
-- sql/003_foreign_tables.sql applied successfully there), kept here for portability to
-- older/other IRIS deployments per the constitution's Foreign Table risk note.
--
-- Load data/traffic_weather_reference.csv into this table with any CSV loader
-- (e.g. LOAD DATA, or a short Python/Embedded-Python script) after running this DDL.

CREATE TABLE UberRoute.TrafficWeatherReference (
    HourOfDay         INTEGER,
    DayOfWeek         INTEGER,
    CongestionFactor  DOUBLE,
    PrecipitationMm   DOUBLE
);
