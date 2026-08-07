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
