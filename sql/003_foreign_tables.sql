-- External, non-IRIS-native traffic/weather reference data mapped in via a Foreign
-- Table (CSV wrapper), per constitution's Data & External Integration Standards and
-- research.md §10. HOST must point at a directory containing
-- data/traffic_weather_reference.csv that is readable by the `irisowner` OS user on
-- the IRIS server's filesystem — copy the CSV there before running this script, e.g.:
--   docker exec <container> mkdir -p /tmp/uberroute_data
--   docker cp data/traffic_weather_reference.csv <container>:/tmp/uberroute_data/
-- (verified live against IRIS 2026.1 Community: `/irisapp` is not writable by
-- `irisowner` in this image, hence `/tmp/uberroute_data` below — adjust to whatever
-- writable path your deployment uses).
--
-- If this fails on the target IRIS version (Foreign Tables have been experimental in
-- some releases — see research.md §10 risk note), run
-- sql/003b_foreign_tables_fallback.sql instead.

CREATE FOREIGN SERVER UberRoute.CSVServer
    FOREIGN DATA WRAPPER CSV
    HOST '/tmp/uberroute_data';

-- USING takes a JSON-string literal, not a parenthesized key=value list (verified live
-- against IRIS 2026.1 — the `USING (header = true)` form fails with SQLCODE -1,
-- "LITERAL ('USING') expected, ( found").
CREATE FOREIGN TABLE UberRoute.TrafficWeatherReference (
    HourOfDay         INTEGER,
    DayOfWeek         INTEGER,
    CongestionFactor  DOUBLE,
    PrecipitationMm   DOUBLE
)
    SERVER UberRoute.CSVServer
    FILE 'traffic_weather_reference.csv'
    USING '{"header":true}';
