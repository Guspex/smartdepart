# Phase 1 Data Model: Uber Route & Coffee Recommendation Agent

All entities live in a single InterSystems IRIS Community Edition namespace, spanning
relational tables, the JSON Document Store, the Vector Store, and one Foreign Table — per
Constitution Principle II. Field names below are the actual, live-verified IRIS SQL column
names (see `sql/*.sql`); class/package naming matches the implementation
(`production/hosts/*.py`).

There are no multi-step state transitions in this feature: each `TripRequest` produces exactly
one `RouteRecommendation` row (still one row, though the response itself now carries three
departure options — see that entity's notes below) and, when relevant, waiting-place
suggestions returned inline per option, all within a single synchronous request/response flow
(spec User Stories 1–3). No entity here is later revisited or transitioned through additional
states.

## TripRequest

The rider's ask, as received by `BsUberRouteService` and validated before being handed to
`BpRouteOrchestrator` (spec FR-001, FR-002).

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `Origin` | VARCHAR(256) | Raw origin text from the rider; NOT NULL |
| `OriginLat`, `OriginLng` | DOUBLE | Reserved for the resolved origin coordinates — column exists in `sql/001_core_tables.sql` but `BpRouteOrchestrator._persist` does not currently write to it (always `NULL`); resolved coordinates are used in-request (geocoding, distance/sanity checks) but only the raw `Origin` text is persisted |
| `Destination` | VARCHAR(256) | Raw destination text; NOT NULL |
| `DestinationLat`, `DestinationLng` | DOUBLE | Same caveat as `OriginLat`/`OriginLng` — reserved, currently always `NULL` |
| `TargetTime` | VARCHAR(5) | Rider's **arrival deadline** at `Destination` (`HH:MM`), e.g. an appointment time — not a departure time (see spec.md Assumptions); NOT NULL |
| `RequestedAt` | TIMESTAMP | Server receipt time; defaults to `CURRENT_TIMESTAMP` |

**Validation** (FR-002, FR-011, FR-013): `Origin`, `Destination`, `TargetTime` must all be
present and well-formed before a `TripRequest` row is written; if geocoding cannot resolve
`Origin` or `Destination` to coordinates, or resolves them to implausibly distant coordinates
(research.md §21), the request is rejected with an explanatory error and no
`RouteRecommendation` is produced.

## RouteRecommendation

The system's answer to a `TripRequest` (spec Key Entity "Route Recommendation"; FR-003–FR-006).
**Amended (research.md §20)**: the response is now three fixed departure options
("ideal"/"30min_earlier"/"60min_earlier"), not one auto-picked time. This table predates that
redesign and wasn't given a schema migration for it — it still stores just the "ideal"
option's time/fare for simple SQL querying, while `RequestLog.Payload` (below) carries the
full 3-option breakdown as JSON.

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `TripRequestID` | Integer (FK → TripRequest) | NOT NULL — see caveat below: currently always `0` |
| `RecommendedTime` | VARCHAR(5) | The "ideal" option's departure time (`HH:MM`) — the naive departure, arrival deadline minus estimated travel time (research.md §20) |
| `EstimatedArrivalTime` | — | Estimated arrival at `Destination` if leaving at `RecommendedTime` — not a persisted column; recomputed per-option on every request and returned in the response (`arrival_time`), included here for documentation |
| `EstimatedFare` | NUMERIC(8,2) | `FarePredictor` prediction for the "ideal" option's `RecommendedTime` |
| `DeltaMinutes` | Integer | Unused since the §20 redesign (always `0`) — kept as a column for backward compatibility, not recomputed |
| `WaitingPlaceTriggered` | INTEGER (0/1) | `1` iff any of the three options has a waiting-place suggestion (the "30min_earlier"/"60min_earlier" options always attempt one) |
| `CreatedAt` | TIMESTAMP | Defaults to `CURRENT_TIMESTAMP` |

**Known gap**: `TripRequestID` is written as `0` on every insert — `SELECT LAST_IDENTITY()`
reliably returns an empty string in this environment rather than the row just inserted
(research.md §16), so the FK link between `TripRequest` and `RouteRecommendation` is not
currently populated correctly. `RequestLog.Payload` (below) still carries the full request +
response together in one JSON document per session, which is what this project actually uses
for traceability — the relational FK link was not needed to satisfy FR-012 in practice.

## WaitingPlace

A candidate location the rider could wait at (spec Key Entity "Waiting Place"); the RAG source
collection. Populated from **two** sources (research.md §3–4, §22):

1. **Offline seed**: `ingestion/load_waiting_places.py`, run once against
   `data/waiting_places_seed.json` (São Paulo + Florianópolis/São José entries).
2. **Live, on every request**: `BoHybridRagEngine._sync_live_candidates` fetches real nearby
   places from the Overpass API (`production/adapters/overpass_adapter.py`) around the
   rider's origin and inserts any not already indexed (capped at 3 new candidates per sync,
   cached 5 minutes per origin) — so any city has real candidates, not just wherever the seed
   dataset covered.

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `Name` | VARCHAR(200) | NOT NULL |
| `Address` | VARCHAR(300) | NOT NULL — preserved verbatim in every chunk (research.md §4) |
| `Category` | VARCHAR(64) | e.g., `cafe`, `coworking` |
| `Lat`, `Lng` | DOUBLE | Used for the ~1 km origin-proximity filter (FR-009) |
| `Rating` | DECIMAL(2,1) | Nullable; descriptive attribute surfaced in suggestions (FR-007) |
| `Description` | %String(MAXLEN=4000) or STREAM | Free-text ambiance/review content — chunked and embedded |
| `SearchableText` | %String(MAXLEN=4000), iFind-indexed | `Name + Address + Category + Description`, for `%CONTAINS` keyword matching |
| `Embedding` | VECTOR(DOUBLE, 384) | `sentence-transformers/all-MiniLM-L6-v2` embedding of the chunked description (research.md §3) |

**Indexes**: `AS HNSW(Distance='Cosine')` on `Embedding` when the IRIS instance is 2025.1+
(research.md §6); a `%iFind.Index.Basic` on `SearchableText`.

## WaitingPlaceSuggestion

Links a `RouteRecommendation` to the `WaitingPlace`(s) chosen for it (spec Key Entity
"Waiting-Place Suggestion"; FR-005, FR-007, FR-008, User Story 3).

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `RouteRecommendationID` | Integer (FK → RouteRecommendation) | NOT NULL |
| `WaitingPlaceID` | Integer (FK → WaitingPlace) | NOT NULL |
| `VectorScore` | DOUBLE | Raw `VECTOR_COSINE` similarity |
| `KeywordScore` | DOUBLE | Normalized `%CONTAINS`/iFind relevance |
| `FinalScore` | DOUBLE | Weighted combination (research.md §5) |
| `Rank` | Integer | 1 = best match |
| `Rationale` | VARCHAR(300) | Short human-readable reason (User Story 3, e.g., "closest match, 0.4 km, highly rated") |

**Known gap**: this table is defined in `sql/002_vector_index.sql` but no Python code writes
to it — `BoHybridRagEngine`'s chosen suggestion (with its score breakdown and rationale) is
returned directly in the response and persisted as part of `RequestLog.Payload`'s per-option
JSON instead (research.md §20), which turned out to satisfy FR-005/FR-007/FR-008/User Story 3
without needing the extra relational join. Left in the schema for a future enhancement (e.g.,
analytics over which places get suggested most often) rather than removed.

## TripHistory

Historical training data feeding `FarePredictor` (research.md §7, §16; constitution Principle
IV). Not exposed to riders; loaded once from `data/trip_history_seed.csv`.

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `PickupTime` | VARCHAR(5) | Historical trip's departure time (`HH:MM`) |
| `DayOfWeek` | Integer | 1–7 |
| `DistanceKm` | DOUBLE | Trip distance |
| `DemandFactor` | DOUBLE | Historical surge/demand multiplier |
| `FinalPrice` | NUMERIC(8,2) | Label column |

**Amended (research.md §16)**: `FarePredictor` is not trained by `TRAIN MODEL` against this
table directly — the Community Edition image tested has no working AutoML provider. Instead,
`models/train_fare_predictor.py` trains a plain scikit-learn `LinearRegression` outside IRIS
on this same seed data (converting `PickupTime` to minutes-since-midnight, since PMML can't
express an "HH:MM" string-parsing transform), exports it to PMML, and
`sql/004_integratedml.sql` imports it via `%ML.PMML.Provider` — `CREATE MODEL FarePredictor
PREDICTING (FinalPrice) WITH (PickupMinutes INTEGER, DayOfWeek INTEGER, DistanceKm DOUBLE,
DemandFactor DOUBLE)` (an explicit feature clause, not `FROM TripHistory`), then `TRAIN MODEL
... USING {"file_name": "..."}` after `SET ML CONFIGURATION %PMML`. `PREDICT(FarePredictor)`
(no `USING` clause — not valid IntegratedML syntax) matches feature columns by name against
the caller's `FROM` row context. `BoIntegratedMlPredictor` converts `candidate_time` to
`PickupMinutes` before calling `PREDICT()`, to match this feature clause.

## TrafficWeatherReference (Foreign Table)

External, non-IRIS-native reference data mapped in via a CSV foreign data wrapper (research.md
§10; constitution's Data & External Integration Standards).

| Field | Type | Notes |
|---|---|---|
| `HourOfDay` | Integer | 0–23 |
| `DayOfWeek` | Integer | 1–7 |
| `CongestionFactor` | DOUBLE | Typical traffic congestion multiplier for that slot |
| `PrecipitationMm` | DOUBLE | Typical precipitation for that slot |

Queried by `BpRouteOrchestrator._congestion_factor()` when estimating travel time for each
candidate departure option, to demonstrate the
relational + Foreign Table + Vector Store combination running in one query surface
(constitution Principle II).

## RequestLog (JSON Document Store)

Raw request/response payloads and key decision points, stored as JSON documents (not just
relational rows) — the multimodel documentation requirement from the constitution's Data &
External Integration Standards, and the audit trail behind FR-012 / Constitution Principle V.

| Field | Type | Notes |
|---|---|---|
| `SessionID` | VARCHAR(64) | Correlates to the interoperability message trace |
| `Payload` | JSON (`%DynamicObject`/`%Library.DynamicObject` document column) | `{request, response: {options: [...]}}` — `options` is the full 3-option breakdown (research.md §20), each with its own waiting-place detail |
| `CreatedAt` | TIMESTAMP | Defaults to `CURRENT_TIMESTAMP` |

## Entity Relationships

```text
TripRequest (1) ──── (1) RouteRecommendation (1) ──── (0..N) WaitingPlaceSuggestion (N) ──── (1) WaitingPlace
TripHistory                                    (used offline to TRAIN MODEL FarePredictor)
TrafficWeatherReference                        (joined read-only during candidate-time scan)
RequestLog                                     (one per TripRequest, written by every host for observability)
```
