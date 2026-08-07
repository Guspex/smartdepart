# Phase 1 Data Model: Uber Route & Coffee Recommendation Agent

All entities live in a single InterSystems IRIS Community Edition namespace, spanning
relational tables, the JSON Document Store, the Vector Store, and one Foreign Table — per
Constitution Principle II. Field names below are the intended IRIS SQL column names; exact
class/package naming happens in `tasks.md`/implementation.

There are no multi-step state transitions in this feature: each `TripRequest` produces exactly
one `RouteRecommendation` and, when triggered, one ranked set of `WaitingPlaceSuggestion` rows,
all within a single synchronous request/response flow (spec User Stories 1–3). No entity here
is later revisited or transitioned through additional states.

## TripRequest

The rider's ask, as received by `BS_UberRouteService` and validated before being handed to
`BP_RouteOrchestrator` (spec FR-001, FR-002).

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `Origin` | VARCHAR(256) | Raw origin text from the rider; NOT NULL |
| `OriginLat`, `OriginLng` | DOUBLE | Resolved via the geocoding adapter (research.md §9); NULL until resolved |
| `Destination` | VARCHAR(256) | Raw destination text; NOT NULL |
| `DestinationLat`, `DestinationLng` | DOUBLE | Resolved via geocoding; NULL until resolved |
| `TargetTime` | TIME | Rider's **arrival deadline** at `Destination` (`HH:MM`), e.g. an appointment time — not a departure time (see spec.md Assumptions); NOT NULL |
| `RequestedAt` | TIMESTAMP | Server receipt time; defaults to `CURRENT_TIMESTAMP` |

**Validation** (FR-002, FR-011): `Origin`, `Destination`, `TargetTime` must all be present and
well-formed before a `TripRequest` row is written; if geocoding cannot resolve `Origin` or
`Destination` to coordinates, the request is rejected with an explanatory error and no
`RouteRecommendation` is produced (edge case: ambiguous/unrecognized location).

## RouteRecommendation

The system's answer to a `TripRequest` (spec Key Entity "Route Recommendation"; FR-003–FR-006).

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `TripRequestID` | Integer (FK → TripRequest) | NOT NULL |
| `RecommendedTime` | TIME | Departure time chosen from the IntegratedML candidate scan (research.md §7); always a departure, never the arrival deadline itself |
| `EstimatedArrivalTime` | TIME | Estimated arrival at `Destination` if leaving at `RecommendedTime`, from distance + `TrafficWeatherReference` congestion — not persisted as its own column (recomputed on read), included here for documentation |
| `EstimatedFare` | DECIMAL(8,2) | `FarePredictor` prediction for `RecommendedTime` |
| `DeltaMinutes` | Integer | `ABS(RecommendedTime - NaiveDepartureTime)`, where `NaiveDepartureTime = TripRequest.TargetTime - EstimatedTravelMinutes` (typical-traffic baseline) — see spec.md Assumptions |
| `WaitingPlaceTriggered` | BIT | `1` iff `DeltaMinutes > 30` — output of the Business Rule (research.md §8) |
| `CreatedAt` | TIMESTAMP | Defaults to `CURRENT_TIMESTAMP` |

**Validation** (FR-004–FR-006): `DeltaMinutes` and `WaitingPlaceTriggered` are always
consistent with each other and MUST be recomputed by the Business Rule, never set directly by
client input.

## WaitingPlace

A candidate location the rider could wait at (spec Key Entity "Waiting Place"); the RAG source
collection populated offline by `ingestion/load_waiting_places.py` (research.md §3–4).

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

**Validation**: A `WaitingPlaceSuggestion` set exists for a `RouteRecommendation` if and only
if `RouteRecommendation.WaitingPlaceTriggered = 1` (FR-005/FR-006 — no false positives/negatives,
SC-002/SC-003). When no place satisfies the proximity/relevance threshold, zero rows are
produced and the response indicates unavailability rather than omitting the field (FR-010).

## TripHistory

Historical training data feeding `FarePredictor` (research.md §7; constitution Principle IV).
Not exposed to riders; owned entirely by `BO_IntegratedMLPredictor`'s setup step.

| Field | Type | Notes |
|---|---|---|
| `ID` | Identity | Primary key |
| `PickupTime` | TIME | Historical trip's departure time |
| `DayOfWeek` | Integer | 1–7 |
| `DistanceKm` | DOUBLE | Trip distance |
| `DemandFactor` | DOUBLE | Historical surge/demand multiplier |
| `FinalPrice` | DECIMAL(8,2) | Label column — `CREATE MODEL FarePredictor PREDICT (FinalPrice) FROM TripHistory` |

## TrafficWeatherReference (Foreign Table)

External, non-IRIS-native reference data mapped in via a CSV foreign data wrapper (research.md
§10; constitution's Data & External Integration Standards).

| Field | Type | Notes |
|---|---|---|
| `HourOfDay` | Integer | 0–23 |
| `DayOfWeek` | Integer | 1–7 |
| `CongestionFactor` | DOUBLE | Typical traffic congestion multiplier for that slot |
| `PrecipitationMm` | DOUBLE | Typical precipitation for that slot |

Queried (joined against candidate times) by `BO_IntegratedMLPredictor` or
`BP_RouteOrchestrator` when scanning candidate departure times, to demonstrate the
relational + Foreign Table + Vector Store combination running in one query surface
(constitution Principle II).

## RequestLog (JSON Document Store)

Raw request/response payloads and key decision points, stored as JSON documents (not just
relational rows) — the multimodel documentation requirement from the constitution's Data &
External Integration Standards, and the audit trail behind FR-012 / Constitution Principle V.

| Field | Type | Notes |
|---|---|---|
| `SessionID` | VARCHAR(64) | Correlates to the interoperability message trace |
| `Payload` | JSON (`%DynamicObject`/`%Library.DynamicObject` document column) | `{request, response, delta_minutes, waiting_place_triggered, host_timings}` |
| `CreatedAt` | TIMESTAMP | Defaults to `CURRENT_TIMESTAMP` |

## Entity Relationships

```text
TripRequest (1) ──── (1) RouteRecommendation (1) ──── (0..N) WaitingPlaceSuggestion (N) ──── (1) WaitingPlace
TripHistory                                    (used offline to TRAIN MODEL FarePredictor)
TrafficWeatherReference                        (joined read-only during candidate-time scan)
RequestLog                                     (one per TripRequest, written by every host for observability)
```
