# Implementation Plan: Uber Route & Coffee Recommendation Agent

**Branch**: `001-uber-route-coffee-agent` | **Date**: 2026-08-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-uber-route-coffee-agent/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

**Amended post-implementation** (research.md §20; original Summary below is the pre-build
intent and is kept for history). Riders submit origin, destination, and the time they need to
**arrive**; the system returns three comparable departure options — leave now, 30 minutes
early, or 60 minutes early — each independently priced, with a real nearby café/bakery/
restaurant suggestion (fetched live, any city) for the two earlier options. Technical
approach: a 100% Python `intersystems-pyprod` Interoperability Production
(`BsUberRouteService` → `BpRouteOrchestrator` → `BoIntegratedMlPredictor` /
`BoHybridRagEngine`) running inside InterSystems IRIS Community Edition, with the fare
prediction served by IntegratedML (via a PMML import, not `TRAIN MODEL`/AutoML — this image
has no working AutoML provider) and the waiting-place suggestion served by a hybrid
(vector + keyword) search over IRIS's native Vector Store, over candidates fetched live from
the Overpass API, per [constitution.md](../../.specify/memory/constitution.md).

*Original Summary (pre-implementation)*: Riders submit origin, destination, and a desired
time; the system returns an optimized departure time and fare estimate, and — when that
recommendation differs from the requested time by more than 30 minutes — a nearby
café/coworking suggestion to wait at.

## Technical Context

*Updated post-implementation to match what actually ships (research.md §1–§22) — see inline
notes for what changed from the original pre-build plan.*

**Language/Version**: Python 3.12 (the Embedded Python runtime bundled with the IRIS
Community Edition 2025.3 image actually used for live verification; `intersystems-pyprod`'s
CLI must run inside it via `/usr/irissys/bin/irispython`, not an external `pip install`
environment — research.md §14).

**Primary Dependencies**: `intersystems-pyprod` (interoperability hosts/production);
`sentence-transformers`, loaded with **`backend="onnx"`** (needs `onnxruntime` +
`optimum[onnxruntime]`) — *not* the library's default PyTorch backend, which reliably
segfaulted the IRIS embedded-Python worker process (research.md §22); `requests` (public API
calls — geocoding via Nominatim, live waiting-place lookup via the Overpass API — from
adapter modules); `scikit-learn` + `nyoka`, used **offline only**
(`models/train_fare_predictor.py`) to train `FarePredictor` and export it to PMML, since this
image has no working AutoML provider (research.md §16). No external ORM, vector DB client, or
ML-serving library — IRIS SQL/Embedded Python cover storage, vector search, and predictions.

**Storage**: InterSystems IRIS Community Edition — a single namespace holding relational
tables (trips, route recommendations), the JSON Document Store (raw request/response
payloads, now the full 3-option breakdown per request — research.md §20), the Vector Store
(`VECTOR(DOUBLE, 384)` embeddings for waiting places, populated from both an offline seed and
live Overpass results — research.md §22), Foreign Tables (historical traffic/weather
reference data via the CSV foreign data wrapper), and an IntegratedML model (`FarePredictor`,
imported from PMML rather than trained in-database — research.md §16).

**Testing**: `pytest` for pure Python unit/integration/contract tests (40 tests as of
research.md §22, all IRIS/pyprod calls mocked, no live IRIS required); live end-to-end
validation against a running IRIS 2025.3 Community container via real HTTP requests (research
md §14–§22) — the authoritative validation path was direct HTTP against the deployed WSGI web
app, not just the mocked test suite.

**Target Platform**: InterSystems IRIS Community Edition running in Docker (Linux). **IRIS
2026.1 Build 234U was tried first and rejected** — it silently truncates new class names at
the first underscore (research.md §12) and its embedded-Python worker jobs crashed
unpredictably even on trivial calls (research.md §13); **IRIS 2025.3 does not exhibit either
issue** and is what this project actually ships against. The production's WSGI-facing
Business Service runs as an IRIS-hosted WSGI Web Application (`/uberapp`, registered via
`deploy/UberRouteSetup.cls` — the Management Portal's own "Create Web Application" form has
the same effect but a subtler bug, `DispatchClass` must be set explicitly or the app silently
falls back to the CSP/Zen page dispatcher — research.md §15).

**Project Type**: Single project — a backend interoperability service, **plus a minimal
frontend** (`production/wsgi/static/index.html`, added post-implementation per direct user
request — a single self-contained HTML page, no build step/framework, served by the same
WSGI app). `quickstart.md` documents both the HTTP-client validation path and how to reach
the frontend in a browser.

**Performance Goals**: End-to-end request handling (WSGI request in → JSON response out)
completes within 5 seconds (spec SC-001) — live-verified. Waiting-place lookups have a real
platform constraint discovered live: IRIS's job monitor marks a worker "dead" (and restarts
the message) if a synchronous call runs too long without yielding, independent of whether the
call would have succeeded — mitigated by pre-warming the embedding model and the Overpass
HTTPS connection at host startup, caching the Overpass sync per origin for 5 minutes, and
capping new-candidate embedding to 3 per sync (research.md §22).

**Constraints**: IRIS Community Edition is single-instance / non-production-licensed (no
mirroring or sharding) — acceptable since this feature targets a single demo/dev instance
per the constitution's IRIS-only mandate. No externally paid API keys are required by
design — the embedding model runs locally, and both external API calls (Nominatim geocoding,
Overpass live-place lookup) are free and keyless, deliberately chosen over the paid Google
Places API for this reason (research.md §22).

**Scale/Scope**: Tens to low hundreds of waiting-place records in the RAG collection (seed +
live-fetched); a small (238-row) historical trip dataset used to train `FarePredictor`
offline; single-requester-at-a-time demo/evaluation load — this feature is not designed for
high concurrency or multi-region scale.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.2 (updated
against the shipped implementation, not just the pre-build design):

| Principle | Gate | Status |
|---|---|---|
| I. PyProd-First Interoperability | Four hosts (`BsUberRouteService`, `BpRouteOrchestrator`, `BoIntegratedMlPredictor`, `BoHybridRagEngine`) are `intersystems-pyprod` `BusinessService`/`BusinessProcess`/`BusinessOperation` subclasses in pure Python; the inbound interface uses WSGI (`production/wsgi/app.py`, IRIS-native WSGI Web App); `BsUberRouteService` validates input then hands off to the Business Process rather than orchestrating itself | PASS |
| II. IRIS as Single Multimodel Platform | All data (relational trip/route records, JSON request/response logs, `VECTOR(DOUBLE, 384)` waiting-place embeddings, one Foreign Table) lives in one IRIS Community namespace; no external vector DB or document store is introduced | PASS |
| III. Hybrid Retrieval & Documented Embeddings (NON-NEGOTIABLE) | `BoHybridRagEngine` combines `VECTOR_COSINE` semantic search with iFind (`%FIND search_index(...)`) keyword search using weighted (0.6/0.4) ranking; chunking strategy and embedding model choice are documented in research.md §3–4, §22 | PASS |
| IV. In-Database Predictive Models via IntegratedML | `FarePredictor` is built with `CREATE MODEL` / `TRAIN MODEL` (via PMML import, not AutoML — research.md §16) / `PREDICT()`; `BoIntegratedMlPredictor` queries it via Embedded Python/SQL — no separate ML-serving stack | PASS |
| V. Observability by Default | Every host logs structured events (request received, IntegratedML call, RAG call, rule outcome, persisted, error) to the IRIS event log via `observability/telemetry.py`'s `log_event`/`timed_event`, with in-process counters/histograms exposed via `get_metrics_snapshot()` | PASS |

No violations identified. Complexity Tracking table is omitted (not needed).

**Post-implementation re-check** (research.md §1–§22, live-verified against a running IRIS
2025.3 container, not just design review): all five gates remain PASS. One wording-only
constitution amendment (v1.0.1 → v1.0.2) was needed along the way: the Data & External
Integration Standards section originally required public-API calls to be isolated inside a
*dedicated Business Operation*; the shipped code isolates them inside a dedicated *adapter
module* instead (`geocoding_adapter.py` called from the Business Process,
`overpass_adapter.py` called from a Business Operation) — satisfying the rule's actual intent
(mockable, rate-limited, swappable) without mandating an extra host per external call.

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

*Updated to the actual, shipped structure — additions beyond the original pre-build plan are
marked.*

```text
production/
├── production.py                    # Declarative Production (Production/ServiceItem/ProcessItem/OperationItem)
├── requirements.txt                 # incl. onnxruntime/optimum (ONNX embedding backend) — [added]
├── hosts/
│   ├── bs_uber_route_service.py     # BsUberRouteService — BusinessService, WSGI-fed, validates input
│   ├── bp_route_orchestrator.py     # BpRouteOrchestrator — BusinessProcess, builds the 3 departure options
│   ├── bo_integratedml_predictor.py # BoIntegratedMlPredictor — BusinessOperation, FarePredictor via SQL
│   ├── bo_hybrid_rag_engine.py      # BoHybridRagEngine — BusinessOperation, hybrid vector+keyword search + live Overpass sync
│   └── business_rules.py            # standalone Business Rule function (research.md §8) — no longer called from the 3-option flow, kept as a swappable component
├── messages/
│   └── schemas.py                   # Request/response message shapes; RouteRecommendationMessage carries options_json (a JSON string, not nested fields — pyprod's JsonSerialize constraint)
├── adapters/
│   ├── geocoding_adapter.py         # Nominatim geocoding adapter (used by BpRouteOrchestrator)
│   └── overpass_adapter.py          # Overpass live nearby-place lookup adapter (used by BoHybridRagEngine) — [added]
├── wsgi/
│   ├── app.py                       # WSGI entrypoint: GET / (frontend) + POST /api/uber-route/recommend
│   └── static/index.html            # single-page frontend, no build step — [added, post-implementation]
└── observability/
    └── telemetry.py                 # Structured logging (log_event) + timed_event context manager, shared by all hosts

deploy/
└── UberRouteSetup.cls               # ObjectScript helper: registers the /uberapp WSGI Web Application — [added, research.md §15]

models/
├── train_fare_predictor.py          # Offline scikit-learn training script; exports FarePredictor to PMML — [added, research.md §16]
└── fare_predictor.pmml              # The exported model, imported into IRIS by sql/004_integratedml.sql — [added]

sql/
├── 001_core_tables.sql              # TripRequest, RouteRecommendation, TripHistory relational tables + RequestLog JSON document table
├── 002_vector_index.sql             # WaitingPlace table (incl. Embedding VECTOR(DOUBLE, 384) + conditional HNSW index) + WaitingPlaceSuggestion table (unused — see data-model.md)
├── 003_foreign_tables.sql           # CREATE FOREIGN SERVER/TABLE for historical traffic/weather (CSV)
├── 003b_foreign_tables_fallback.sql # Native-table fallback for TrafficWeatherReference if 003's Foreign Table isn't supported
└── 004_integratedml.sql             # CREATE MODEL (explicit feature clause) + SET ML CONFIGURATION %PMML + TRAIN MODEL (PMML import, not AutoML)

ingestion/
└── load_waiting_places.py           # Chunking + local (ONNX-backend) embedding pipeline for café/coworking seed data

tests/
├── contract/                        # WSGI request/response payload contract tests
├── integration/                     # End-to-end production tests (BS → BP → BO → IRIS, mocked)
└── unit/                            # Pure Python unit tests (business rule, validation, arrival-time math, adapters mocked)

ARTIGO.md                            # Requirements-coverage write-up (PyProd + RAG topics and their bonus items) — [added]
assets/                              # Project logo (icon + light/dark lockups) — [added]
LICENSE                              # MIT — [added]
```

**Structure Decision**: Single project (Option 1), specialized for a PyProd interoperability
production rather than a generic app. `production/` holds all Python interoperability code
required by Constitution Principle I; `sql/` holds the versioned DDL/IntegratedML scripts
required by the Development Workflow section of the constitution; `ingestion/`+`models/` are
the offline pipelines that populate the Vector Store (Principle III) and `FarePredictor`
(Principle IV, via PMML since AutoML isn't available); `deploy/` holds the one-time
ObjectScript setup needed because the WSGI Web Application registration has a Python-bridge
limitation (research.md §15) that can't be done from Embedded Python alone; `tests/` mirrors
the service/process/operation boundary so each host can be tested independently.

## Complexity Tracking

*No constitution violations identified — this section is intentionally empty.*
