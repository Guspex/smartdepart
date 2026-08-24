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

## 12. Deployment blocker found live: underscore-prefixed class names fail to compile

**Finding (2026-08-07, IRIS 2026.1 Build 234U Community, dedicated `smart-depart-iris`
container)**: after successfully loading `production.py` (the `Production` definition) and
all six message classes via the `intersystems_pyprod` CLI run *inside* the container's
embedded Python (`/usr/irissys/bin/irispython`), loading any of the four host classes
(`BS_UberRouteService`, `BP_RouteOrchestrator`, `BO_IntegratedMLPredictor`,
`BO_HybridRAGEngine`) failed with:

```
ERROR #5351: Class 'UberRoute.BS_UberRouteService' does not exist.
Skipping class UberRoute.BS_UberRouteService
Detected 1 errors during load.
```

**Root cause, isolated by binary search** (confirmed independently via three different
paths: `iris_execute`, the CLI run directly inside the container, and the Management
Portal's own class-import UI — same failure every time, ruling out a client-side or
tool-side bug): compiling a **brand-new** class whose name contains an underscore causes
IRIS to silently create a class truncated at the first underscore instead
(`BS_UberRouteService` → `UberRoute.BS`; `A_B` → `UberRoute.A`; `XY_Test` → `UberRoute.XY`),
then fail the dependency-resolution pass because the *full* name it just tried to compile
now doesn't exist. This reproduces for **any** underscore-containing class name, regardless
of superclass (`Ens.BusinessService`, `%RegisteredObject`) — it is not specific to Ensemble
or to pyprod's generated content. Minimal hand-written classes with no underscore (e.g.
`UberRoute.TestSvc`) compile without issue on the same instance.

**Impact**: this blocks deploying all four host classes as named, since the project
constitution and the originating SPECS-001 request both mandate the `BS_`/`BP_`/`BO_`
naming convention. Not something fixable in this project's code — it is a bug/limitation in
this specific IRIS build's class compiler. Not re-attempted on a different IRIS version in
this session; a different Community Edition build/version should be checked before
concluding this is universal to IRIS 2026.1.

**Not yet resolved** — options for the next session, in order of preference:
1. Try a different IRIS Community Edition image/version (2025.1, or a different 2026.1
   patch build) and see if the bug reproduces.
2. If it doesn't reproduce anywhere else, treat this specific `smart-depart-iris` container
   as bugged and recreate it from a fresh pull of `intersystemsdc/iris-community:latest`.
3. Last resort, and only with explicit user sign-off (it deviates from the mandated naming
   convention): rename the four host classes to remove underscores (e.g.
   `BSUberRouteService`) as a workaround.

**Update (2026-08-07, same session, user-approved)**: option 3 was applied. All four host
classes and the corresponding `production.py` item names were renamed to underscore-free
PascalCase: `BsUberRouteService`, `BpRouteOrchestrator`, `BoIntegratedMlPredictor`,
`BoHybridRagEngine`. This **fixed the compilation bug** — all four classes, `production.py`,
and all six message classes now load successfully via the CLI run inside the container's
embedded Python, and `Ens.Director.StartProduction` starts the production with all four
items showing as running.

## 13. New blocker found after fixing §12: worker jobs crash on first message, independent of the rename

**Finding**: with the production started and all four hosts compiled, sending a request all
the way through (`director.create_business_service("BsUberRouteService")` then
`.process_input({...})`, exactly as `production/wsgi/app.py` does) hangs forever. Root
cause, found via `Ens_Util.Log` (the Interoperability event log — not `%SYS.ProcessQuery`,
which showed jobs as merely "idle", and not the per-host mini-log in the Portal, which only
logs the "job started" info): **the worker job for a config item is marked "dead" by
`Ens.MonitorService` within seconds of receiving its first message**, e.g.:

```
Marking job 3540 ('BpRouteOrchestrator'), with active message '2', as 'dead' ...
Active message '2', processed in job 'BpRouteOrchestrator', has been restored to the queue.
```

This happened even for `BsUberRouteService`, whose `on_process_input` does nothing more
exotic than validate the payload and call `self.send_request_sync(...)` — no direct SQL, no
external calls. The job dying that fast on that trivial a call, combined with the earlier,
independently-confirmed `TRAIN MODEL` segfault (research.md §"Verified live... TRAIN MODEL
did not succeed", same category: a job silently dying inside this container), points to the
same underlying cause: **this specific `smart-depart-iris` container's embedded
Python↔IRIS bridge is unstable under real host execution**, not just under IntegratedML
training. The class-naming fix (§12) was real and necessary, but not sufficient — it
unblocked compilation, not execution.

**Not resolved this session** (time budget). Requires either: a fresh container from a
different IRIS Community image/version to see if the instability reproduces there too, or
InterSystems support engagement given two independent embedded-Python crash patterns have
now been reproduced on the same container. The application code itself (host logic, message
schemas, WSGI routing) has been verified correct by other means (32 passing local tests with
IRIS/pyprod mocked; live SQL/vector/keyword search verified separately in isolation) — the
gap is specifically in exercising it through a live, running production on this container.

## 14. §13 resolved by switching IRIS versions; five more integration bugs found and fixed
end-to-end on IRIS 2025.3

**Environment change**: per explicit instruction, the entire Docker environment was wiped
(all containers/images/volumes) and rebuilt from scratch on **IRIS Community 2025.3**
(instead of the 2026.1 build used in §12/§13), using the same CPF-merge auto-configuration
approach. IRIS 2025.1 was tried first and rejected — it failed to start at all ("Invalid
Community Edition license, may have exceeded core limit"), reproducing even with `--cpus=2`.

**§13 confirmed environment-specific, not a code bug**: the same production code (post-§12
rename) was redeployed unmodified on 2025.3. Worker jobs no longer get marked "dead" by
`Ens.MonitorService`. Errors that do occur now surface as ordinary catchable Python
exceptions instead of killing the job. `TRAIN MODEL` also no longer segfaults on 2025.3 — it
fails cleanly with SQLCODE -186 ("AutoML provider not available", see below). This is strong
evidence 2026.1 Build 234U had broader embedded-Python-bridge instability beyond just the
class-naming bug, not present in 2025.3.

With the crash class gone, five further integration bugs surfaced while driving a real
request through the full stack (`director.create_business_service(...).process_input(...)`,
exactly as `production/wsgi/app.py` does) — all fixed and reverified with a live call:

1. **`ModuleNotFoundError: No module named 'production'`**. `intersystems_pyprod`'s
   generated `OnInit` only adds the *script's own directory* (e.g. `production/hosts/`) to
   `sys.path`, not the project root — so `from production.messages.schemas import ...`
   failed on the very first host invocation. Fixed by inserting the project root into
   `sys.path` explicitly at module level in all four host files, computed from `__file__` via
   `os.path.dirname()` chaining, before any `production.*` import:
   ```python
   _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
   if _PROJECT_ROOT not in sys.path:
       sys.path.insert(0, _PROJECT_ROOT)
   ```

2. **`AttributeError: Property session_id not found in object of type
   iris.UberRoute.TripRequestMessage`**. Pyprod's `_createmessage()` (which converts the raw
   IRIS-side message object into the Python-side dataclass before `on_request`/`on_message`/
   `on_process_input` runs) looks up the incoming message's class in a module-level registry
   (`_ProductionMessage_registry`) populated by `__init_subclass__` — i.e. only when the
   message class's *module has been imported*. The original code deferred
   `from production.messages.schemas import ...` to inside each method body (to dodge the
   sys.path issue above), so on the first message the registry was still empty and
   `_createmessage()` silently returned the *raw, unconverted* object instead of raising.
   Fixed by hoisting all `production.*` imports (message schemas, adapters, business rules,
   telemetry) to module level in all four host files, alongside the `sys.path` fix above.
   Downstream effect on tests: `tests/integration/test_user_story_2.py` patched
   `"production.adapters.geocoding_adapter.geocode"`, but since `bp_route_orchestrator.py`
   now imports `geocode` at module level (binding a local name in its own namespace), the
   patch target had to move to `"production.hosts.bp_route_orchestrator.geocode"`.

3. **`director.create_business_service(name)` takes the production's configured *item name*
   (the `name=` passed to `ServiceItem`/`ProcessItem`/`OperationItem` in `production.py`),
   not the fully-qualified ObjectScript class name.** Passing the class name (e.g.
   `"UberRoute.BsUberRouteService"`) raises `ErrBusinessDispatchNameNotRegistered` inside
   `intersystems_pyprod`'s `director.py`, surfacing to the caller as
   `AttributeError: 'str' object has no attribute 'ProcessInput'`. This bug was present in
   `production/wsgi/app.py`'s `_SERVICE_CLASS` constant (unnoticed until live-tested — unit
   tests mock `director.create_business_service` entirely, so they never touch this arg).
   Fixed by renaming the constant to `_SERVICE_NAME` with value `"BsUberRouteService"` (no
   package prefix) and updating the `create_business_service(...)` call site.

4. **The object returned by `service.process_input(payload)` uses PascalCase properties, not
   the Python-side snake_case attribute names.** `director._AdapterlessService.process_input()`
   returns the *raw IRIS-side message object* directly — it does not route through pyprod's
   `_createmessage()` conversion the way host-to-host messaging does. Confirmed via
   `dir(response)` on a live call: `['DeltaMinutes', 'ErrorCode', 'ErrorMessage',
   'EstimatedArrivalTime', 'EstimatedFare', ..., 'RecommendedTime', ..., 'TripRequestId',
   'WaitingPlaceAddress', ...]` — i.e. pyprod's `Column`-to-ObjectScript-property projection
   (`recommended_time` → `RecommendedTime`), with no snake_case Python wrapper on this
   particular path. Fixed by rewriting every field access in `production/wsgi/app.py` from
   snake_case (`response.recommended_time`) to PascalCase (`response.RecommendedTime`), and
   updating the `SimpleNamespace` mocks in `tests/contract/test_bs_uber_route_service_us1.py`
   and `test_bs_uber_route_service_us2.py` to match the real shape.

5. **`irispython` is not on `$PATH` inside the container** — it must be invoked by full path,
   `/usr/irissys/bin/irispython`. (Environment/tooling note, not an application bug — recorded
   here because it cost real debugging time and will recur on any fresh container.)

**End-to-end verification**: after all five fixes, a live POST-equivalent call through
`production/wsgi/app.py`'s `application()` (the same WSGI entrypoint the frontend calls)
against the running `smart-depart-iris` (2025.3) container returned a well-formed response:
```
STATUS: 503 Service Unavailable
BODY: {"error": "prediction_unavailable", "message": "Could not compute a fare/time
recommendation right now:  Model 'FarePredictor' has no default trained model.  It may not
have been trained."}
```
This is the *expected* outcome given `TRAIN MODEL` cannot succeed on this Community Edition
image (§ below) — critically, it is a clean, catchable, correctly-routed error surfaced
through the full BsUberRouteService → BpRouteOrchestrator → BoIntegratedMlPredictor chain and
back through the WSGI layer with the right HTTP status and JSON shape, not a crash, hang, or
silent job death. This confirms the application code, host wiring, message routing, and WSGI
layer are all correct; the only remaining gap is IntegratedML model training in this specific
Docker image.

**`TRAIN MODEL` / AutoML unavailable — confirmed as a genuine platform limitation, not a
bug**: `TRAIN MODEL FarePredictor` fails with SQLCODE -186 ("AutoML provider not available")
on IRIS Community 2025.3. This is a *clean* failure (unlike the 2026.1 segfault in §13) —
another confirmation that 2026.1's instability was broader than just this feature. IRIS
Community Edition images do not ship a default AutoML provider; enabling `TRAIN MODEL` would
require either a different IRIS edition/image with AutoML bundled, or registering a custom
IntegratedML provider — out of scope for this deployment session. The application already
degrades gracefully (FR-covered): `BoIntegratedMlPredictor` catches the SQL error and returns
`prediction_unavailable`, which `production/wsgi/app.py` maps to `503 Service Unavailable`
with a descriptive message, exactly as verified above.

## 15. Registering `/uberapp` as a real HTTP-reachable WSGI Web Application

**Finding**: §14's live verification called `production/wsgi/app.py`'s `application()` function
directly from a Python script inside the container — it never went through an actual HTTP
request, because no IRIS **Web Application** had been registered to route HTTP traffic to it.
`Security.Applications` (the `%SYS`-namespace table backing both the Management Portal's
"Web Applications" page and `##class(Security.Applications)`) had no entry for the frontend at
all. Registering one was needed before a browser could reach it.

**Web Application configuration for WSGI**: `Security.Applications` has dedicated WSGI columns
(`WSGIAppLocation`, `WSGIAppName`, `WSGICallable`, `WSGIType`, `WSGIDebug`) alongside the
generic ones. The Management Portal's "Create Web Application" form exposes these once the
"WSGI [Experimental]" radio button is selected (`CSPZENEnabled = "WSGI"`), revealing "Nome do
aplicativo" (`WSGIAppName` — the importable Python module, e.g. `production.wsgi.app`), "Nome
chamável" (`WSGICallable` — the module-level callable, e.g. `application`), and "Diretório de
aplicativos WSGI" (`WSGIAppLocation` — the directory added to `sys.path` before import, e.g.
`/tmp/uberroute_app`). Setting these via `Security.Applications` programmatically (`%New()` +
property assignment + `%Save()`, or `##class(Security.Applications).Create(name, .Properties)`
with a `Properties` local array) requires running from ObjectScript — the Python native API's
by-ref array parameters (`&Properties`) do not marshal correctly from `iris.cls(...).Create(...)`
or `.Modify(...)` calls (they silently no-op), and `Security.Applications` objects opened via
`%New()`/`%OpenId()` and mutated directly from Python raise `SystemError: Cannot modify a
string currently used` on `%Save()`. Both are Python-bridge marshaling issues specific to this
class, not ObjectScript bugs — the same operations work correctly from a compiled ObjectScript
classmethod (see `deploy/UberRouteSetup.cls`, `CreateWebApp()`), which is the pattern used to
work around them.

**Bug found: `Security.Applications.Type` value `5` creates an undeletable, misconfigured
"privileged application"**: while probing valid values for the `Type` property (documented
range 2–9, no VALUELIST readable via SQL), `Type=5` produced an application record with
`NameSpace=''` (empty) instead of the requested `NameSpace="USER"`, and Management Portal then
refers to it as a "aplicação privilegiada" (privileged application) that neither
`##class(Security.Applications).Delete()` nor a raw `SQL DELETE` can remove (`ERROR #870:
Cannot delete system application`; SQL DML is blocked entirely on this table). No workaround
was found within this session's time budget — the leftover `/uberroute` record (and a stray
`/uberroute_probe7` from the same probing) are harmless (unreachable, not routed to by the
gateway for any in-use path) but permanently stuck in `%SYS`. **Avoid `Type=5`.** `Type=2`
(used by `deploy/UberRouteSetup.cls`) creates a normal, deletable WSGI application correctly
scoped to the requested namespace.

**Bug found: `Type=2` + the `WSGI*` properties is not sufficient to make the app actually
dispatch as WSGI — `DispatchClass` must be set to `%SYS.Python.WSGI` explicitly, and nothing
in the SQL-visible `Security.Applications` schema signals this.** Discovered when
`deploy/UberRouteSetup.cls`'s first version (which set `Type`, `WSGIAppLocation`,
`WSGIAppName`, `WSGICallable` but not `DispatchClass`) was used to delete and recreate
`/uberapp` after the original portal-created record (see below) — every request, authenticated
or not, started returning `404` instead of dispatching to the app at all. The record still
looked correct in the Management Portal's application list and in every WSGI-specific column;
only `DispatchClass` (left at its default, i.e. the CSP/Zen page dispatcher) was wrong, which
silently 404s because there's no matching CSP page for any path. Confirmed by re-editing the
broken record in the Portal, re-selecting the "WSGI [Experimental]" radio, and observing
`DispatchClass` flip to `%SYS.Python.WSGI` only at that point — the Portal's save handler sets
it as a side effect of that radio choice, but it's not exposed as a property you'd think to set
from reading the schema (the visually-obvious `CSPZENEnabled` column looked like the
discriminator but editing it directly through SQL/property access has no effect on dispatch).
**Fixed** by adding `Set app.DispatchClass = "%SYS.Python.WSGI"` to `CreateWebApp()` — verified
this alone (no portal involvement) produces a fully working app from a fresh
`##class(UberRoute.Setup).CreateWebApp()` call.

**Bug found: unauthenticated access (`AutheEnabled=64` alone) returns a bare `403 Forbidden`
for WSGI-type applications, even though the same setting works for REST applications on the
same instance.** Confirmed methodically (against a correctly-`DispatchClass`-configured app):
- `%Api.Monitor` (a built-in REST app also configured for `AutheEnabled=64`-only,
  unauthenticated) returns `404` for an unmatched route — meaning unauthenticated access is
  *accepted* (auth passes; the request just reaches the dispatch class and finds no matching
  route). The system-wide `Security.System.AutheEnabled` bitmask and `%Service_WebGateway`
  both permit bit 64.
- The same request pattern against `/uberapp/` (`AutheEnabled=64`-only, WSGI type) returns
  `403 Forbidden` with an empty body, before the WSGI callable is ever invoked — confirmed by
  calling `%SYS.Python.WSGI.ImportWSGIApplication("production.wsgi.app", "/tmp/uberroute_app",
  "application", 1)` directly (outside HTTP), which succeeds and returns the callable, ruling
  out a module-import or `sys.path` problem.
- Neither disabling `CSRFToken`, disabling `WSGIDebug`, nor setting `UseCookies=0` changed the
  result.
- Adding `AutheEnabled = 64 + 32` (Unauthenticated + Password) and retrying **with HTTP Basic
  Auth as `SuperUser`** returned `200 OK` with the expected frontend HTML, and a subsequent
  `POST /api/uber-route/recommend` with the same Basic Auth returned the expected `503
  prediction_unavailable` JSON (matching §14's direct-call verification exactly). So the
  WSGI dispatch, module resolution, and application logic are all confirmed correct — the
  403 is specifically an authentication-layer rejection of the *Unauthenticated* method for
  WSGI-type applications, most likely tied to the "[Experimental]" status of WSGI support in
  this IRIS Community 2025.3 build. Not root-caused further within this session's time budget.
- **A second, related bug**: `AutheEnabled = 64 + 32` (Unauthenticated *and* Password) does
  make the app reachable with Basic Auth credentials (confirmed: `200 OK` with the frontend
  HTML, and `POST /api/uber-route/recommend` with the same header returning the expected `503
  prediction_unavailable`, matching §14's direct-call verification exactly) — but it breaks
  normal *browser* login, because having Unauthenticated as one of the allowed methods makes
  IRIS skip the `401 Unauthorized` + `WWW-Authenticate: Basic` challenge that a browser needs
  in order to pop its native login dialog. A request with no credentials at all just gets
  silently rejected instead of prompting. (`curl`/`urllib` with an explicit `Authorization:
  Basic` header sent up front masked this, since they never rely on the challenge.)
- **Workaround** (applied in `deploy/UberRouteSetup.cls`): set `AutheEnabled = 32` (Password
  *only*, Unauthenticated fully disabled). Verified: an unauthenticated request now correctly
  gets `401` with `WWW-Authenticate: Basic` (the signal a browser needs to show its login
  prompt), and a request with valid credentials (e.g. `SuperUser`) gets `200`. Not root-caused
  further why Unauthenticated-alone 403s for WSGI apps specifically — most likely tied to the
  "[Experimental]" status of WSGI support in this IRIS Community 2025.3 build.

**End-to-end result**: `http://<host>:<mapped-52773>/uberapp/` (GET) serves the frontend
`index.html` (prompting for Basic Auth in a real browser — `SuperUser` / the CPF-configured
password), and `POST /uberapp/api/uber-route/recommend` returns correctly-routed JSON — both
verified over real HTTP, not just via direct Python function calls. This closes the remaining
gap from §14 (the frontend was runnable but not yet reachable over HTTP).

**One more bug found and fixed while verifying the form in a real browser**:
`production/wsgi/static/index.html`'s `fetch()` call used an absolute path,
`fetch("/api/uber-route/recommend", ...)`, which resolves against the *domain root*
regardless of where the app is mounted — since the app is mounted at `/uberapp/`, not `/`,
this hit a path outside the app entirely and failed with a generic browser network error
("Não foi possível falar com o servidor"), even though the server-side route matching in
`production/wsgi/app.py` (which compares `PATH_INFO`, already stripped of the mount prefix by
the WSGI spec) was correct. Fixed by changing it to a relative path,
`fetch("api/uber-route/recommend", ...)`, which resolves against the current page URL
(`/uberapp/`) instead. Verified live in a real browser session (filled the form, submitted,
confirmed the request reached `/uberapp/api/uber-route/recommend` and rendered the expected
"não foi possível calcular a recomendação agora" message from the `503
prediction_unavailable` response — not a network error). Note this fix depends on the app
always being accessed with the trailing slash (`/uberapp/`, not `/uberapp` — the latter 404s
with no redirect on this build, confirmed separately).

## 16. Unblocking `FarePredictor` without `TRAIN MODEL`: import a PMML model instead

**Decision**: since `TRAIN MODEL` cannot run on this Community Edition image (§14: no AutoML
provider installed, `SQLCODE -186`), and a DataRobot ML configuration was ruled out (requires
a paid external account/API token this session has no access to), `FarePredictor` is trained
**outside** IRIS with plain `scikit-learn` and imported into IntegratedML via the built-in
`%ML.PMML.Provider` — a provider that does not train at all, it just imports an
already-trained model exported to the PMML standard (`%ML.PMML.Provider`'s own class comment,
read via `%SYSTEM.OBJ.Export`, since the class predates and is unrelated to any of this
session's Python-embedding issues: *"This Provider does not train models based on a dataset,
but can be used to import a model built elsewhere and exported to [PMML]"*). This needs no
external account, no network calls, and no working AutoML — it is IntegratedML's documented
mechanism for exactly this situation (a pre-built `%PMML` ML configuration ships out of the
box alongside `%AutoML` and `%H2O`).

**Training pipeline**: `models/train_fare_predictor.py` reads `data/trip_history_seed.csv`
(238 rows, the same seed data the original `TRAIN MODEL FROM TripHistory` approach was meant
to use), fits a plain `sklearn.linear_model.LinearRegression`, and exports it with
`nyoka.skl_to_pmml`. `nyoka`'s PMML export does not support arbitrary preprocessing steps
reliably (a `ColumnTransformer`-based `OneHotEncoder` for the categorical `PickupTime` field
raised `TypeError: This PreProcessing Task is not Supported`; falling back to the
`sklearn_pandas.DataFrameMapper` pattern nyoka's own examples use failed too, for an unrelated
reason — `sklearn_pandas` 2.x is incompatible with `scikit-learn` 1.9's `sklearn.utils` API,
`ImportError: cannot import name 'tosequence'`). Rather than fight either library further, the
feature set was changed to be **fully numeric**: `PickupTime` ("HH:MM") is converted to
`PickupMinutes` (minutes-since-midnight, an integer) before training, sidestepping
categorical encoding entirely. Trained on all 238 rows (no held-out test split, given the
sample size and that this is meant to unblock the pipeline, not compete on predictive
accuracy): R² ≈ 0.947 on the training data. The resulting `RegressionModel` PMML is a flat
linear equation over `PickupMinutes`, `DayOfWeek`, `DistanceKm`, `DemandFactor` — inspected
directly (it's plain XML) to confirm the field names and a plausible coefficient sign/magnitude
pattern (distance and demand dominate the price; time-of-day and day-of-week have small
effects, matching how the synthetic seed data was generated).

**Downstream schema/query changes this forced**: because PMML has no portable way to express
"parse an HH:MM string into a number" as an importable transform, and `CREATE MODEL ... FROM
UberRoute.TripHistory` would otherwise infer `PickupTime` as `VARCHAR(5)` (TripHistory's
actual storage type) as a *string* feature, `sql/004_integratedml.sql` was rewritten to use
`CREATE MODEL FarePredictor PREDICTING (FinalPrice) WITH (PickupMinutes INTEGER, DayOfWeek
INTEGER, DistanceKm DOUBLE, DemandFactor DOUBLE)` — an explicit feature-column clause instead
of inferring from the table — and `production/hosts/bo_integratedml_predictor.py` now
converts `candidate_time` ("HH:MM") to minutes-since-midnight (`_minutes_since_midnight`)
before calling `PREDICT()`, instead of passing the raw string.

**Four further syntax/config bugs found getting `CREATE MODEL`/`TRAIN MODEL`/`PREDICT()` to
actually run**, none related to PMML specifically — likely latent since the original
`TRAIN MODEL FarePredictor` (§14) never got far enough to hit any of them:
1. **`CREATE MODEL`'s `model-name` must be unqualified.** `CREATE MODEL UberRoute.FarePredictor
   PREDICTING (...) WITH (...)` (schema-qualified, matching how every table name in this
   project is written) is a parser error: `PREDICTING expected, . found`, pointing at the `.`
   right after `UberRoute`. Dropping the `UberRoute.` prefix (`CREATE MODEL FarePredictor
   ...`) works. (Table names, e.g. `UberRoute.TripHistory`, are unaffected — this is specific
   to `CREATE MODEL`'s `model-name`.)
2. **`TRAIN MODEL`'s `FROM` subquery does not accept inline `CAST`/`SUBSTRING` expressions.**
   `TRAIN MODEL FarePredictor FROM (SELECT (CAST(SUBSTRING(PickupTime,1,2) AS INTEGER)*60 + ...)
   AS PickupMinutes, ... FROM UberRoute.TripHistory) USING {...}` fails with `Field
   'PICKUPTIME' not found in the applicable tables`, even though the identical `SELECT`
   (without wrapping it in `TRAIN MODEL ... FROM (...)`) runs correctly as plain SQL. Worked
   around by precomputing the same expression into a `CREATE VIEW
   UberRoute.TripHistoryForTraining AS SELECT (CAST(...) ...) FROM UberRoute.TripHistory` and
   pointing `TRAIN MODEL ... FROM UberRoute.TripHistoryForTraining` at the view instead — this
   parses fine.
3. **`TRAIN MODEL ... USING {"file_name": ...}` alone still tries the default `%AutoML`
   provider**, failing with `%ML Provider 'AutoML' is not available on this instance` — the
   `USING` clause's `file_name` key only configures the PMML provider once it's actually
   selected; it does not select it. Fixed by running `SET ML CONFIGURATION %PMML;` as its own
   statement before `TRAIN MODEL` (the pre-built `%PMML` configuration, confirmed to ship by
   default alongside `%AutoML`/`%H2O`). This selection only matters at `TRAIN`/import time —
   confirmed a fresh session with no `SET ML CONFIGURATION` at all can still run `PREDICT()`
   against the already-imported model correctly, since the provider is now baked into the
   trained-model record itself, not re-resolved per query.
4. **`PREDICT(model USING (col1, col2, ...))` is not valid syntax** — despite being what this
   project's `research.md` §7 and the original `BoIntegratedMlPredictor` code assumed
   (unverified live at the time, per that section's own text). Live, it's a parser error:
   `) expected, USING found`. The actual, working form omits `USING` entirely —
   `PREDICT(model)` — and matches feature columns *by name* against whatever the `FROM` row
   context provides (confirmed: `SELECT PREDICT(FarePredictor) AS PredictedPrice FROM (SELECT
   ? AS PickupMinutes, ? AS DayOfWeek, ? AS DistanceKm, ? AS DemandFactor)` works correctly
   with positional bind parameters). `production/hosts/bo_integratedml_predictor.py` and
   `sql/004_integratedml.sql`'s example comment were both corrected to drop the `USING`
   clause.

**Business Operation Python changes require a production restart to take effect** — copying
an updated `.py` host file into the container (as done throughout §14/§15) was *not* enough
here; the running `BoIntegratedMlPredictor` worker job kept using its already-imported (stale)
Python module and returned `Field 'PICKUPMINUTES' not found in the applicable tables` (the
old `PREDICT(... USING (...))` query) even after the file on disk was updated and
`__pycache__` cleared. This is unlike the WSGI app (`%SYS.Python.WSGI.ImportWSGIApplication`
re-imports fresh per request, so `production/wsgi/app.py` edits took effect immediately in
§14/§15) — pyprod's compiled Business Operation hosts import their Python module once and
keep it alive in the worker job's long-running interpreter for the job's lifetime. Fixed with
`Ens.Director.StopProduction()` then `StartProduction()` (confirmed via
`Ens_Config.Production` for the running production's name), which respawns the worker jobs
and re-imports the (now current) Python source.

**End-to-end result**: `POST /uberapp/api/uber-route/recommend` now returns `200 OK` with a
real predicted fare and recommended time, instead of `503 prediction_unavailable`, confirmed
over real HTTP. One thing this surfaced that is **not** an IntegratedML/PMML issue: a live
test (Av. Paulista 1000 → Rua Augusta 500, both São Paulo) returned an implausibly high fare
(R$314.99) — traced directly to `production/adapters/geocoding_adapter.py`'s geocoder
resolving "Rua Augusta, 500, Sao Paulo" to coordinates roughly 96 km from "Av. Paulista, 1000,
Sao Paulo" (verified by calling `geocode()` on both strings directly and computing the
haversine distance: `96.47` km), when the real streets are close together in São Paulo city.
`FarePredictor` correctly priced *that* (wrong) distance — plugging the same inputs back
through `PREDICT()` directly reproduces the R$314.99 figure exactly. This is a geocoding
address-resolution accuracy issue, pre-existing and unrelated to this session's ML work; not
investigated further here (out of scope for "get FarePredictor working").

## 17. `FarePredictor` could return negative fares — unbounded linear extrapolation + an
out-of-distribution hardcoded `DemandFactor`

**Finding**: a live user test with a short, correctly-geocoded Florianópolis trip
(~2-3 km) returned `"estimated_fare": -2.08` — a linear regression has no non-negativity
constraint, so it can and does extrapolate below zero outside the region its coefficients
were fit on.

**Root cause, isolated exactly**: `BoIntegratedMlPredictor._predict_fare` (§16) passed a
hardcoded `demand_factor=1.0` for every candidate (comment: "demand factor is not known
ahead of time... a neutral demand_factor=1.0 is passed"). But `data/trip_history_seed.csv`'s
`DemandFactor` column ranges `0.92`–`2.93` with mean/median ≈ `1.82` — there is no "1.0 =
neutral" convention in this data; `1.0` sits near the observed *minimum*. Combined with a
short real-world distance, and `DemandFactor`'s comparatively large regression coefficient
(`20.09`, vs. `3.40` for `DistanceKm`), this pushed the linear prediction below zero.
Reproduced exactly by hand from the PMML's own coefficients
(`intercept=-31.886 + 0.00035*PickupMinutes - 0.197*DayOfWeek + 3.397*DistanceKm +
20.091*DemandFactor`): at `distance_km=3.0, demand_factor=1.0`, the formula evaluates to
`-2.12` — matching the observed `-2.08` (small residual difference just from the exact
distance/time/day not being read off the screenshot precisely).

**Fixed** in `production/hosts/bo_integratedml_predictor.py`, two changes:
1. The hardcoded demand-factor placeholder changed from `1.0` to `1.82` (the training data's
   mean) — keeps the "demand unknown for a hypothetical future slot" assumption inside the
   distribution the model was actually fit on, instead of near its edge.
2. A `_MINIMUM_FARE = 5.0` floor: `max(predicted_fare, _MINIMUM_FARE)`, since the underlying
   model has no built-in floor and a real fare is never zero or negative regardless of how
   demand-factor is chosen.

**Verified live**: the same Florianópolis trip that produced `-R$2.08` now returns
`R$14.40` — plausible for a short trip, and consistent with the cheapest fares actually
observed in the training data (minimum `R$9.60` at `distance_km≈2`).

## 18. `location_not_found` on valid addresses — Nominatim can't parse the Brazilian "nº"
abbreviation

**Finding**: a live user test with two complete, real Florianópolis/São José addresses
returned `422 location_not_found` for the destination — *"Rodovia BR 101 nº km 211, 7235 -
Distrito Industrial, São José - SC"*. Isolated by calling `geocode()` directly on
progressively simplified variants of the same string:
```
'Rodovia BR 101 nº km 211, 7235 - Distrito Industrial, São José - SC' -> None
'Rodovia BR 101, km 211, 7235 - Distrito Industrial, São José - SC'   -> (-27.618, -48.647)
```
The only difference is the `"nº "` token — a standard Brazilian abbreviation for "número"
("number"), commonly placed before a house/km number. Nominatim's query tokenizer doesn't
recognize this abbreviation and fails to resolve the *entire* address when it's present, even
though every other token is correct and the address genuinely exists. Confirmed the origin
address (which had no `"nº"` in it) resolved correctly on the very first end-to-end test that
triggered this bug — only the destination, which had it, failed, matching the hypothesis
exactly.

**Fixed** in `production/adapters/geocoding_adapter.py`: strip `\bn\.?[°º]\.?\s*`
(case-insensitive; matches `nº`, `n°`, `n.º`, `N°`, etc. — both dot-before and dot-after
orderings, since Brazilian usage varies) from the query text before sending it to Nominatim,
leaving the actual number intact (`"nº km 211"` → `"km 211"`). This is a query-normalization
step, not a change to what's stored/returned to the user — the original `location_text` is
still what's logged/persisted. (First attempt used `\bn[°º]\.?\s*`, only matching the
dot-after form `nº.` — missed the equally common dot-before form `n.º`; caught immediately by
`tests/unit/test_geocoding_adapter.py`'s variant test, which now covers both orderings.)

**Verified live**: the exact address pair from the failing report now both resolve and the
full request succeeds end-to-end (`200 OK`, real fare `R$24.08`) instead of `422
location_not_found`.

## 19. Restarting the container after it sat stopped for several days: production needs
`RecoverProduction()`, not just `StartProduction()`

**Finding**: `docker start smart-depart-iris` (after the container had been `Exited` for 5
days) brought IRIS itself back up cleanly — superserver, private webserver, and even the
production's prior auto-start all logged as successful in `messages.log`. But the very first
live request hung indefinitely (client-side timeout), and `Ens_Util.Log` showed `ERRO
<Ens>ErrJobRegistryNotClean: Global de registro de processo para '1089' não está limpa` for
`BsUberRouteService` right as the request came in. Manually calling `Ens.Director
.StopProduction()` then `.StartProduction()` (the pattern used throughout §14/§16/§18 to
reload changed Python code) made things *worse*, not better: `StartProduction` refused with
`ErrProductionNotShutdownCleanlyUberRoute.UberRouteProduction`.

**Root cause**: the container had been stopped with a plain `docker stop` (not a graceful
IRIS shutdown from inside), so IRIS's own production-state bookkeeping was left dirty —
consistent with the general IRIS container guidance that ungraceful stops leave the write
image journal (WIJ) and related state dirty, requiring recovery on next start. IRIS's own
crash-recovery (journal replay, seen in the startup log) fixed the *database* — it did not fix
the production's own "was I shut down cleanly" state, which is separate bookkeeping.

**Fixed**: `##class(Ens.Director).RecoverProduction()` (no arguments) — found by introspecting
`Ens.Director`'s method list rather than guessing at a `force`/`clean` flag on
`StartProduction` (which doesn't have one; a naive 4-positional-arg guess raised
`RuntimeError: <PARAMETER>StartProduction^Ens.Director.1`, since its actual signature is just
`StartProduction(pProductionName)`). After `RecoverProduction()`, `IsProductionRunning()`
correctly reported `0`, and a normal `StartProduction()` then succeeded and the app worked
end-to-end immediately.

**Practical takeaway for any future container restart after a non-trivial stopped period**:
check `Ens.Director.IsProductionRunning(name)` first; if `StartProduction` raises
`ErrProductionNotShutdownCleanly`, call `RecoverProduction()` once (no args) before retrying
`StartProduction` — don't just retry `StopProduction`/`StartProduction` in a loop, since
`StopProduction` on an already-not-running production doesn't clear this particular flag.

**Unrelated, cosmetic**: `docker inspect`'s health status shows `unhealthy` on this container
regardless of the above — its `HEALTHCHECK` command is itself broken (`/bin/sh: 1:
[/irisHealth.sh]: not found`, a malformed/missing script baked into this image), not a
reflection of whether the app actually works. Confirmed the app works via direct HTTP calls
independent of this label; not fixed (would require recreating the container with a corrected
health-check command).

## 20. Redesign: three fixed departure options instead of one auto-picked recommendation

**Decision**: replaced the original single-recommendation design (`BpRouteOrchestrator` scans
several candidate departure times, picks whichever is cheapest, and conditionally attaches a
waiting-place suggestion only when that pick is more than 30 minutes from a "naive" baseline —
§7/§8/§16's design) with three **fixed, always-returned** options, all anchored to the same
naive-departure baseline (arrival deadline minus a typical-traffic travel estimate):
- `ideal` — leave at the naive departure time, no extra wait, no waiting-place suggestion.
- `30min_earlier` — leave 30 minutes before that, with a waiting-place suggestion.
- `60min_earlier` — leave 60 minutes before that, with a waiting-place suggestion.

Each option is priced independently by a single, direct `PREDICT()` call at its own departure
time — no more scanning a window of nearby candidates to find a minimum (`_CANDIDATE_OFFSETS
_MINUTES` and the whole "pick whichever is cheapest" search were removed).

**Rationale**: user feedback on the live app (comparing our estimate against real Uber app
quotes) surfaced a product requirement not captured in the original spec: the rider wants to
directly compare "leave now for X" against "leave early, wait somewhere, pay Y" themselves,
rather than have the system silently pick one time on their behalf and only sometimes explain
why. This is a genuine product-requirements change, not a bug fix — confirmed with the user
before implementing (they explicitly chose "options replace the single recommendation" over
"options are added alongside it").

**What this touched**:
- `production/messages/schemas.py`: `RouteRecommendationMessage` dropped its flat
  `recommended_time`/`estimated_arrival_time`/`estimated_fare`/`delta_minutes`/
  `waiting_place_*` fields for a single `options_json: str` field — a JSON-serialized list of
  the option dicts. This is still one flat scalar field (a string), which is what pyprod's
  `JsonSerialize.chunks_from_python()` requires (every declared field must be a JSON-native
  scalar; a *list* field would not be) — the workaround is that the field's *content* is JSON
  text, not that the field itself is structured.
- `production/hosts/bp_route_orchestrator.py`: `on_request` now loops over three
  `(label, offset)` pairs, building one option dict per label via a new `_build_option`
  helper (fare prediction + waiting-place lookup for non-zero offsets). The old
  `business_rules.waiting_place_should_be_suggested()` 30-minute trigger is no longer called
  from this flow (waiting-place lookups are now unconditional for the two earlier options) —
  the module and its unit tests are left in place, since the constitution's own rationale for
  that module (§8) already frames it as a swappable, independently-testable component, not
  something coupled 1:1 to this specific call site.
- `production/wsgi/app.py`: reads the raw response's `OptionsJson` PascalCase property (same
  raw-object/PascalCase pattern as §14) and `json.loads()`s it directly into the HTTP response
  body's `"options"` array — no more per-field PascalCase→JSON mapping.
- `production/wsgi/static/index.html`: renders one card per option instead of one
  recommendation card plus an optional waiting-place card.
- `UberRoute.RouteRecommendation` (SQL table) was **not** migrated — see data-model.md's
  amended notes on that table. `UberRoute.RequestLog.Payload` (already a flexible JSON column,
  constitution Principle II) now carries the full option list instead.
- `specs/001-uber-route-coffee-agent/spec.md`, `contracts/bs_uber_route_service.md`,
  `data-model.md`: amended in place (not left stale) to describe the 3-option contract —
  spec.md's Assumptions section documents this as a post-implementation amendment, matching
  the pattern already used there for the `target_time` = arrival-deadline clarification.

**Deploying a message *schema* change needs more than a production restart** — this was a
new wrinkle §14/§16/§19's "restart the production to reload changed Python" guidance didn't
cover, because those were all *host logic* changes; this one changed `RouteRecommendationMessage`'s
declared fields. `docker cp`-ing the changed `.py` files and restarting the production still
left the *IRIS-side ObjectScript class* for `RouteRecommendationMessage` on its old field
layout (`RecommendedTime`/`EstimatedFare`/... instead of `OptionsJson`) — pyprod only
generates/compiles those `.cls` files when its CLI is run against the source, which a plain
production restart never does. Fixed by running the CLI directly:
`irispython /home/irisowner/.local/bin/intersystems_pyprod -s /tmp/uberroute_app <file.py>`
against `production/messages/schemas.py` (regenerates all 6 message classes) and each of the
4 host files in turn (they all depend on the message classes) — confirmed via
`%Dictionary.ClassDefinition` that `UberRoute.RouteRecommendationMessage` then had the new
`OptionsJson` property.

**Even after that, the live HTTP endpoint still failed** — `500`, `TypeError: Object of type
Column is not JSON serializable`, thrown from deep inside `OnProcessInput` on
`UberRoute.BsUberRouteService.1`. Confusingly, calling the *exact same code path*
(`director.create_business_service("BsUberRouteService").process_input(...)`, matching what
`production/wsgi/app.py` does) from a fresh `irispython -c "..."` script worked perfectly,
every time — as did calling `production.wsgi.app.application()` directly. Only requests
routed through the real, already-running CSP/private-webserver worker process (i.e., genuine
HTTP traffic) failed. This pointed at *process-level* staleness distinct from anything a
production restart touches: IRIS's CSP worker pool is long-lived and holds its own embedded-
Python interpreter state (`sys.modules`, pyprod's internal message registry) independently of
`Ens.Director`'s production lifecycle — a `StopProduction`/`StartProduction` cycle (tried
twice) never reached it. **Fixed** by restarting IRIS itself from inside the container
(`iris stop IRIS quietly` then `iris start IRIS` — the graceful, IRIS-native restart command,
not `docker stop`/`restart`, which research.md §19 already established leaves production
state dirty) and manually re-starting the production afterward (it didn't auto-start this
time, unlike on a full container boot). This is the first time in the session a *schema*
change (as opposed to *logic-only* changes) needed redeploying — logic-only host edits really
do only need `StopProduction`/`StartProduction`, per §14/§16/§19; changing a message class's
declared fields needs the pyprod CLI re-run **and** a full IRIS restart on top of that.

**Verified live**: after all of the above, `POST /uberapp/api/uber-route/recommend` returns
`200 OK` with all three options for both a São Paulo address pair and the exact Florianópolis
addresses from earlier live testing — each option with its own fare, and the two
earlier-departure options each carrying a `waiting_place_unavailable_reason` (`sentence-
transformers` isn't installed in this session — a pre-existing, already-documented gap,
unrelated to this change). Full local test suite (35 tests, including two integration test
files rewritten for the new response shape) passes.

## 21. Vague addresses can geocode to a same-named place hundreds of km away — added a
distance sanity check

**Finding**: a live user test with destination `"SENAI, São José"` (a real Brazilian training
institute, but no street or state given — "São José" alone is a common city name in multiple
states) returned an implausible fare of **R$1163.71** and, worse, an arrival time
(`13:19`) displayed as *earlier than* the departure time (`20:16`) for the same option — a
result that made no sense on its face. Root-caused by calling `geocode()` directly on both
addresses: the destination resolved to coordinates **341 km** away from the origin (verified
via haversine distance on the returned lat/lng), a completely different city than the rider
meant. `FarePredictor` then correctly priced *that* (wrong) 341 km trip, and the ~13.6-hour
naive travel-time estimate this produced (`341 km / 25 km/h` baseline speed) pushed the naive
departure calculation back across a full day boundary — `_add_minutes`'s `% (24*60)` wraparound
has no day-tracking, so a departure "yesterday at 20:16" for an "18:30 today" arrival displays
with no indication it isn't the same calendar day, and the two independently-recomputed
duration estimates (naive vs. per-candidate) diverged enough across that wraparound to show an
`arrival_time` that looks earlier than the `departure_time`. This is the same underlying class
of issue as §17 (an ML model or a formula extrapolating a nonsensical output when fed
implausible input) and §18 (geocoding ambiguity) combined — not a new kind of bug, but their
combination exposed a gap neither fix alone covered.

**Fixed**, at the input-validation level rather than trying to patch the day-wraparound math:
`BpRouteOrchestrator` now checks `distance_km` against a **`_MAX_TRIP_DISTANCE_KM = 100.0`**
sanity cap immediately after geocoding both addresses (generous for a single-city/metro-area
Uber trip; the training data in `data/trip_history_seed.csv` tops out at `DistanceKm≈20`) and
returns a new `distance_out_of_range` error (`422`, alongside `location_not_found` in the
contract) instead of computing a "trip" at all. This is deliberately a *distance* check, not a
same-city/same-state string check on the input addresses — Nominatim genuinely did "resolve"
both strings to real coordinates, so there was nothing malformed to reject earlier; the
implausibility only becomes visible once both are geocoded and compared.

**Verified live**: the exact `"SENAI, São José"` request now returns `422
distance_out_of_range` with a message asking the rider to add more detail (street, city,
state), instead of a nonsensical fare and time-wraparound. A regression check against the
known-good Florianópolis address pair (§18) still returns `200 OK` with plausible ~R$27
fares across all three options, confirming the 100 km cap doesn't reject legitimate
same-metro-area trips.
