<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo-light.svg" alt="Smart Depart" width="360">
</picture>

# Smart Depart — Uber Route & Coffee Recommendation Agent

**Smart Depart** is a trip-timing agent built on InterSystems IRIS that turns "when should I
leave?" into a real decision instead of a guess. Give it an origin, a destination, and the
time you actually need to **arrive**, and it works backwards: an in-database IntegratedML
model prices three departure options — leave now, or leave 30/60 minutes early — and a
hybrid (vector + keyword) RAG search finds a real, nearby café, bakery, or restaurant to wait
at for the earlier options, fetched live from OpenStreetMap for wherever you actually are.

*[Artigo em português: cobertura completa dos requisitos](ARTIGO.md) · [Full decision log & every bug found along the way](specs/001-uber-route-coffee-agent/research.md)*

## 🧭 Project principles

The system does not try to answer *"what's the cheapest possible fare, ever?"* — it answers
one concrete question, three ways at once:

> *"If I need to be at Rua Augusta by 18:30, what does it cost to leave now — versus leaving
> 30 or 60 minutes earlier and waiting somewhere nearby instead?"*

What it does:
- Prices three comparable departure options for one trip, each independently estimated.
- Suggests a real, nearby place to wait for the two earlier-departure options — not a
  hardcoded list, fetched live for the rider's actual location.
- Explains *why* a place was suggested over the alternatives (distance, rating, which
  retrieval signal — semantic or keyword — won).
- Degrades honestly: an address that can't be resolved, or that resolves to somewhere
  implausibly far away, gets a clear error — never a fabricated recommendation.

What it doesn't do:
- Place an actual ride request, take payment, or talk to a live driver-dispatch system.
- Predict real-world Uber surge pricing — the fare model is trained on a small synthetic
  dataset for demonstration, not live market data (see [Known limitations](#known-limitations--honest-notes)).
- Rank or recommend *where to go* — only *when to leave* and *where to wait*.

## 🏗️ Architecture

```
                    Rider (browser)
                         │
                  GET /uberapp/  or
           POST /uberapp/api/uber-route/recommend
                         │
                         ▼
        production/wsgi/app.py  (IRIS-native WSGI Web App)
                         │
                         ▼
              BsUberRouteService  (Business Service)
                validates payload, adapterless
                         │  send_request_sync
                         ▼
              BpRouteOrchestrator  (Business Process)
        geocodes, builds the 3 departure options, persists
             ┌───────────┴───────────┐
             │ ×3 (one per option)   │ ×2 (earlier-departure options only)
             ▼                       ▼
   BoIntegratedMlPredictor   BoHybridRagEngine
   (Business Operation)      (Business Operation)
   FarePredictor via SQL     live Overpass fetch + hybrid
                              vector/keyword search
             │                       │
             └───────────┬───────────┘
                         ▼
         InterSystems IRIS Community Edition
     relational tables · JSON documents · VECTOR store · Foreign Table
```

Every host is pure Python (`intersystems-pyprod`) — no ObjectScript-first code — per the
project [constitution](.specify/memory/constitution.md)'s PyProd-First Interoperability
principle. Two adapter modules (`geocoding_adapter.py`, `overpass_adapter.py`) isolate the
only two external, free/keyless API calls the system makes.

## 🧩 Implementations in the IRIS ecosystem

| Capability | Effective use |
|---|---|
| **PyProd hosts** | Four `intersystems-pyprod` hosts — one Business Service, one Business Process, two Business Operations — covering the required host mix, all pure Python |
| **Adapters** | `geocoding_adapter.py` (Nominatim) and `overpass_adapter.py` (Overpass), each isolated so the external dependency can be mocked, rate-limited, or swapped |
| **Business Rules** | `business_rules.py` — a standalone, independently-tested function, deliberately swappable for a formal `Ens.Rule.RuleSet` later |
| **Native WSGI** | `production/wsgi/app.py` served directly by IRIS as a Web Application (`/uberapp`) — no gunicorn/uwsgi, no second process |
| **IntegratedML** | `FarePredictor`, defined/queried through `CREATE MODEL`/`TRAIN MODEL`/`PREDICT()` SQL — trained outside IRIS (no AutoML provider on this image) and imported via PMML, still 100% IntegratedML at query time |
| **Vector Search** | `UberRoute.WaitingPlace.Embedding` as native `VECTOR(DOUBLE, 384)`, `VECTOR_COSINE` + `AS HNSW(Distance='Cosine')` |
| **Multimodel data** | Relational tables, a JSON document log (`RequestLog.Payload`), and the Vector Store all in one IRIS namespace — no external vector DB |
| **Foreign Table** | `UberRoute.TrafficWeatherReference`, mapped from an external CSV via `CREATE FOREIGN SERVER`/`FOREIGN TABLE` |
| **Observability** | Every host logs structured events (`log_event`/`timed_event`) queryable via SQL (`Ens_Util.Log`) — the actual tool used to diagnose every live bug found in this project |

## 🔎 RAG + Hybrid Search

Waiting-place candidates have names, categories, and addresses that need exact matching —
but a rider's own words ("somewhere quiet with wifi") need semantic matching too.

| Mechanism | Role |
|---|---|
| **Vector search** | `VECTOR_COSINE` over `sentence-transformers/all-MiniLM-L6-v2` embeddings (384-dim), run locally via ONNX Runtime — no external embedding API |
| **Keyword search** | IRIS iFind (`%FIND search_index(...)`), for exact name/category/address matches vector similarity alone can miss |
| **Hybrid ranking** | `0.6 × vector_score + 0.4 × keyword_score`, filtered to candidates within ~1 km of the rider's origin |
| **"Generation"** | The top-ranked candidate's fields are returned directly, plus a short templated rationale explaining why it won — deterministic and auditable, not a free-form LLM call (see note below) |

> **Transparency note**: this project's RAG "generation" step is a structured explanation
> assembled from retrieval evidence, not a call to a generative LLM — a deliberate choice to
> avoid a paid external API-key dependency for a Community Edition demo. The response is
> still genuinely **fundamented in retrieved data**, which is the core of what RAG means
> here; the ARTIGO.md linked above discusses this trade-off directly.

Full pipeline breakdown — ingestion, chunking strategy (256–512 "token" sentence chunks, 50
overlap, address/category header reattached to every chunk), and embedding-model rationale —
is in [ARTIGO.md](ARTIGO.md#tópico-2--rag-pesquisa-híbrida-com-dados-reais) and
[research.md §3–5, §22](specs/001-uber-route-coffee-agent/research.md).

## 🏛️ Public data sources

| Source | Used for | Access |
|---|---|---|
| **Nominatim** (OpenStreetMap) | Geocoding free-text addresses to coordinates | Free, keyless REST |
| **Overpass API** (OpenStreetMap) | Live nearby cafés/bakeries/restaurants/coworking spaces around any coordinate | Free, keyless Overpass QL |

Both were chosen deliberately over paid alternatives (e.g. Google Maps/Places) — this project
runs entirely on free, keyless data sources by design (see the DataRobot vs. PMML decision
and the Google Places vs. Overpass decision in research.md §16 and §22).

## 🧪 Reproducible demonstration

Once the production is running and the WSGI app is registered (see below), open the frontend:

```
http://<host>:<mapped-52773-port>/uberapp/
```

It prompts for HTTP Basic Auth (any valid IRIS account, e.g. `SuperUser`), then renders three
option cards after a trip request. No screenshots are checked into this repository — the
frontend is a single self-contained HTML page
([`production/wsgi/static/index.html`](production/wsgi/static/index.html)) you can open
directly to see exactly what it renders.

Or call the API directly:

```bash
curl -u SuperUser:<password> -X POST http://<host>:<port>/uberapp/api/uber-route/recommend \
  -H "Content-Type: application/json" \
  -d '{"origin":"Av. Paulista, 1000, Sao Paulo","destination":"Rua Augusta, 500, Sao Paulo","target_time":"18:30"}'
```

```
{
  "trip_request_id": 0,
  "options": [
    { "label": "ideal",         "wait_minutes": 0,  "departure_time": "18:04", "arrival_time": "18:30", "estimated_fare": 27.11, "waiting_place": null },
    { "label": "30min_earlier", "wait_minutes": 30, "departure_time": "17:34", "arrival_time": "18:03", "estimated_fare": 27.10,
      "waiting_place": { "name": "Padaria Bella Vista", "distance_km": 0.93, "rationale": "0.9 km away; strongest signal: semantic match; rated 4.4; ranked above 2 other nearby option(s)" } },
    { "label": "60min_earlier", "wait_minutes": 60, "departure_time": "17:04", "arrival_time": "17:33", "estimated_fare": 27.09, "waiting_place": { "...": "..." } }
  ]
}
```

## 🚀 Running it

### Local tests (no live IRIS needed)

```bash
pip install -r production/requirements.txt
pytest tests/
```

40 unit/integration/contract tests, all IRIS/`intersystems-pyprod` calls mocked.

### Full live deployment

There is no `docker compose up` for this project yet — deployment against a live IRIS
Community Edition container is a sequence of documented steps (schema, PMML model import,
production load, WSGI app registration), because several of them only became correct after
live debugging against real platform bugs (see [Known limitations](#known-limitations--honest-notes)).
The complete, reproducible sequence is in
**[quickstart.md](specs/001-uber-route-coffee-agent/quickstart.md)**.

## 🛠️ Essential troubleshooting

| Symptom | Likely cause / action |
|---|---|
| `500` with `TypeError: Object of type Column is not JSON serializable` | A `pyprod` message field was never explicitly set in its constructor — always pass every field, even ones with defaults |
| A request hangs, `Ens_Util.Log` shows `DeadJobAlert` for a worker | Check `/usr/irissys/mgr/messages.log` for `caught signal 11` before assuming it's slow — could be a real segfault (this happened with `sentence-transformers`'s PyTorch backend; fixed via ONNX) |
| `TRAIN MODEL` fails or segfaults | This Community Edition image has no AutoML provider — train outside IRIS and import via PMML (`models/train_fare_predictor.py`) instead |
| `ErrProductionNotShutdownCleanly` on `StartProduction` | Call `##class(Ens.Director).RecoverProduction()` once before retrying, don't just loop Stop/Start |
| Frontend gives a bare `403`, no login prompt | The Web Application's `AutheEnabled` includes Unauthenticated — set it to Password (`32`) only, so the browser gets the `401` challenge it needs to show its login dialog |
| A message-schema change doesn't seem to take effect | Business Process/Operation edits need a production restart; a *message field* change also needs the `intersystems_pyprod` CLI re-run against `schemas.py` and every host that imports it |

Full root-cause writeups for every one of these (and more) are in
[research.md](specs/001-uber-route-coffee-agent/research.md) — 22 numbered sections, each a
real bug found live, not a hypothetical.

## 📁 Structure

```
production/
├── production.py         # Declarative Production definition
├── hosts/                 # BsUberRouteService, BpRouteOrchestrator, BoIntegratedMlPredictor,
│                           # BoHybridRagEngine, business_rules.py
├── messages/schemas.py    # Message shapes passed between hosts
├── adapters/               # geocoding_adapter.py, overpass_adapter.py
├── wsgi/                   # app.py (API + frontend routing), static/index.html
└── observability/telemetry.py

deploy/UberRouteSetup.cls  # Registers the /uberapp WSGI Web Application
models/                     # Offline PMML training script + exported model
sql/                        # Versioned DDL: core tables, vector index, foreign table, IntegratedML
ingestion/                  # Waiting-place seed data chunking + embedding pipeline
tests/                      # contract/ · integration/ · unit/
specs/001-uber-route-coffee-agent/  # Full SDD: spec, plan, data-model, contracts, research, quickstart
```

## 📚 Documentation

- **[ARTIGO.md](ARTIGO.md)** — requirements-coverage write-up (PyProd + RAG topics, every
  bonus item mapped to code) — *em português*
- **[spec.md](specs/001-uber-route-coffee-agent/spec.md)** — user stories, functional
  requirements, success criteria
- **[plan.md](specs/001-uber-route-coffee-agent/plan.md)** — technical context, architecture
  decisions, constitution compliance
- **[data-model.md](specs/001-uber-route-coffee-agent/data-model.md)** — every entity, field,
  and known gap between the original design and what's actually persisted
- **[contracts/bs_uber_route_service.md](specs/001-uber-route-coffee-agent/contracts/bs_uber_route_service.md)** —
  the HTTP API contract
- **[research.md](specs/001-uber-route-coffee-agent/research.md)** — 22 sections of decisions
  and real bugs found live, in the order they were discovered
- **[quickstart.md](specs/001-uber-route-coffee-agent/quickstart.md)** — full deployment
  walkthrough
- **[constitution.md](.specify/memory/constitution.md)** — the project's non-negotiable
  technology principles

## ✅ Requirements coverage

Full mapping (every bonus item, linked to the exact file that implements it) is in
[ARTIGO.md](ARTIGO.md). Summary:

| Topic | Requirement | Status |
|---|---|---|
| **PyProd** | ≥3 hosts (BS/BP/BO) | ✅ 4 hosts, all three types |
| | Adapter in a host | ✅ 2 adapters (geocoding, Overpass) |
| | Business Rules | ✅ `business_rules.py` |
| | WSGI protocol | ✅ native IRIS WSGI Web App |
| | Monitoring/telemetry | ✅ `observability/telemetry.py` + `Ens_Util.Log` |
| | IntegratedML | ✅ `FarePredictor` (PMML import) |
| **RAG** | Foreign Table | ✅ `TrafficWeatherReference` |
| | Multimodel data | ✅ relational + JSON + vector, one namespace |
| | Hybrid search | ✅ `VECTOR_COSINE` + iFind, weighted |
| | Public API access | ✅ Nominatim + Overpass |
| | Chunking/embedding rationale | ✅ documented, research.md §3–4 |
| | Pipeline clarity | ✅ ingestion → chunking → indexing → retrieval → response |

## 🔍 Known limitations & honest notes

- **`TRAIN MODEL`/AutoML doesn't work on this Community Edition image** — worked around by
  training `FarePredictor` outside IRIS and importing it as PMML (research.md §16). Fare
  values are demonstrative, not real-world Uber pricing.
- **`sentence-transformers`'s default PyTorch backend segfaults** IRIS's embedded-Python
  worker process — fixed by loading it with `backend="onnx"` (research.md §22).
- **IRIS 2026.1 Build 234U** truncates new class names at the first underscore and its
  worker jobs crash unpredictably — this project ships against **IRIS 2025.3**, which
  doesn't exhibit either issue (research.md §12–13).
- The `WaitingPlaceSuggestion` SQL table is defined but currently unused — the same data is
  returned inline and logged as JSON instead (data-model.md).

## ⚖️ License

[MIT](LICENSE) — code is free to use, copy, and modify. Independent demonstration project;
not affiliated with Uber, and it does not place real ride requests or process payment.

## 👤 Author

André Friedrich
