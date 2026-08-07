# Implementation Plan: Uber Route & Coffee Recommendation Agent

**Branch**: `001-uber-route-coffee-agent` | **Date**: 2026-08-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-uber-route-coffee-agent/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Riders submit origin, destination, and a desired time; the system returns an optimized
departure time and fare estimate, and — when that recommendation differs from the requested
time by more than 30 minutes — a nearby café/coworking suggestion to wait at. Technical
approach: a 100% Python `intersystems-pyprod` Interoperability Production (`BS_UberRouteService`
→ `BP_RouteOrchestrator` → `BO_IntegratedMLPredictor` / `BO_HybridRAGEngine`) running inside
InterSystems IRIS Community Edition, with the fare/timing prediction served by an IntegratedML
model and the waiting-place suggestion served by a hybrid (vector + keyword) search over IRIS's
native Vector Store, per [constitution.md](../../.specify/memory/constitution.md).

## Technical Context

**Language/Version**: Python 3.11 (matches the Embedded Python runtime bundled with IRIS
Community Edition 2024.1+ and `intersystems-pyprod`'s supported interpreter)

**Primary Dependencies**: `intersystems-pyprod` (interoperability hosts/production),
`sentence-transformers` (local embedding model, run via Embedded Python — see research.md),
`requests` (public geocoding API calls from a Business Operation adapter); no external ORM,
vector DB client, or ML-serving library — IRIS SQL/Embedded Python cover storage, vector
search, and predictions per the constitution

**Storage**: InterSystems IRIS Community Edition — a single namespace holding relational
tables (trips, route recommendations), the JSON Document Store (raw request/response
payloads for observability), the Vector Store (`VECTOR(DOUBLE, 384)` embeddings for waiting
places), Foreign Tables (historical traffic/weather reference data via the CSV foreign data
wrapper), and an IntegratedML model (`FarePredictor`)

**Testing**: `pytest` for pure Python unit tests (time-delta rule, payload validation,
adapter logic with IRIS calls mocked); SQL/Embedded-Python smoke scripts run against a live
IRIS Community container for DDL, IntegratedML train/predict, and vector search validation
(see quickstart.md); WSGI contract tests against `BS_UberRouteService`'s HTTP interface

**Target Platform**: InterSystems IRIS Community Edition running in Docker (Linux), version
2025.1+ recommended (HNSW indexing + `EMBEDDING()` SQL function available; core
`VECTOR`/`VECTOR_COSINE` functionality only requires 2024.1+, so the design degrades
gracefully to an unindexed `VECTOR_COSINE` scan on older 2024.1.x images given the small
dataset size). The production's WSGI-facing Business Service runs as an IRIS-hosted WSGI Web
Application (native IRIS WSGI support, 2024.2+).

**Project Type**: Single project — a backend interoperability service. No separate frontend
is in scope for this feature; `quickstart.md` provides an HTTP client script for validation.

**Performance Goals**: End-to-end request handling (WSGI request in → JSON response out)
completes within 5 seconds (spec SC-001); the hybrid vector+keyword waiting-place search
returns its top candidates in well under 1 second given the expected small (tens–hundreds of
rows) waiting-place dataset.

**Constraints**: IRIS Community Edition is single-instance / non-production-licensed (no
mirroring or sharding) — acceptable since this feature targets a single demo/dev instance
per the constitution's IRIS-only mandate. Foreign Tables have been an evolving IRIS SQL
feature across recent releases; the CSV foreign-data-wrapper path used here (see research.md)
must be verified against the actual installed IRIS version before being treated as
non-experimental. No externally paid API keys are required by design (embedding model runs
locally; geocoding and weather sources are free/keyless).

**Scale/Scope**: Tens to low hundreds of waiting-place records in the RAG collection; a
handful of historical trip records to train `FarePredictor`; single-requester-at-a-time
demo/evaluation load — this feature is not designed for high concurrency or multi-region
scale.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.1:

| Principle | Gate | Status |
|---|---|---|
| I. PyProd-First Interoperability | All four hosts (`BS_UberRouteService`, `BP_RouteOrchestrator`, `BO_IntegratedMLPredictor`, `BO_HybridRAGEngine`) are `intersystems-pyprod` `BusinessService`/`BusinessProcess`/`BusinessOperation` subclasses in pure Python; the inbound REST interface uses WSGI (IRIS-native WSGI Web App, 2024.2+); `BS_UberRouteService` validates input then hands off to the Business Process rather than orchestrating itself | PASS |
| II. IRIS as Single Multimodel Platform | All data (relational trip/route records, JSON request/response logs, `VECTOR(DOUBLE, 384)` waiting-place embeddings, Foreign Tables) lives in one IRIS Community namespace; no external vector DB or document store is introduced | PASS |
| III. Hybrid Retrieval & Documented Embeddings (NON-NEGOTIABLE) | `BO_HybridRAGEngine` combines `VECTOR_COSINE` semantic search with `%CONTAINS`/iFind keyword search using weighted ranking; chunking strategy and embedding model choice are documented in research.md before data-model/contracts are written | PASS |
| IV. In-Database Predictive Models via IntegratedML | `FarePredictor` is built with `CREATE MODEL` / `TRAIN MODEL` / `PREDICT()`; `BO_IntegratedMLPredictor` queries it via Embedded Python/SQL — no separate ML-serving stack | PASS |
| V. Observability by Default | Every host logs structured events (request received, IntegratedML call, RAG call, rule outcome, error) to the IRIS Management Portal and exposes telemetry hook points; detailed in data-model.md/contracts | PASS |

No violations identified. Complexity Tracking table is omitted (not needed).

**Post-Phase 1 re-check**: data-model.md added the `RequestLog` JSON-document entity and a
shared `observability/telemetry.py` helper (Principle V); contracts/bs_uber_route_service.md
states the observable invariants tying `waiting_place_suggested` to the Business Rule output
(Principle III/IV); quickstart.md step 9 is a dedicated observability verification step. All
five gates remain PASS after design — no new violations introduced.

## Project Structure

### Documentation (this feature)

```text
specs/001-uber-route-coffee-agent/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── bs_uber_route_service.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

Single project: a PyProd interoperability production plus its IRIS-side SQL/DDL and a small
ingestion script for the RAG collection. No frontend is in scope for this feature.

```text
production/
├── production.py                    # Declarative Production (Production/ServiceItem/ProcessItem/OperationItem)
├── hosts/
│   ├── bs_uber_route_service.py     # BS_UberRouteService — BusinessService, WSGI-fed, validates input
│   ├── bp_route_orchestrator.py     # BP_RouteOrchestrator — BusinessProcess, 30-min delta Business Rule
│   ├── bo_integratedml_predictor.py # BO_IntegratedMLPredictor — BusinessOperation, FarePredictor via SQL
│   └── bo_hybrid_rag_engine.py      # BO_HybridRAGEngine — BusinessOperation, hybrid vector+keyword search
├── messages/
│   └── schemas.py                   # Request/response message shapes passed between hosts
├── adapters/
│   └── geocoding_adapter.py         # Public geocoding API adapter (used by BP_RouteOrchestrator)
├── wsgi/
│   └── app.py                       # WSGI entrypoint exposed as an IRIS Web Application
└── observability/
    └── telemetry.py                 # Structured logging + OpenTelemetry/metrics hooks shared by all hosts

sql/
├── 001_core_tables.sql              # TripRequest, RouteRecommendation, TripHistory relational tables + RequestLog JSON document table
├── 002_vector_index.sql             # WaitingPlace table (incl. Embedding VECTOR(DOUBLE, 384) + conditional HNSW index) + WaitingPlaceSuggestion table
├── 003_foreign_tables.sql           # CREATE FOREIGN SERVER/TABLE for historical traffic/weather (CSV)
├── 003b_foreign_tables_fallback.sql # Native-table fallback for TrafficWeatherReference if 003's Foreign Table isn't supported
└── 004_integratedml.sql             # CREATE MODEL / TRAIN MODEL FarePredictor

ingestion/
└── load_waiting_places.py           # Chunking + local embedding pipeline for café/coworking source data

tests/
├── contract/                        # WSGI request/response payload contract tests
├── integration/                     # End-to-end production tests (BS → BP → BO → IRIS)
└── unit/                            # Pure Python unit tests (delta rule, validation, adapters mocked)
```

**Structure Decision**: Single project (Option 1), specialized for a PyProd interoperability
production rather than a generic app. `production/` holds all Python interoperability code
required by Constitution Principle I; `sql/` holds the versioned DDL/IntegratedML scripts
required by the Development Workflow section of the constitution; `ingestion/` is the offline
pipeline that populates the Vector Store per Principle III; `tests/` mirrors the
service/process/operation boundary so each host can be tested independently.

## Complexity Tracking

*No constitution violations identified — this section is intentionally empty.*
