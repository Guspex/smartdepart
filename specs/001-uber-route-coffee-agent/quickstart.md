# Quickstart: Uber Route & Coffee Recommendation Agent

Validates the feature end-to-end against a live InterSystems IRIS Community Edition instance.
This is a run/validation guide — implementation code lives under `production/`, `sql/`, and
`ingestion/` (see [plan.md](./plan.md) Project Structure), created during `/speckit-tasks` +
`/speckit-implement`, not here.

## Prerequisites

- Docker, with an IRIS Community Edition container available (2025.1+ recommended for HNSW;
  2024.1+ minimum — see [research.md](./research.md) §6).
- Python 3.11 with `intersystems-pyprod`, `sentence-transformers`, and `requests` installed
  (`pip install -r production/requirements.txt`, created during implementation).
- Network access to `nominatim.openstreetmap.org` for geocoding (research.md §9) — or a local
  mock if running offline.

## 1. Confirm IRIS vector/foreign-table support on the target image

```sql
-- Confirms VECTOR + VECTOR_COSINE work (should return 1.0 on an exact match)
SELECT VECTOR_COSINE(TO_VECTOR('1.0,0.0', DOUBLE, 2), TO_VECTOR('1.0,0.0', DOUBLE, 2))
```

```objectscript
Write $System.Version.GetVersion(),!
```

If the version is 2025.1+, the setup script in step 3 creates the `AS HNSW(...)` index;
otherwise it falls back to an unindexed `VECTOR_COSINE` scan (research.md §6). Also confirm
`CREATE FOREIGN SERVER ... FOREIGN DATA WRAPPER CSV` succeeds on this image before relying on
it (research.md §10 risk note) — if it fails, load the same reference data as a native table
instead.

## 2. Apply schema and seed data

Stage the CSV foreign-table source inside the container first (verified live: `/irisapp` was
not writable by `irisowner` in the tested image, so `/tmp/uberroute_data` was used — adjust
to whatever writable path your deployment uses, matching `sql/003_foreign_tables.sql`'s
`HOST` value):

```bash
docker exec <container> mkdir -p /tmp/uberroute_data
docker cp data/traffic_weather_reference.csv <container>:/tmp/uberroute_data/
```

Then apply the DDL (via any SQL client against the instance — e.g. the Management Portal's
SQL tool, or `iris session <container>` and `##class(%SQL.Statement)`), in this order:

```text
sql/001_core_tables.sql        -- TripRequest, RouteRecommendation, TripHistory, RequestLog
sql/002_vector_index.sql       -- WaitingPlace (+ Embedding/HNSW), WaitingPlaceSuggestion
sql/003_foreign_tables.sql     -- CSVServer + TrafficWeatherReference (or 003b fallback)
```

Load `data/trip_history_seed.csv` into `UberRoute.TripHistory` (e.g. via `INSERT` statements
or any CSV loader) **before** running `sql/004_integratedml.sql` — `TRAIN MODEL` needs rows
to train against.

Expected: no SQL errors; `UberRoute.WaitingPlace`, `UberRoute.TripRequest`,
`UberRoute.RouteRecommendation`, `UberRoute.WaitingPlaceSuggestion`, `UberRoute.TripHistory`,
and `UberRoute.TrafficWeatherReference` all exist — see [data-model.md](./data-model.md).
All of the above (except the vector/keyword search calls in step 3, which need real
embeddings) were verified live against a dedicated IRIS 2026.1 Community container on
2026-08-07.

## 3. Ingest the waiting-place RAG collection

```bash
python ingestion/load_waiting_places.py --input data/waiting_places_seed.json
```

Expected: one row per seed place in `UberRoute.WaitingPlace`, each with a populated 384-dim
`Embedding` and non-null `SearchableText` (research.md §3–4). The underlying
`VECTOR_COSINE`/`ORDER BY` search and the `%FIND search_index(...)` keyword search (used by
`BO_HybridRAGEngine`, not `%CONTAINS(...)` — see research.md §5) were both verified live with
synthetic vectors/text on 2026-08-07.

## 4. Train the fare predictor

```sql
CREATE MODEL FarePredictor PREDICTING (FinalPrice) FROM UberRoute.TripHistory;
TRAIN MODEL FarePredictor;
```

Expected: training completes without error against the seeded `UberRoute.TripHistory` rows
(research.md §7). **Known issue** (verified live against IRIS 2026.1 Community, 2026-08-07):
`CREATE MODEL` succeeds, but `TRAIN MODEL` segfaulted (signal 11, "Callin Connection"
process) twice in a row on that instance — the default AutoML provider appears broken on
that particular Docker image. If this happens, check
`INFORMATION_SCHEMA.ML_TRAINING_RUNS` (should show a row once training genuinely starts) and
try a different IRIS image/version, or configure a different ML provider (e.g. PMML import)
before continuing — `BO_IntegratedMLPredictor` will otherwise return
`prediction_unavailable` errors for every request (this is by design: it does not fall back
to a non-IntegratedML formula, per constitution Principle IV).

## 5. Load and start the production

The `intersystems_pyprod` CLI must run **inside** IRIS's own embedded Python — it calls
`##class(%SYS.Python).Import(...)`-style validation that isn't available from a plain
external `pip install intersystems-pyprod` environment (confirmed live: running it from an
external Windows/Linux Python fails with `module '_iris_ep' has no attribute '_Stream'`, even
with `intersystems-irispython` installed and IRISHOST/IRISPORT/IRISUSERNAME/IRISPASSWORD
set). Copy the `production/` folder into the container and run the loader from inside it:

```bash
docker cp production/. <container>:/irisapp/production/
docker exec <container> /usr/irissys/bin/irispython /irisapp/production/production.py
```

Then, from Embedded Python (e.g. an IRIS terminal inside the container):

```python
from intersystems_pyprod import director
director.start_production("YourPkg.UberRouteProduction")
status, name, state = director.get_production_status()
assert state == "1", f"expected running, got state={state}"
```

## 6. Exercise the contract — no wait needed (User Story 1)

```bash
curl -s -X POST http://localhost:52773/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"Av. Paulista, 1000, Sao Paulo","destination":"Rua Augusta, 500, Sao Paulo","target_time":"18:00"}'
```

Expected: HTTP 200 within 5 seconds; JSON matches the "delta ≤ 30 minutes" shape in
[contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md), with
`waiting_place_suggested: false`.

## 7. Exercise the contract — waiting place suggested (User Story 2 + 3)

Pick an `origin`/`target_time` combination expected (given the seeded `TripHistory`/
`TrafficWeatherReference` data) to push the predicted optimal time more than 30 minutes from
the request — e.g., a known high-demand slot:

```bash
curl -s -X POST http://localhost:52773/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"Av. Paulista, 1000, Sao Paulo","destination":"Aeroporto de Congonhas, Sao Paulo","target_time":"18:00"}'
```

Expected: HTTP 200; `waiting_place_suggested: true`; `waiting_place` populated with `name`,
`address`, and `rationale` — matching SC-002 and SC-004.

## 8. Exercise validation and error paths

```bash
# Missing field -> 400
curl -s -X POST http://localhost:52773/api/uber-route/recommend \
  -H "Content-Type: application/json" -d '{"origin":"Av. Paulista, 1000"}'

# Unresolvable location -> 422
curl -s -X POST http://localhost:52773/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"asdkjhaskjdh not a real place","destination":"Rua Augusta, 500, Sao Paulo","target_time":"18:00"}'
```

Expected: 400 and 422 responses matching contracts/bs_uber_route_service.md — confirms FR-002
and FR-011.

## 9. Confirm observability

In the IRIS Management Portal, open the event log / message trace for the production and
confirm each of the calls above produced structured log entries for: request received,
IntegratedML prediction, RAG lookup (if triggered), Business Rule outcome, and (for step 8)
the error path — per Constitution Principle V and `observability/telemetry.py`.

## Done

All nine steps passing constitutes an end-to-end validation of User Stories 1–3, the
Functional Requirements, and Success Criteria SC-001 through SC-006.
