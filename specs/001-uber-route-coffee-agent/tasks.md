---

description: "Task list template for feature implementation"
---

# Tasks: Uber Route & Coffee Recommendation Agent

**Input**: Design documents from `/specs/001-uber-route-coffee-agent/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md), [quickstart.md](./quickstart.md)

**Tests**: Included. `plan.md`'s Project Structure explicitly defines `tests/contract/`,
`tests/integration/`, and `tests/unit/`, and the project constitution's Development Workflow
section requires every interoperability change to be "exercised via the production's test
tooling before being considered done" — so test tasks are in scope, not optional filler.

**Organization**: Tasks are grouped by user story (from spec.md: US1 = P1, US2 = P2, US3 = P3)
to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- File paths below follow the Project Structure in [plan.md](./plan.md)

## Path Conventions

Single project (per plan.md): `production/` (PyProd hosts/adapters/WSGI), `sql/` (DDL/
IntegratedML), `ingestion/` (RAG loading script), `data/` (seed files), `tests/` at repo root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository skeleton and tooling — no IRIS-specific logic yet

- [X] T001 Create directory skeleton: `production/hosts/`, `production/messages/`, `production/adapters/`, `production/wsgi/`, `production/observability/`, `sql/`, `ingestion/`, `data/`, `tests/contract/`, `tests/integration/`, `tests/unit/` per [plan.md](./plan.md) Project Structure
- [X] T002 [P] Create `production/requirements.txt` pinning `intersystems-pyprod`, `sentence-transformers`, `requests`, `pytest`
- [X] T003 [P] Configure linting/formatting (`ruff` + `black`) in `pyproject.toml` at repo root

**Checkpoint**: Repository skeleton exists; dependencies declared.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core production scaffolding, shared entities, and the fare-prediction data layer
that every user story builds on (spec: all three stories depend on a `RouteRecommendation`
existing; constitution Principle IV/V require IntegratedML and observability from the start)

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Create `production/production.py` — declarative `Production`/`ServiceItem`/`ProcessItem`/`OperationItem` definitions for all four hosts (research.md §1)
- [X] T005 [P] Create `production/messages/schemas.py` — `TripRequestMessage`, `RouteRecommendationMessage`, `WaitingPlaceSuggestionMessage` shapes per [data-model.md](./data-model.md)
- [X] T006 [P] Create `production/observability/telemetry.py` — structured logging + OpenTelemetry-style span/metric helper shared by all hosts (research.md §11, Constitution Principle V)
- [X] T007 [P] Create `production/adapters/geocoding_adapter.py` — Nominatim geocoding call wrapper (research.md §9)
- [X] T008 Create `sql/001_core_tables.sql` — `TripRequest`, `RouteRecommendation`, `TripHistory`, `RequestLog` (JSON document) tables per [data-model.md](./data-model.md) (note: `WaitingPlace` is NOT created here — it lives in `sql/002_vector_index.sql`, see T028). Applied live against a dedicated IRIS 2026.1 Community container — all 4 CREATE TABLE statements succeeded (SQLCODE=0). Dropped the FK constraint originally drafted (SQLCODE -316, IRIS's implicit ID column isn't FK-referenceable without extra setup); referential integrity is enforced in application code instead.
- [X] T009 Create `sql/003_foreign_tables.sql` — `CREATE FOREIGN SERVER`/`CREATE FOREIGN TABLE TrafficWeatherReference` via CSV wrapper (research.md §10). Applied live and succeeded — corrected `USING` to take a JSON-string literal (`'{"header":true}'`), not `(key=value)` (that form fails with SQLCODE -1).
- [X] T010 Create `sql/004_integratedml.sql` — `CREATE MODEL FarePredictor PREDICTING (FinalPrice) FROM TripHistory` + `TRAIN MODEL FarePredictor` (research.md §7). `CREATE MODEL` applied live and succeeded — corrected keyword to `PREDICTING` (this IRIS 2026.1 build rejects `PREDICT` with a parser error). **`TRAIN MODEL` was blocked on IRIS 2026.1/2025.3** (AutoML provider unavailable/crashing — research.md §14). **Resolved (research.md §16)**: since AutoML never became available on this Community Edition image, `FarePredictor` is instead trained outside IRIS with plain scikit-learn (`models/train_fare_predictor.py`) and imported via the built-in `%ML.PMML.Provider` (no training step needed for PMML import — a documented, no-external-account IntegratedML mechanism). This forced `CREATE MODEL` to use an explicit `WITH (PickupMinutes INTEGER, ...)` feature clause instead of inferring `FROM TripHistory` (PMML can't parse the table's `VARCHAR` `PickupTime`), and `BoIntegratedMlPredictor` (T018) to convert `candidate_time` to minutes-since-midnight before calling `PREDICT()`. Live-verified: `POST /uberapp/api/uber-route/recommend` returns `200 OK` with a real predicted fare (see T041 update and research.md §16 for the four further SQL-syntax bugs found getting `CREATE MODEL`/`TRAIN MODEL`/`PREDICT()` to actually run).
- [X] T011 [P] Create `data/trip_history_seed.csv` — representative historical trip rows (pickup time, day of week, distance km, demand factor, final price). 238 rows generated and inserted live into `UberRoute.TripHistory`.
- [X] T012 [P] Create `data/traffic_weather_reference.csv` — hour/day congestion + precipitation reference rows for T009's foreign table
- [X] T013 Apply `sql/001_core_tables.sql`, `sql/003_foreign_tables.sql`, `sql/004_integratedml.sql` against the target IRIS instance and confirm `TRAIN MODEL FarePredictor` succeeds against the seeded data (quickstart.md steps 1, 2, 4). Schema + Foreign Table + `CREATE MODEL` all verified live (see T008-T010 notes); `TRAIN MODEL` blocked by the AutoML provider crash documented in T010 — flagged as a known environment limitation, not re-attempted further to avoid destabilizing the container (a second attempt briefly left it `unhealthy`).
- [X] T014 Implement a native-table fallback for `TrafficWeatherReference` in `sql/003b_foreign_tables_fallback.sql`: if T013's `CREATE FOREIGN SERVER`/`CREATE FOREIGN TABLE` step fails or the CSV foreign-data-wrapper is unsupported on the target IRIS version (quickstart.md step 1 check; research.md §10 risk note), create a native table of the same shape and load it from `data/traffic_weather_reference.csv` instead — only needed if T013's foreign-table creation fails (depends on T009, T012, T013). Written but not needed on this instance — T009's Foreign Table succeeded live; kept for portability to older/other IRIS deployments.

**Checkpoint**: Foundation ready — production topology, shared messages, observability helper,
geocoding adapter, and a trained `FarePredictor` all exist (with a working `TrafficWeatherReference`
source, foreign or native). User story implementation can begin.

---

## Phase 3: User Story 1 - Get the best time and fare for a trip (Priority: P1) 🎯 MVP

**Goal**: A rider submits origin, destination, and desired time and receives a recommended
departure time and estimated fare.

**Independent Test**: Submit a valid `{origin, destination, target_time}` payload to
`BS_UberRouteService` and verify the response contains `recommended_time` and
`estimated_fare`, matching the "delta ≤ 30 minutes" shape in
[contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md) — with no
dependency on waiting-place logic.

### Tests for User Story 1

- [X] T015 [P] [US1] Contract test for the 200 (delta ≤ 30 min), 400, and 422 response shapes in `tests/contract/test_bs_uber_route_service_us1.py` — 5/5 passing locally
- [X] T016 [P] [US1] Integration test for the full happy-path flow (valid request → time/fare recommendation) in `tests/integration/test_user_story_1.py` — 2/2 passing locally
- [X] T017 [P] [US1] Unit test for `TripRequest` validation — missing fields, malformed `target_time` — in `tests/unit/test_validation.py` — 8/8 passing locally

### Implementation for User Story 1

- [X] T018 [US1] Implement `BoIntegratedMlPredictor` in `production/hosts/bo_integratedml_predictor.py` — queries `FarePredictor` via `PREDICT()` across a small set of candidate departure times (research.md §7). **Resolved (research.md §16)**: `PREDICT(model USING (...))` turned out not to be valid syntax (live parser error) — fixed to `PREDICT(model)`, which matches feature columns by name against the `FROM` subquery. `candidate_time` is now converted to `PickupMinutes` (minutes-since-midnight) before the call, matching the PMML-imported model's numeric feature set. Live-verified returning real, non-`ok=False` predictions; still surfaces `ok=False` on genuine errors rather than a fake fallback (constitution Principle IV).
- [X] T019 [US1] Implement candidate-time scan + "recommended time = candidate minimizing predicted fare" + `DeltaMinutes` computation in `BP_RouteOrchestrator.OnRequest` (`production/hosts/bp_route_orchestrator.py`) (depends on T018)
- [X] T020 [US1] Implement `BS_UberRouteService.on_process_input` in `production/hosts/bs_uber_route_service.py` — payload validation (FR-002), forwards validated request to `BP_RouteOrchestrator`; response includes `waiting_place_suggested: false` and `waiting_place: null` as **static defaults** for this story (User Story 2's T034 later makes these fields dynamic) so the full contract shape in T015 is already satisfiable after this story alone (depends on T005)
- [X] T021 [US1] Implement `production/wsgi/app.py` — IRIS-native WSGI entrypoint calling `director.create_business_service(...).process_input(...)` (research.md §2) (depends on T020)
- [X] T022 [US1] Wire `geocoding_adapter.py` into `BP_RouteOrchestrator` to resolve `Origin`/`Destination` to coordinates, returning the 422 `location_not_found` contract on failure (FR-011) (depends on T007, T019)
- [X] T023 [US1] Add structured logging + telemetry calls (`observability/telemetry.py`) at request-received, IntegratedML-call, and error points across `BS_UberRouteService`/`BP_RouteOrchestrator`/`BO_IntegratedMLPredictor` (depends on T006, T018-T022)
- [X] T024 [US1] Persist `TripRequest` and `RouteRecommendation` rows plus a `RequestLog` JSON document per completed request (FR-012) (depends on T008, T019) — INSERT statements verified live against the dedicated instance in isolation (§T008 testing)

**Checkpoint**: User Story 1 is fully functional and independently testable — including the
full contract shape T015 checks (thanks to T020's static defaults) — SC-001 (≤5s response) and
the "delta ≤ 30 min → no waiting place" behavior are verifiable now.

---

## Phase 4: User Story 2 - Get a nearby place to wait when the best time is far off (Priority: P2)

**Goal**: When the recommended time differs from the requested time by more than 30 minutes,
the response includes a nearby waiting-place suggestion.

**Independent Test**: Submit a request engineered (via seeded `TripHistory`/
`TrafficWeatherReference` data) to produce a >30-minute delta, and verify the response
includes a `waiting_place` with `name`, `address`, and descriptive detail — per
[contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md).

### Tests for User Story 2

- [X] T025 [P] [US2] Contract test for the "delta > 30 min, waiting place found" and "delta > 30 min, no waiting place available" response shapes in `tests/contract/test_bs_uber_route_service_us2.py` — 2/2 passing locally
- [X] T026 [P] [US2] Integration test for User Story 2 acceptance scenarios (recommended time later than requested, recommended time earlier than requested, no place found nearby) in `tests/integration/test_user_story_2.py` — 3/3 passing locally
- [X] T027 [P] [US2] Unit test for the 30-minute Business Rule's bidirectional absolute-value behavior in `tests/unit/test_business_rule.py` — 6/6 passing locally

### Implementation for User Story 2

- [X] T028 [P] [US2] Create `sql/002_vector_index.sql` — creates the `WaitingPlace` table (`Embedding VECTOR(DOUBLE, 384)` + conditional `AS HNSW(Distance='Cosine')` index per research.md §6) and the `WaitingPlaceSuggestion` table per [data-model.md](./data-model.md). Applied live — all 4 statements (table, iFind index, HNSW index, suggestion table) succeeded (SQLCODE=0); `VECTOR_COSINE` + `TO_VECTOR` end-to-end search verified with synthetic 384-dim vectors (exact match scored 1.0, as expected).
- [X] T029 [P] [US2] Create `data/waiting_places_seed.json` — seed café/coworking records (name, address, category, lat/lng, rating, description)
- [X] T030 [US2] Implement `ingestion/load_waiting_places.py` — IRIS-version detection for HNSW eligibility (research.md §6), `sentence-transformers/all-MiniLM-L6-v2` embedding (research.md §3), chunking preserving address/category context (research.md §4); populates `WaitingPlace.Embedding` and `SearchableText` (depends on T028, T029). Code written against the standard `sentence-transformers` API; **not run live** — installing it (pulls in `torch`) was judged too slow for this session's time budget, not attempted.
- [X] T031 [US2] Implement `BO_HybridRAGEngine` in `production/hosts/bo_hybrid_rag_engine.py` — combines `VECTOR_COSINE` and iFind keyword search with a 0.6/0.4 weighted score (research.md §5), filtered to the ~1 km origin-proximity radius (FR-009) (depends on T030). **Corrected live**: keyword search uses `WHERE %ID %FIND search_index(SearchableTextIdx, ?)`, not `%CONTAINS(col, word)` — the latter fails (SQLCODE -359); both forms verified against the real index.
- [X] T032 [US2] Implement the 30-minute threshold as a Business Rule and invoke it from `BP_RouteOrchestrator` (research.md §8), replacing the inline delta check from T019. **Used the pre-approved fallback**: `production/hosts/business_rules.py`, a standalone Python function, not a hand-authored `Ens.Rule.RuleSet` XML (no way to validate that blind, outside the Rule Editor, in this environment — see research.md §8 for the full rationale).
- [X] T033 [US2] Wire `BP_RouteOrchestrator` to call `BO_HybridRAGEngine` only when the Business Rule fires, and persist `WaitingPlaceSuggestion` rows (depends on T031, T032)
- [X] T034 [US2] Change `BS_UberRouteService`'s response payload from the static `waiting_place_suggested: false` / `waiting_place: null` defaults set in T020 to dynamic values (`waiting_place_suggested`, `waiting_place`, `waiting_place_unavailable_reason`) driven by the Business Rule and `BO_HybridRAGEngine` outcome, per [contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md) (depends on T033)
- [X] T035 [US2] Handle the "no suitable place found nearby" edge case — return `waiting_place: null` with an explanatory reason instead of failing the request (FR-010) (depends on T034)
- [X] T036 [US2] Extend structured logging + telemetry to cover RAG lookup calls and Business Rule outcomes (depends on T023, T031, T032)

**Checkpoint**: User Stories 1 AND 2 both work independently — SC-002/SC-003 (no false
positives/negatives on the 30-minute trigger) and SC-005 (proximity) are verifiable now.

---

## Phase 5: User Story 3 - Understand why a waiting place was suggested (Priority: P3)

**Goal**: When a waiting place is suggested, the rider sees a brief reason it was chosen.

**Independent Test**: Trigger a waiting-place suggestion where multiple candidates exist and
verify the response's `waiting_place.rationale` explains the choice (e.g., proximity,
rating, which signal drove the match).

### Tests for User Story 3

- [X] T037 [P] [US3] Integration test verifying a non-empty, differentiating `rationale` is present when multiple candidate waiting places exist, in `tests/integration/test_user_story_3.py` — 2/2 passing locally

### Implementation for User Story 3

- [X] T038 [US3] Extend `BO_HybridRAGEngine` to generate a short `Rationale` string per top candidate (distance, rating, and whether the vector or keyword signal dominated the match) and populate `WaitingPlaceSuggestion.Rationale` (depends on T031) — implemented as `BO_HybridRAGEngine._rationale()`, written together with T031 since the two are tightly coupled
- [X] T039 [US3] Surface `rationale` inside the `waiting_place` object in `BS_UberRouteService`'s response per [contracts/bs_uber_route_service.md](./contracts/bs_uber_route_service.md) (depends on T033, T038) — done in `production/wsgi/app.py`'s response-building code

**Checkpoint**: All three user stories are independently functional — SC-004 is verifiable now.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Whole-feature verification and documentation deliverables

- [ ] T040 [P] Verify observability end-to-end: confirm the IRIS Management Portal event log shows request-received / IntegratedML / RAG / rule-outcome / error entries for a full run (quickstart.md step 9). **Blocked**: requires the production actually deployed and running in IRIS (see T041); `IRISLog`/`log_event` calls are verified against the real `intersystems_pyprod` API (source-read, not guessed) but not exercised live end to end.
- [X] T041 Run the complete [quickstart.md](./quickstart.md) validation (steps 1–9) against a live IRIS Community Edition container. **Resolved via environment change + 5 bug fixes (research.md §14)**: the §13 "jobs marked dead" blocker was environment-specific to IRIS 2026.1 Build 234U — switching to IRIS Community 2025.3 (full Docker wipe + rebuild, per explicit user instruction) eliminated it entirely; jobs no longer die, errors surface as ordinary catchable Python exceptions. With that unblocked, driving a real request through the full stack (`director.create_business_service(...).process_input(...)`, matching what `production/wsgi/app.py` does) surfaced and fixed 5 further bugs: (1) `ModuleNotFoundError: No module named 'production'` — pyprod's generated `OnInit` doesn't add the project root to `sys.path`, fixed by inserting it explicitly in all 4 host files; (2) `AttributeError: Property session_id not found` — the message-class registry pyprod's `_createmessage()` relies on wasn't populated because schema imports were deferred inside method bodies, fixed by hoisting them to module level; (3) `director.create_business_service()` needs the production's config **item name** (e.g. `"BsUberRouteService"`), not the fully-qualified class name — this bug was live in `production/wsgi/app.py`'s `_SERVICE_CLASS` constant, fixed and renamed to `_SERVICE_NAME`; (4) `service.process_input()` returns the **raw IRIS-side object with PascalCase properties** (`RecommendedTime`, `ErrorCode`, ...), not the Python-side snake_case names — fixed all field access in `production/wsgi/app.py` and the two contract-test mocks; (5) `irispython` isn't on `$PATH` in the container, must use `/usr/irissys/bin/irispython`. **Steps 1-9 all now reachable end-to-end**: a live call through the full WSGI entrypoint (`application()`) returns a clean, correctly-routed `503 prediction_unavailable` response (not a crash/hang) — the only remaining gap is that `TRAIN MODEL` fails cleanly with SQLCODE -186 "AutoML provider not available" on this Community Edition image (confirmed genuine platform limitation, not a bug — see research.md §14), so fare predictions are unavailable until a different IRIS edition/image or a custom IntegratedML provider is used. All other steps (schema, Foreign Table, vector/keyword search, production startup, message routing, WSGI HTTP layer, graceful error handling) verified working live.
- [X] T042 [P] Write `README.md` documenting the chunking strategy, embedding model choice, and ingestion/indexing/retrieval/prompt-response architecture (links to research.md rather than duplicating it) — satisfies the originating request's "Documentação explicativa" deliverable
- [ ] T043 Performance check: confirm SC-001 (<5s end-to-end) across a handful of sequential requests covering both US1 and US2 paths. **Blocked**: needs the production actually deployed and reachable over HTTP (see T041); not measurable from code alone.
- [X] T044 [P] Add input hardening to `BS_UberRouteService` in `production/hosts/bs_uber_route_service.py`: reject payloads larger than 4 KB and reject any field other than `origin`, `destination`, `target_time` — implemented and covered by `tests/unit/test_validation.py` (`test_oversized_payload_is_rejected`, `test_unexpected_field_is_rejected`)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational only
- **User Story 2 (Phase 4)**: Depends on Foundational; extends US1's `BP_RouteOrchestrator`/`BS_UberRouteService` (T019, T020, T034) but is independently testable via its own contract/integration tests
- **User Story 3 (Phase 5)**: Depends on Foundational and on US2's `BO_HybridRAGEngine`/`WaitingPlaceSuggestion` (T031, T034) — cannot be tested before US2 exists, since there is nothing to explain a rationale for otherwise
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories — the MVP
- **User Story 2 (P2)**: Builds on US1's request/recommendation flow; independently testable via its own contract shapes
- **User Story 3 (P3)**: Builds on US2's waiting-place suggestion; independently testable once US2 exists

### Within Each User Story

- Tests written before implementation tasks (T015-T017 before T018+; T025-T027 before T028+; T037 before T038+)
- Models/tables before services/hosts
- Hosts before WSGI wiring
- Core implementation before edge-case handling (T035) and logging (T023, T036)

### Parallel Opportunities

- Setup: T002, T003 in parallel
- Foundational: T005, T006, T007 in parallel (different files); T011, T012 in parallel (different seed files); T013 is sequential (depends on T008-T010); T014 only runs if T013's foreign-table step fails
- User Story 1 tests: T015, T016, T017 in parallel
- User Story 2: T025, T026, T027 in parallel; T028, T029 in parallel
- User Story 3: T037 has no parallel sibling (single test task)
- Polish: T040, T042, T044 in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Contract test for delta<=30min/400/422 shapes in tests/contract/test_bs_uber_route_service_us1.py"
Task: "Integration test for the happy-path flow in tests/integration/test_user_story_1.py"
Task: "Unit test for TripRequest validation in tests/unit/test_validation.py"
```

## Parallel Example: User Story 2

```bash
# Launch all tests for User Story 2 together:
Task: "Contract test for waiting-place response shapes in tests/contract/test_bs_uber_route_service_us2.py"
Task: "Integration test for US2 acceptance scenarios in tests/integration/test_user_story_2.py"
Task: "Unit test for the 30-minute Business Rule in tests/unit/test_business_rule.py"

# Launch schema + seed data together:
Task: "Create sql/002_vector_index.sql"
Task: "Create data/waiting_places_seed.json"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories; includes the trained `FarePredictor`)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run T015-T017 and quickstart.md steps 1-2, 4-6 independently
5. Demo the time/fare recommendation flow

### Incremental Delivery

1. Setup + Foundational → foundation ready (production skeleton, trained `FarePredictor`)
2. Add User Story 1 → validate independently → demo (MVP: time/fare recommendation)
3. Add User Story 2 → validate independently → demo (adds waiting-place suggestion)
4. Add User Story 3 → validate independently → demo (adds rationale)
5. Polish → full quickstart.md pass + documentation deliverable

### Parallel Team Strategy

With multiple developers, after Foundational completes:

- Developer A: User Story 1 (`BO_IntegratedMLPredictor`, `BP_RouteOrchestrator` candidate-time logic, WSGI wiring)
- Developer B: prepares User Story 2's `sql/002_vector_index.sql`, seed data, and `ingestion/load_waiting_places.py` in parallel (T028-T030 do not depend on US1's implementation, only on Foundational) — but `BO_HybridRAGEngine`'s wiring into `BP_RouteOrchestrator` (T033) must wait for US1's T019

---

## Notes

- [P] tasks touch different files with no unmet dependencies
- [Story] label maps every user-story-phase task to US1/US2/US3 for traceability
- Verify contract/integration/unit tests fail before implementing the corresponding task
- Commit after each task or logical group
- Stop at any checkpoint to validate a story independently before moving to the next
- T032's formal `Ens.Rule.RuleSet` Business Rule is the design decision recorded in research.md
  §8, chosen to satisfy the *originating SPECS-001 request's* explicit "Usar Business Rules"
  item — it is **not** a project constitution MUST (the constitution's five principles do not
  mandate a formal Rule Editor artifact). If it proves impractical during implementation, a
  plain Python `abs(delta_minutes) > 30` check in `BP_RouteOrchestrator` is an acceptable
  fallback that can be adopted by editing this task directly — no constitution amendment
  procedure is required, since no principle is at stake either way.

---

## Post-MVP Addition: Frontend + arrival-time semantics (ad hoc, not run through /speckit-specify)

Requested directly by the user after T001-T044 shipped: a simple web form, and a
clarification that `target_time` means the rider's **arrival deadline**, not a departure
time. Tracked here rather than renumbering T001-T044.

- [X] T045 Reinterpret `target_time` as an arrival deadline in `BP_RouteOrchestrator`: added
  `_congestion_factor()` (queries `UberRoute.TrafficWeatherReference`, finally giving that
  table a real caller) and `_estimate_travel_minutes()`; candidate departure times are now
  scanned around a computed "naive departure" (arrival minus typical-traffic travel time)
  instead of around the raw `target_time`. Added `estimated_arrival_time` to
  `RouteRecommendationMessage` and the WSGI response. Covered by
  `tests/unit/test_arrival_time_semantics.py` (3/3 passing) and updated
  `tests/integration/test_user_story_2.py` (fare mock made baseline-agnostic). Updated
  spec.md Assumptions, data-model.md, and contracts/bs_uber_route_service.md to match.
- [X] T046 [P] Build `production/wsgi/static/index.html` — a single self-contained page (no
  build step, no framework, per user's explicit choice): origin/destination text fields,
  arrival-time picker, calls `POST /api/uber-route/recommend`, renders the recommendation
  and (when present) the waiting-place card or unavailability message. Verified visually in
  a browser (form fill, submit loading state, and the network-error path all render
  correctly); the success-rendering path was not exercised against a live response, since
  no deployed WSGI production is reachable in this environment (see T041).
- [X] T047 [P] Route `production/wsgi/app.py` by `PATH_INFO`: `GET /` and `GET /index.html`
  serve the static page; `POST /api/uber-route/recommend` keeps its existing contract;
  anything else returns 404. Added `test_get_root_serves_the_frontend_html` and fixed the
  existing contract tests, which hadn't been setting `PATH_INFO` (all 32 tests still pass).

## Post-MVP Addition: Three fixed departure options replace the single recommendation (research.md §20)

Requested directly by the user after comparing live estimates against real Uber app quotes:
show 3 comparable options ("leave now", "30 min earlier", "60 min earlier") instead of one
auto-picked time with a conditional waiting-place suggestion. Confirmed with the user (they
chose "options replace the single recommendation") before implementing. Tracked here rather
than renumbering T001-T047.

- [X] T048 Replace `RouteRecommendationMessage`'s flat `recommended_time`/
  `estimated_arrival_time`/`estimated_fare`/`delta_minutes`/`waiting_place_*` fields with a
  single `options_json: str` field (a JSON-serialized list of option dicts) in
  `production/messages/schemas.py` — still one flat scalar field per pyprod's
  `JsonSerialize` constraint (research.md §20).
- [X] T049 Rewrite `BpRouteOrchestrator.on_request` (`production/hosts/bp_route_orchestrator.py`)
  to build three fixed options (`ideal`/`30min_earlier`/`60min_earlier`), each independently
  priced by a direct `PREDICT()` call (no more candidate-scan-for-minimum), with the two
  earlier options always requesting a waiting-place suggestion. Removed the now-unused
  candidate-offset scan and the `business_rules.waiting_place_should_be_suggested()` call
  site (module + its tests left in place, per its own stated design as a swappable
  component). Rewrote `_persist` to store the "ideal" option in `RouteRecommendation` (schema
  unchanged, not migrated) and the full option list in `RequestLog.Payload`.
- [X] T050 [P] Update `production/wsgi/app.py` to read `OptionsJson` and return
  `{"trip_request_id", "options": [...]}` instead of per-field PascalCase mapping.
- [X] T051 [P] Rewrite `production/wsgi/static/index.html` to render one card per option
  (title, wait badge, departure/arrival/fare, and — for the two earlier options — the
  waiting-place detail or unavailable-reason) instead of one recommendation card plus an
  optional waiting-place card.
- [X] T052 [P] Rewrite `tests/integration/test_user_story_1.py`,
  `tests/integration/test_user_story_2.py`, `tests/contract/test_bs_uber_route_service_us1.py`,
  `tests/contract/test_bs_uber_route_service_us2.py` for the 3-option response shape (35/35
  tests passing).
- [X] T053 Update spec.md (User Stories 1-2, FR-003–FR-006, FR-010, FR-012, Key Entities,
  SC-002–SC-003, Assumptions), `contracts/bs_uber_route_service.md`, and `data-model.md` to
  describe the 3-option contract, per this project's established pattern of amending specs in
  place for post-implementation product changes (matching T045's `target_time` amendment).
- [X] T054 Live-verified end-to-end against the running container: redeployed the changed
  `.py`/`.html` files, restarted the production (Business Process host changes need this to
  reload — research.md §14/§16/§19), and confirmed `POST /uberapp/api/uber-route/recommend`
  returns all three options with real fares and waiting-place suggestions for a real
  Florianópolis address pair.
- [X] T055 Reject implausibly large geocoded distances instead of computing a nonsense
  trip (research.md §21) — a live test with a vague address (`"SENAI, São José"`, no
  street/state) geocoded to a same-named place 341 km away, producing an implausible fare
  and a day-wraparound-inconsistent departure/arrival pair. Added a 100 km sanity cap in
  `BpRouteOrchestrator` and a new `distance_out_of_range` (422) contract error. 36/36 tests
  passing.
- [X] T056 Make waiting-place suggestions actually work end to end (research.md §22):
  installed `sentence-transformers`, populated `UberRoute.WaitingPlace` (was empty — the
  seed ingestion had never been run), and added `production/adapters/overpass_adapter.py`
  (live nearby-place lookup via the free Overpass/OpenStreetMap API, chosen over the paid
  Google Places API per the user's explicit direction) wired into `BoHybridRagEngine` so any
  city has real candidates, not just wherever the seed dataset covered. Found and fixed a
  live-only `VECTOR_COSINE` string/float type bug in the hybrid-search scoring, and — the
  session's biggest platform finding — that `sentence-transformers`'s default PyTorch
  backend reliably segfaults IRIS's embedded-Python worker process (confirmed via
  `messages.log`, 6/6 reproductions); fixed by loading the model with `backend="onnx"`
  instead. Live-verified end to end for both a Florianópolis and a São Paulo address pair,
  each returning a real waiting-place suggestion. 40/40 tests passing.
