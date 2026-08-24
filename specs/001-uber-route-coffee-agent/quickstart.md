# Quickstart: Uber Route & Coffee Recommendation Agent

Validates the feature end-to-end against a live InterSystems IRIS Community Edition instance.
**This version reflects what actually works, live-verified against IRIS 2025.3 Community
Edition** (research.md §14–§22) — it supersedes the original pre-implementation plan, which
assumed `TRAIN MODEL`/AutoML and a seed-only RAG collection, neither of which panned out as
originally designed. See research.md for the full discovery process and every bug found along
the way; this file is just the reproducible happy path.

## Prerequisites

- Docker, with an IRIS **Community Edition 2025.3** container. **Avoid IRIS 2026.1 Build
  234U** — it truncates new class names at the first underscore (research.md §12) and its
  embedded-Python worker jobs crash unpredictably (research.md §13). IRIS 2025.1 also failed
  to start in this session ("Invalid Community Edition license, may have exceeded core
  limit") — 2025.3 is the version this project actually ships against.
- `pip install -r production/requirements.txt` — note this includes `onnxruntime` and
  `optimum[onnxruntime]`, required because `sentence-transformers`'s **default PyTorch
  backend segfaults the IRIS embedded-Python worker process** (research.md §22); the code
  loads it with `backend="onnx"` instead.
- Network access to `nominatim.openstreetmap.org` (geocoding) and `overpass-api.de` (live
  waiting-place lookup) — both free, keyless.

## 1. Deploy the code into the container

```bash
docker cp production/. <container>:/tmp/uberroute_app/production/
docker cp data/. <container>:/tmp/uberroute_app/data/
docker cp ingestion/. <container>:/tmp/uberroute_app/ingestion/
docker cp models/. <container>:/tmp/uberroute_app/models/
```

## 2. Apply schema and seed data

Apply the DDL, in order, via any SQL client against the instance (e.g. the Management
Portal's SQL tool):

```text
sql/001_core_tables.sql        -- TripRequest, RouteRecommendation, TripHistory, RequestLog
sql/002_vector_index.sql       -- WaitingPlace (+ Embedding/HNSW), WaitingPlaceSuggestion (unused, see data-model.md)
sql/003_foreign_tables.sql     -- CSVServer + TrafficWeatherReference (needs the CSV staged first, see below; or 003b fallback)
```

Stage the CSV foreign-table source inside the container before running `003_foreign_tables.sql`
(`/irisapp` was not writable by `irisowner` in the tested image — adjust to whatever writable
path your deployment uses, matching `sql/003_foreign_tables.sql`'s `HOST` value):

```bash
docker exec <container> mkdir -p /tmp/uberroute_data
docker cp data/traffic_weather_reference.csv <container>:/tmp/uberroute_data/
```

Load `data/trip_history_seed.csv` into `UberRoute.TripHistory` (any CSV loader or `INSERT`
statements) before step 4.

## 3. Ingest the waiting-place RAG seed collection

Run **inside** IRIS's embedded Python (`irispython` is not on `$PATH` in the tested image —
use its full path):

```bash
docker exec <container> /usr/irissys/bin/irispython -c "
import sys; sys.path.insert(0, '/tmp/uberroute_app')
sys.argv = ['load_waiting_places.py', '--input', '/tmp/uberroute_app/data/waiting_places_seed.json']
exec(open('/tmp/uberroute_app/ingestion/load_waiting_places.py').read())
"
```

Expected: one row per seed place in `UberRoute.WaitingPlace`, each with a populated 384-dim
`Embedding` and non-null `SearchableText`. This is the **offline** half of the RAG
collection — the seed dataset — not the whole story: `BoHybridRagEngine` additionally fetches
real nearby places live from the Overpass API on every request (research.md §22), so the
seed dataset only matters as a fallback baseline / for offline demos.

## 4. Train the fare predictor (via PMML import, not AutoML)

**`TRAIN MODEL` against AutoML does not work on this image** — no AutoML provider is
installed (`SQLCODE -186`; segfaulted outright on IRIS 2026.1). Train outside IRIS instead:

```bash
python models/train_fare_predictor.py   # writes models/fare_predictor.pmml
docker cp models/fare_predictor.pmml <container>:/tmp/uberroute_app/models/fare_predictor.pmml
```

Then, in IRIS SQL (model-name must be **unqualified** — `CREATE MODEL UberRoute.FarePredictor`
is a parser error):

```sql
CREATE MODEL FarePredictor PREDICTING (FinalPrice)
    WITH (PickupMinutes INTEGER, DayOfWeek INTEGER, DistanceKm DOUBLE, DemandFactor DOUBLE);

CREATE VIEW UberRoute.TripHistoryForTraining AS
    SELECT (CAST(SUBSTRING(PickupTime,1,2) AS INTEGER)*60
            + CAST(SUBSTRING(PickupTime,4,2) AS INTEGER)) AS PickupMinutes,
           DayOfWeek, DistanceKm, DemandFactor, FinalPrice
    FROM UberRoute.TripHistory;

SET ML CONFIGURATION %PMML;

TRAIN MODEL FarePredictor
    FROM UberRoute.TripHistoryForTraining
    USING {"file_name": "/tmp/uberroute_app/models/fare_predictor.pmml"};
```

Expected: `TRAIN MODEL` succeeds immediately (no actual training happens — PMML import just
registers the pre-trained model). Verify:

```sql
SELECT PREDICT(FarePredictor) AS PredictedPrice
FROM (SELECT 1080 AS PickupMinutes, 3 AS DayOfWeek, 10.5 AS DistanceKm, 1.0 AS DemandFactor)
```

should return a plausible fare (no `USING` clause on `PREDICT` — not valid syntax; it matches
feature columns by name against the row context instead).

## 5. Load and start the production

```bash
docker exec <container> /usr/irissys/bin/irispython /home/irisowner/.local/bin/intersystems_pyprod \
  -s /tmp/uberroute_app /tmp/uberroute_app/production/production.py
```

**If you change a message schema** (a `Column` field added/removed in
`production/messages/schemas.py`), also re-run the CLI against `schemas.py` and every file in
`production/hosts/*.py` directly — a production reload alone does not regenerate the
IRIS-side ObjectScript class for a changed message shape (research.md §20).

Start the production, and confirm it started cleanly (no `ErrProductionNotShutdownCleanly` —
if you see that, call `##class(Ens.Director).RecoverProduction()` once before retrying;
research.md §19):

```python
# inside irispython, USER namespace
import iris
sc = iris.cls('Ens.Director').StartProduction('UberRoute.UberRouteProduction')
```

**If a live request hangs and `Ens_Util.Log` shows a `DeadJobAlert` for `BoHybridRagEngine`**,
check `/usr/irissys/mgr/messages.log` for `caught signal 11` before assuming it's a timing
problem — that log (not `Ens_Util.Log`) is where the real crash shows up (research.md §22).

## 6. Register the frontend as a real HTTP-reachable Web Application

```bash
docker cp deploy/UberRouteSetup.cls <container>:/tmp/UberRouteSetup.cls
```

```python
# inside irispython, %SYS namespace
import iris
sc = iris.cls('%SYSTEM.OBJ').Load('/tmp/UberRouteSetup.cls', 'ck')
r = iris.cls('UberRoute.Setup').CreateWebApp()
```

This registers `/uberapp` with `DispatchClass = "%SYS.Python.WSGI"` explicitly (the
Management Portal's own "Create Web Application" wizard sets this as a side effect of
selecting its "WSGI [Experimental]" radio button; setting every *other* WSGI-looking
property by hand without it silently falls back to the CSP/Zen dispatcher and 404s on every
path — research.md §15) and `AutheEnabled = 32` (Password only — Unauthenticated alone 403s
for WSGI apps on this build, and mixing it with Password suppresses the browser's login
challenge — research.md §15).

## 7. Exercise the contract — the "ideal" option (User Story 1)

```bash
curl -u SuperUser:<password> -X POST http://localhost:<mapped-52773-port>/uberapp/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"Av. Paulista, 1000, Sao Paulo","destination":"Rua Augusta, 500, Sao Paulo","target_time":"18:00"}'
```

Expected: HTTP 200 within 5 seconds; JSON matches
[contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md)'s `options` array
shape — three entries (`ideal`, `30min_earlier`, `60min_earlier`), each with its own
`departure_time`/`arrival_time`/`estimated_fare`; the `ideal` entry has `waiting_place: null`.

## 8. Exercise the contract — waiting-place suggestions (User Story 2 + 3)

Same request as step 7 — the response's `30min_earlier` and `60min_earlier` entries should
each carry either a real `waiting_place` (name, address, category, rating, distance, a
rationale explaining why it was chosen over other candidates) or a
`waiting_place_unavailable_reason`, never neither. Confirms SC-002/SC-004/User Story 3.

## 9. Exercise validation and error paths

```bash
# Missing field -> 400
curl -u SuperUser:<password> -X POST http://localhost:<port>/uberapp/api/uber-route/recommend \
  -H "Content-Type: application/json" -d '{"origin":"Av. Paulista, 1000"}'

# Unresolvable location -> 422 location_not_found
curl -u SuperUser:<password> -X POST http://localhost:<port>/uberapp/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"asdkjhaskjdh not a real place","destination":"Rua Augusta, 500, Sao Paulo","target_time":"18:00"}'

# Origin/destination geocode to implausibly distant real places -> 422 distance_out_of_range
curl -u SuperUser:<password> -X POST http://localhost:<port>/uberapp/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"Centro, Florianopolis","destination":"SENAI, Sao Jose","target_time":"18:00"}'
```

Expected: 400, 422, and 422 responses matching contracts/bs_uber_route_service.md — confirms
FR-002, FR-011, and FR-013.

## 10. Confirm observability

```sql
SELECT TOP 20 TimeLogged, Type, ConfigName, Text FROM Ens_Util.Log ORDER BY %ID DESC
```

Confirms structured log entries for: request received, geocode call, IntegratedML call (×3,
one per option), RAG call (×2, for the two earlier options), rule outcome, persisted, and (for
step 9) the error path — per Constitution Principle V and `observability/telemetry.py`. This
table was also the primary diagnostic tool used throughout this project's live debugging
(research.md §14–§22) — worth knowing as a troubleshooting step, not just a checkbox.

## 11. Open the frontend in a browser

```
http://localhost:<mapped-52773-port>/uberapp/
```

Prompts for HTTP Basic Auth (any valid IRIS account, e.g. `SuperUser`) — the form renders
three option cards after submitting a trip request.

## Done

All eleven steps passing constitutes an end-to-end validation of User Stories 1–3, the
Functional Requirements (including FR-013), and Success Criteria SC-001 through SC-007.
