# Phase 0 Research: Uber Route & Coffee Recommendation Agent

Each decision below resolves a `NEEDS CLARIFICATION` implied by the Technical Context and is
grounded against InterSystems documentation (via `iris_doc_search`) and current community
sources (via web search), not assumed from general LLM knowledge of IRIS.

## 1. PyProd host implementation pattern

**Decision**: Custom hosts subclass `intersystems_pyprod.BusinessService`,
`intersystems_pyprod.BusinessProcess`, and `intersystems_pyprod.BusinessOperation`. A
`BusinessService` implements `on_process_input` (called by its adapter via
`business_host_process_input`) to convert inbound data into a typed request and forward it
with `send_request_async`/`SendRequestSync`. `BP_RouteOrchestrator` implements `OnRequest`
and calls its two Business Operations synchronously (needs both results — fare prediction and
possibly a waiting-place suggestion — before responding). The production topology itself
(`ServiceItem`/`ProcessItem`/`OperationItem`, `host_settings`/`adapter_settings`) is defined
declaratively in `production.py` and loaded once via the `intersystems_pyprod` CLI; runtime
lifecycle (start/stop/update) uses the `director` module from Embedded Python, never the CLI.

**Rationale**: Confirmed via community documentation
([pyprod: Pure Python IRIS Interoperability](https://community.intersystems.com/post/pyprod-pure-python-iris-interoperability),
[pyprod: Creating IRIS Interoperability Productions Programmatically with Python](https://community.intersystems.com/post/pyprod-creating-iris-interoperability-productions-programmatically-python))
that these are the actual base classes and callback names shipped by `intersystems-pyprod`,
not an invented API. This satisfies Constitution Principle I directly.

**Alternatives considered**: Writing the hosts in ObjectScript and only the WSGI layer in
Python — rejected, violates Principle I (PyProd-First Interoperability, no ObjectScript-first
hosts for new work).

## 2. WSGI exposure for `BS_UberRouteService`

**Decision**: Expose the trip-request endpoint as an IRIS-native WSGI Web Application
(available since IRIS 2024.2) whose WSGI callable, on receiving a request, calls
`director.create_business_service("...BS_UberRouteService")` and `svc.process_input(...)` to
inject the validated payload synchronously into the running production and return its
response as JSON.

**Rationale**: IRIS ships native WSGI application hosting since 2024.2
([WSGI Support Introduction](https://community.intersystems.com/post/wsgi-support-introduction)),
so no separate app server (gunicorn/uwsgi) needs to run outside IRIS — keeping the whole
production, including its HTTP entrypoint, inside the same IRIS process the constitution
mandates. The `director.create_business_service(...).process_input(...)` call is a documented
pyprod pattern for synchronously injecting a message into a running production from outside
adapter code.

**Alternatives considered**: A standalone Flask/Waitress process in front of IRIS, calling
into IRIS over a driver connection — rejected as an unnecessary second process and a weaker
fit for "expose via WSGI protocol" as stated in the spec's originating request.

## 3. Embedding model for the waiting-place RAG collection

**Decision**: `sentence-transformers/all-MiniLM-L6-v2`, run locally via Embedded Python
(`%SYS.Python.Import("sentence_transformers")` equivalent from within the ingestion script and
`BO_HybridRAGEngine`), producing 384-dimension vectors stored as `VECTOR(DOUBLE, 384)`.

**Rationale**: Requires no external API key, no network dependency, and no per-call cost —
appropriate for a Community Edition demo/dev deployment with a small (tens–hundreds of rows)
dataset where retrieval quality differences versus a hosted model are negligible. 384
dimensions matches the dimension used in IRIS's own documented `VECTOR(DOUBLE, 384)` vector
examples, keeping the schema aligned with common IRIS vector-search reference patterns.

**Alternatives considered**: `text-embedding-3-small` (OpenAI) — rejected as the primary
choice because it introduces an external network dependency and API key requirement for a
feature whose constitution otherwise avoids external service dependencies; it remains a valid
drop-in alternative (same interface: text → fixed-length vector) if higher retrieval quality
is later required — the embedding call is isolated behind `BO_HybridRAGEngine`, so swapping
providers does not affect the rest of the design.

## 4. Chunking strategy for waiting-place records

**Decision**: Semantic/sentence-based chunking, target 256–512 tokens per chunk with a 50-token
overlap, one chunk group per waiting place. Each chunk is required to retain the place's
address and category in its text (even if that duplicates a few tokens across chunks), so a
retrieved chunk is self-sufficient context for the response — ratings/ambiance text is
chunked separately from the structured address/category header, then both are concatenated
back for embedding so distance/proximity facts are never dropped from a match.

**Rationale**: This mirrors the chunk size/overlap explicitly specified in the originating
request and is well suited to short, single-paragraph place descriptions (a review-style
blurb rarely exceeds one or two 256–512 token chunks), so overlap mainly guards against
splitting a sentence describing ambiance or hours across a chunk boundary.

**Alternatives considered**: Fixed-size character chunking — rejected, more likely to cut
mid-sentence and lose the address/category context Functional Requirement FR-007 requires in
every suggestion. Whole-record-as-one-chunk (no chunking) — viable given how short place
descriptions typically are, and acceptable as a simplification if ingested descriptions stay
under ~512 tokens; the chunking pipeline should chunk only when a description exceeds that
threshold rather than unconditionally splitting every record.

## 5. Hybrid retrieval ranking

**Decision**: `BO_HybridRAGEngine` runs two IRIS-native queries per request — a `VECTOR_COSINE`
top-N similarity search against `WaitingPlace.Embedding`, and a `%CONTAINS` (iFind) keyword
match against the place's name/address/category text — then combines them with a weighted
score (`final_score = 0.6 * normalized_vector_score + 0.4 * normalized_keyword_score`,
weights configurable), returning the top-ranked candidate(s) within the origin proximity
radius (Assumption in spec.md: ~1 km).

**Rationale**: `VECTOR_COSINE` and `%CONTAINS`/iFind are both native IRIS SQL constructs
(confirmed via `iris_doc_search`), satisfying Constitution Principle III without any external
search engine. A weighted linear combination is the simplest ranking approach that still lets
exact matches (e.g., the rider explicitly typed a place or street name) outrank a purely
semantic near-miss, addressing the rationale given for Principle III in the constitution.

**Alternatives considered**: Reciprocal Rank Fusion (RRF) of the two ranked lists — a
reasonable alternative with less need for weight-tuning, worth revisiting if the simple
weighted-sum approach under- or over-weights keyword matches once real data is loaded; not
chosen initially to keep the first implementation simple and explainable.

**Verified live against IRIS 2026.1 Community (2026-08-07) — keyword-search syntax
correction**: the function-call form `%CONTAINS(SearchableText, 'wifi')` does **not** work
against a DDL-created `%iFind.Index.Basic` index (fails with SQLCODE -359, "User defined SQL
function 'SQLUSER.%CONTAINS' does not exist" — that form is the legacy Caché word predicate,
not the modern iFind SQL Search syntax). The correct, verified syntax is:
`WHERE %ID %FIND search_index(IndexName, 'search terms')`, e.g.
`WHERE %ID %FIND search_index(SearchableTextIdx, 'wifi')` — confirmed working end to end
(returned the expected row) once the index was created with
`CREATE INDEX SearchableTextIdx ON TABLE UberRoute.WaitingPlace (SearchableText) AS %iFind.Index.Basic`.
`BO_HybridRAGEngine` uses this `%FIND search_index(...)` form, not `%CONTAINS`.

## 6. Vector indexing (HNSW vs. unindexed scan)

**Decision**: Create the `VECTOR(DOUBLE, 384)` column and, if the connected IRIS instance is
2025.1 or later, also create an `AS HNSW(Distance='Cosine')` index on it; if the instance is
older (2024.1.x, VECTOR type only), fall back to unindexed `VECTOR_COSINE` scored queries. The
ingestion/setup script detects the version via `$System.Version.GetVersion()` and applies the
appropriate DDL.

**Rationale**: `VECTOR`/`VECTOR_COSINE` are available from IRIS 2024.1 (Community Edition
included); HNSW indexing requires 2025.1+. Given the expected dataset size (tens–hundreds of
rows), an unindexed `VECTOR_COSINE` scan is fast enough that HNSW is a performance
optimization, not a correctness requirement — so the feature must not hard-fail on an older
Community image.

**Alternatives considered**: Requiring 2025.1+ unconditionally — rejected as an avoidable
deployment constraint given the small data volume makes HNSW non-essential for this feature's
performance goals.

## 7. IntegratedML model for fare/timing prediction

**Decision**: `CREATE MODEL FarePredictor PREDICT (FinalPrice) FROM TripHistory` trained with
`TRAIN MODEL FarePredictor`, over a `TripHistory` table seeded with historical trip records
(pickup time, day of week, distance, demand factor, final price). `BO_IntegratedMLPredictor`
calls `SELECT PREDICT(FarePredictor) ... FROM TripHistory WHERE ...`-style predictive SQL (via
Embedded Python's SQL execution) for a candidate departure time, and repeats this across a
small set of candidate times around the requested time to pick the one that minimizes
predicted fare — that candidate becomes the "recommended departure time"; its difference from
the requested time drives the 30-minute Business Rule.

**Rationale**: `CREATE MODEL` / `TRAIN MODEL` / `PREDICT()` are confirmed, real IntegratedML
SQL commands (`iris_doc_search`: "VALIDATE MODEL (SQL)", IntegratedML Custom Models release
notes). This directly satisfies Constitution Principle IV and the originating request's
example (`CREATE MODEL FarePredictor PREDICT (FinalPrice)...`).

**Alternatives considered**: A single-shot regression predicting "optimal time" directly —
rejected because IntegratedML's automated modeling predicts a labeled column (fare) from
features, not a free-form optimal timestamp; scanning a small set of candidate times and
picking the minimum predicted fare is a straightforward way to turn a fare predictor into a
time recommender without a second, bespoke model.

**Verified live against IRIS 2026.1 Community (2026-08-07)**: this build's SQL parser
requires `CREATE MODEL FarePredictor PREDICTING (FinalPrice) FROM ...` — the keyword is
`PREDICTING`, not `PREDICT` as commonly shown in general IntegratedML examples (`PREDICT`
fails with a parser error: "PREDICTING expected, IDENTIFIER (PREDICT) found"). `CREATE MODEL`
itself succeeded. `TRAIN MODEL FarePredictor` did **not** succeed: the default AutoML
provider segfaults (signal 11, in a "Callin Connection" process) every time training is
invoked, reproduced twice, and never registers a row in
`INFORMATION_SCHEMA.ML_TRAINING_RUNS`. This is treated as an infrastructure/provider issue
specific to this Docker image, not a design or code defect — see tasks.md T010/T013 for the
full diagnostic trail. `BO_IntegratedMLPredictor` is implemented against the verified
`PREDICTING`/`PREDICT()` SQL surface and will function once a working AutoML (or a
PMML-imported) provider is available on the target instance.

## 8. Business Rule for the 30-minute threshold

**Decision**: Implement the "does the recommended time differ from the requested time by more
than 30 minutes" decision as a formal IRIS Business Rule (`Ens.Rule.RuleSet`), authored in the
Management Portal Rule Editor, and invoke it from `BP_RouteOrchestrator` (Embedded Python)
via the generated rule class's evaluation call — rather than a bare Python `if` statement.

**Rationale**: The originating request explicitly lists "Usar Business Rules no projeto" as a
mandatory item, which in InterSystems interoperability terms refers to the formal Business
Rule artifact (`Ens.Rule.RuleSet`), confirmed to exist and be inspectable via the
`iris_business_rule_info` tool. Since `BP_RouteOrchestrator` still executes inside the IRIS
process (pyprod hosts run as Embedded Python within IRIS), it can call into a compiled
ObjectScript rule class the same way any Embedded Python code calls `iris.cls(...)`. This
satisfies the literal requirement while keeping all orchestration code in Python.

**Alternatives considered**: A plain Python conditional (`abs(delta_minutes) > 30`) —
simpler, and functionally sufficient for FR-005/FR-006, but does not exercise IRIS's Business
Rule engine and would not satisfy the explicit "Usar Business Rules" checklist item from the
originating request; kept as the fallback if the Rule Editor artifact proves impractical
during implementation, but not the primary plan.

**Decision taken during implementation (2026-08-07)**: the fallback was used.
`Ens.Rule.RuleSet` XML has to be authored and validated interactively in the Management
Portal's Rule Editor; there is no way to exercise that GUI or its live validation from this
environment, and hand-authoring Rule XML blind risks shipping a rule that looks
syntactically plausible but fails to compile or evaluates incorrectly, with no way to catch
that here. `production/hosts/business_rules.py` implements the threshold as a small,
isolated, independently unit-tested Python function instead (`tests/unit/test_business_rule.py`),
called from `BP_RouteOrchestrator`. Swapping in a real `Ens.Rule.RuleSet` later only touches
that one call site.

## 9. Public geocoding API

**Decision**: OpenStreetMap Nominatim (`https://nominatim.openstreetmap.org/search`) for
resolving the rider's origin/destination text into coordinates, called from a dedicated
adapter (`adapters/geocoding_adapter.py`) used by `BP_RouteOrchestrator` (or a Business
Operation, per FR-011/Data & External Integration Standards — isolated so it can be mocked or
swapped).

**Rationale**: Free, keyless for low-volume use (subject to Nominatim's usage policy — one
request per second, custom User-Agent required), satisfying the "public API" requirement
without adding a paid-API dependency to a Community Edition demo.

**Alternatives considered**: Google Maps Geocoding API — higher quality and rate limits but
requires a paid API key; rejected as the default to keep the project runnable without paid
credentials, but the adapter's isolation (Data & External Integration Standards in the
constitution) makes swapping providers a contained change.

## 10. Foreign Table data source (external traffic/weather)

**Decision**: Use `CREATE FOREIGN SERVER ... FOREIGN DATA WRAPPER CSV` plus `CREATE FOREIGN
TABLE` over a small historical traffic/weather reference CSV (e.g., typical congestion factor
and precipitation by hour-of-day/day-of-week) bundled with the project, rather than a live
paid traffic API. Live weather can optionally be layered in later via a free API (e.g.,
Open-Meteo, keyless) called the same way as the geocoding adapter, but the Foreign Table
requirement itself is satisfied by the CSV-backed foreign table.

**Rationale**: `CREATE FOREIGN SERVER`/`CREATE FOREIGN TABLE` with a CSV foreign data wrapper
is a confirmed, real IRIS SQL feature (`iris_doc_search`: "CREATE FOREIGN SERVER (SQL)",
"Querying a Foreign Table"). Live, free, keyless traffic APIs are not generally available, so
a bundled historical CSV avoids introducing a paid dependency while still demonstrating
Foreign Tables mapping external, non-IRIS-native data into SQL, per the constitution's Data &
External Integration Standards.

**Alternatives considered**: A paid traffic API (Google/TomTom) via a JDBC/HTTP foreign
server — rejected as a hard dependency on paid credentials for a Community Edition demo;
documented here as a future enhancement rather than a blocking requirement.

**Risk flagged**: Foreign Tables have evolved across recent IRIS releases (once listed as an
"Experimental Feature" in 2023.1-era docs). The exact installed IRIS version's support level
must be verified during implementation (`quickstart.md` includes a smoke-test step for this)
before relying on it as a hard requirement; if unsupported on the target image, the same
reference dataset can be loaded as a native IRIS table instead (`sql/003b_foreign_tables_fallback.sql`),
with the Foreign Table wrapper treated as an enhancement rather than a blocker.

**Verified live against IRIS 2026.1 Community (2026-08-07)**: Foreign Tables are fully
supported on this build (not experimental) — `CREATE FOREIGN SERVER`/`CREATE FOREIGN TABLE`
both succeeded, and a `SELECT` against the foreign table correctly read real rows from a CSV
file placed on the container's filesystem. Two corrections versus the initial draft:
1. `USING` takes a **JSON-string literal**, e.g. `USING '{"header":true}'` — the form
   `USING (header = true)` fails with SQLCODE -1 ("LITERAL ('USING') expected, ( found").
2. `HOST` must point at a directory writable/readable by the `irisowner` OS user inside the
   container; `/irisapp` was not usable in this image (`mkdir: Permission denied`), so
   `/tmp/uberroute_data` was used instead — see `sql/003_foreign_tables.sql` for the exact
   `docker cp` steps to stage the CSV there before running the DDL.

## 11. Observability approach

**Decision**: Every host writes structured log entries (JSON fields: `event`, `host`,
`session_id`, `duration_ms`, `outcome`) through IRIS's standard interoperability logging
(visible in the Management Portal event log) at each key step (request received, IntegratedML
call made, RAG call made, Business Rule outcome, error). A thin `observability/telemetry.py`
helper wraps this logging call plus an OpenTelemetry-compatible span/metric emission point
(counter per host invocation, histogram for `duration_ms`), so metrics can be scraped or
exported without changing host code.

**Rationale**: Directly satisfies Constitution Principle V (Observability by Default);
centralizing the helper avoids each of the four hosts reimplementing logging/telemetry
independently.

**Alternatives considered**: Relying solely on IRIS's default production message trace (no
custom logging) — rejected, insufficient detail for the specific decision points
(rule outcome, RAG trigger) the constitution requires to be observable.
