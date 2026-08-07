# Uber Route & Coffee Recommendation Agent

A PyProd (InterSystems IRIS Interoperability, pure Python) production that recommends a
trip's best departure time/fare given an **arrival deadline** (e.g. "I need to be there
by 14:00") and, when that recommendation means leaving much earlier or later than a rider
would naively expect, suggests a nearby café or coworking space to wait at.

Full spec/plan/research: [specs/001-uber-route-coffee-agent/](specs/001-uber-route-coffee-agent/).
This file documents the RAG architecture per the project's deliverables requirement
("Documentação explicativa sobre a estratégia de Chunking, escolha do Modelo de
Embeddings e arquitetura de componentes").

## Frontend

`production/wsgi/static/index.html` — a single self-contained page (no build step, no
framework) served by the same WSGI app at `GET /`. Origin, destination, and "what time do
you need to arrive" go in; the page calls `POST /api/uber-route/recommend` and renders the
recommended departure time, estimated arrival, fare, and (when triggered) the waiting-place
card. See tasks.md's "Post-MVP Addition" section for what was and wasn't verified live.

## Architecture

```
POST /api/uber-route/recommend (WSGI, production/wsgi/app.py)
        │
        ▼
BS_UberRouteService  (validates payload, adapterless — fed by the WSGI app)
        │  send_request_sync
        ▼
BP_RouteOrchestrator (candidate-time scan, 30-min Business Rule, persistence)
        │                                   │
        │ SendRequestSync                   │ SendRequestSync (only if rule fires)
        ▼                                   ▼
BO_IntegratedMLPredictor            BO_HybridRAGEngine
   (FarePredictor via SQL)             (vector + keyword search over WaitingPlace)
        │                                   │
        ▼                                   ▼
        └──────────── InterSystems IRIS (relational + JSON + Vector Store) ──────────┘
```

All four hosts are pure Python (`intersystems-pyprod`), per the project constitution's
PyProd-First Interoperability principle — see [.specify/memory/constitution.md](.specify/memory/constitution.md).

## RAG pipeline: ingestion → chunking → indexing → retrieval → response

**Ingestion** (`ingestion/load_waiting_places.py`): reads `data/waiting_places_seed.json`
— name, address, category, lat/lng, rating, and a free-text description per place.

**Chunking**: sentence/semantic chunking, 256–512 tokens (word count as a proxy) per
chunk with 50-token overlap. Every chunk keeps the place's address and category attached
as a header, so a retrieved chunk is never missing the "where" and "what kind of place"
context. In practice, most place descriptions are short single-paragraph blurbs and fit
in one chunk — the chunker only splits when a description exceeds ~512 tokens. Rationale
and alternatives: [research.md §4](specs/001-uber-route-coffee-agent/research.md).

**Embedding model**: `sentence-transformers/all-MiniLM-L6-v2`, run locally via Python (no
external API call, no API key) — 384-dimension vectors. Chosen over
`text-embedding-3-small` specifically to avoid an external network/API-key dependency for
a Community Edition demo; the embedding call is isolated in
`production/hosts/bo_hybrid_rag_engine.py:_embed()`, so swapping providers later is a
contained change. Full rationale: [research.md §3](specs/001-uber-route-coffee-agent/research.md).

**Indexing**: `UberRoute.WaitingPlace.Embedding` is `VECTOR(DOUBLE, 384)` with an
`AS HNSW(Distance='Cosine')` index (IRIS 2025.1+; falls back to an unindexed
`VECTOR_COSINE` scan on older 2024.1.x images — the dataset is small enough that this is
a performance-only difference). `SearchableText` has an `%iFind.Index.Basic` index for
keyword search. Both verified live against IRIS 2026.1 Community — see
[research.md §5–6](specs/001-uber-route-coffee-agent/research.md).

**Retrieval** (`BO_HybridRAGEngine`): hybrid search — a `VECTOR_COSINE` top-10 semantic
search combined with an iFind keyword search
(`WHERE %ID %FIND search_index(SearchableTextIdx, ?)` — **not** `%CONTAINS(col, word)`,
which does not work against a DDL-created iFind index, corrected after live testing), each
candidate filtered to within ~1 km of the rider's origin (haversine distance), then ranked
by `0.6 * vector_score + 0.4 * keyword_score`.

**Response/"generation"**: rather than an LLM prompt/generation step, the top-ranked
candidate's structured fields (name, address, category, rating, distance) are returned
directly, plus a short templated rationale explaining why it was chosen over the other
candidates (distance, rating, which signal — semantic or keyword — dominated the match).
This keeps the "prompt → generation" step deterministic and explainable rather than
introducing an LLM call as a fifth dependency for a feature that doesn't need free-form
text generation.

## Known environment limitations (this session, IRIS 2026.1 Community, 2026-08-07)

- `TRAIN MODEL FarePredictor` crashes (segfault in the AutoML provider) on the tested
  Docker image — `CREATE MODEL`/the predictive SQL surface are verified correct, but no
  request will get a real fare prediction until a working AutoML (or PMML-imported)
  provider is available. See [tasks.md T010/T013](specs/001-uber-route-coffee-agent/tasks.md).
- `sentence-transformers` was not installed/run in this session (time budget) — the
  ingestion script and `BO_HybridRAGEngine._embed()` are written against its standard API
  but not executed end to end with a real model.
- The PyProd CLI (`intersystems_pyprod production.py`) must be run **inside** IRIS's own
  embedded Python, not from an external `pip install`; see
  [quickstart.md step 5](specs/001-uber-route-coffee-agent/quickstart.md).

## Running it

See [quickstart.md](specs/001-uber-route-coffee-agent/quickstart.md) for the full,
step-by-step validation guide. Local unit/integration/contract tests (no live IRIS
required — IRIS/pyprod calls are mocked):

```bash
pip install -r production/requirements.txt
pytest tests/
```
