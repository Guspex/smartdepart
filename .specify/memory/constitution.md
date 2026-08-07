<!--
Sync Impact Report
- Version change: 1.0.0 → 1.0.1 (PATCH — wording clarification, no principle content change)
- Modified principles:
  - II. IRIS as Single Multimodel Platform — clarified "native `%Vector` data type" to "native
    `VECTOR` SQL data type (backed by the `%Vector` ObjectScript datatype class)". Raised by
    /speckit-analyze on the 001-uber-route-coffee-agent feature: every downstream artifact
    (research.md, data-model.md, tasks.md) correctly uses the SQL-level `VECTOR(DOUBLE, N)`
    syntax, and the constitution's prior wording named only the ObjectScript-level type,
    which could read as a mismatch on a literal compliance check even though both names
    refer to the same underlying feature.
- Added sections: none
- Removed sections: none
- Deferred / TODO items (carried over, unchanged by this amendment):
  - TODO(RATIFICATION_DATE): original adoption date unknown; set to date of first ratification
    (2026-08-07) pending confirmation from project owner.
- Templates requiring alignment: none newly affected by this wording-only change.

---

Sync Impact Report (previous — 1.0.0 initial ratification, 2026-08-07)
- Version change: [TEMPLATE] → 1.0.0 (initial ratification)
- Modified principles: none (first fill of template placeholders)
- Added sections:
  - Core Principles I–V (PyProd-First Interoperability; IRIS as Single Multimodel Platform;
    Hybrid Retrieval & Documented Embeddings; In-Database Predictive Models via IntegratedML;
    Observability by Default)
  - Data & External Integration Standards (Section 2)
  - Development Workflow (Section 3)
  - Governance
- Removed sections: none
- Note on source input: The user-supplied input for this run (SPECS-001: Uber Route &
  Coffee Recommendation Agent) was a single-feature specification, not a set of governance
  principles. Per this command's scope guard, only the recurring, project-wide technology
  and quality mandates found in that input were generalized into principles below. The
  feature itself (BS/BP/BO class design, JSON payload shape, business rules, DDL, WSGI app)
  was NOT implemented here — see Next Actions in the command output for the deferred intent.
- Templates requiring alignment (checked, not modified by this command):
  - .specify/templates/plan-template.md — ⚠ pending manual check that its Constitution Check
    gate references the five principles below (PyProd-only hosts, IRIS-native vector store,
    hybrid retrieval + documented chunking/embeddings, IntegratedML for in-DB predictions,
    mandatory observability).
  - .specify/templates/spec-template.md — ⚠ pending manual check for consistency (no
    technology-stack leakage expected here; template is stack-agnostic by design).
  - .specify/templates/tasks-template.md — ⚠ pending manual check that task categories can
    accommodate DDL/IntegratedML/RAG-indexing tasks distinctly from application code tasks.
  - .specify/templates/checklist-template.md — no changes required.
-->

# Smart Depart Constitution
<!-- Project directory: "APP Smart Depart". No prior constitution or README established an
official product name, so this title is inferred from the working directory. Rename this
heading in a future amendment if the project adopts an official name. -->

## Core Principles

### I. PyProd-First Interoperability
All InterSystems interoperability integrations in this project MUST be built as Pure
Python Productions using `intersystems-pyprod` — no ObjectScript-first hosts for new
integration work. Business Service, Business Process, and Business Operation hosts MUST be
implemented in Python. Any HTTP/REST-facing Business Service MUST expose its interface via
the WSGI protocol, validate inbound payloads before forwarding them into the production, and
route validated messages to a Business Process rather than performing orchestration logic
itself.
**Rationale**: Standardizing on PyProd keeps the team in one language across service,
process, and operation layers, removes the ObjectScript/Python context switch, and matches
the project's chosen interoperability library.

### II. IRIS as Single Multimodel Platform
InterSystems IRIS (Community Edition) is the only system of record for this project's
operational, document, and vector data. Relational tables, the JSON Document Store, and the
Vector Store MUST coexist in the same IRIS database rather than being split across
purpose-built external stores. Vector search MUST use IRIS's native `VECTOR` SQL data type (backed by the `%Vector`
ObjectScript datatype class) and built-in distance functions (`VECTOR_COSINE`,
`VECTOR_DOT_PRODUCT`); introducing an external vector database (e.g., Pinecone, Qdrant,
pgvector) is prohibited without a constitution amendment.
**Rationale**: The project's value proposition depends on demonstrating IRIS's multimodel
capability; splitting data across external stores would defeat that purpose and add
unnecessary operational surface area.

### III. Hybrid Retrieval & Documented Embeddings (NON-NEGOTIABLE)
Any retrieval-augmented (RAG) or search feature MUST combine semantic vector search with
exact/keyword SQL search (`%CONTAINS`, `LIKE`, or equivalent) using a weighted ranking
strategy — vector-only or keyword-only retrieval is not acceptable for production features.
Before such a feature is implemented, its chunking strategy (target chunk size, overlap, and
what context is preserved per chunk) and its embedding model choice (name and vector
dimension) MUST be documented in the feature's plan or a dedicated design doc.
**Rationale**: Hybrid search compensates for the known weaknesses of pure vector similarity
(missed exact matches, e.g., proper nouns) and pure keyword search (missed paraphrases);
documenting chunking/embedding choices up front prevents silent, hard-to-debug retrieval
quality regressions later.

### IV. In-Database Predictive Models via IntegratedML
Predictive or machine-learning capabilities that operate on data already resident in IRIS
MUST be implemented using IntegratedML (`CREATE MODEL` / `TRAIN MODEL` / `PREDICT` via SQL)
rather than standing up a separate external model-serving stack. Business Operations that
need predictions MUST query trained IntegratedML models through Embedded Python or SQL
against the IRIS connection already in use — not via a second, parallel ML service.
**Rationale**: IntegratedML keeps training data, model artifacts, and inference in the same
governed database, avoiding data-movement latency and a second infrastructure surface to
operate and secure.

### V. Observability by Default
Every interoperability host (Business Service, Process, or Operation) MUST emit structured
logs visible in the IRIS Management Portal and MUST expose OpenTelemetry-compatible metrics
or custom telemetry hooks for its key operations (message received, external call made,
error raised, business rule outcome). A feature MUST NOT be considered complete, and MUST
NOT be merged, without this instrumentation in place.
**Rationale**: Interoperability productions fail silently in production if not instrumented;
requiring observability at build time is far cheaper than retrofitting it after an incident.

## Data & External Integration Standards

External and non-IRIS-native data sources MUST be integrated through explicit adapters, not
embedded ad hoc inside business logic:
- Time-varying external data (e.g., traffic, weather) MUST be mapped into IRIS via Foreign
  Tables rather than pulled imperatively inside a Business Process.
- Calls to public APIs (e.g., geocoding, urban utility APIs) MUST be isolated inside a
  dedicated Business Operation with its own adapter, so the external dependency can be
  mocked, rate-limited, or swapped without touching orchestration logic.
- Where feasible, features SHOULD demonstrate and document IRIS's multimodel capability
  (relational + JSON Document Store + Vector Store operating together) rather than treating
  it as three separate databases.

## Development Workflow

- Every feature that introduces or changes schema MUST ship its DDL/SQL (tables, `%Vector`
  indexes, Foreign Table definitions, `CREATE MODEL`/`TRAIN MODEL` statements) as versioned
  files alongside the PyProd code that depends on them, not as manual, undocumented steps.
- Every RAG-capable feature MUST ship the chunking-strategy and embedding-model
  documentation required by Principle III as part of its plan, before implementation begins.
- Changes to interoperability hosts MUST be compiled/validated in IRIS and exercised via the
  production's test tooling before being considered done; a change that cannot be verified
  running in IRIS is not complete.

## Governance

This constitution supersedes ad hoc technology choices for interoperability, data storage,
retrieval, and predictive-model work in this project. Where a feature's plan conflicts with
a principle above, the plan MUST either be revised to comply or the conflict MUST be
resolved via an explicit constitution amendment before implementation proceeds — silent
deviation is not permitted.

**Amendment procedure**: Propose the change (principle text, rationale) via
`/speckit-constitution`; the Sync Impact Report generated by that command MUST list the
version bump, the sections touched, and any templates that need follow-up review. Amendments
take effect immediately upon being written to this file.

**Versioning policy** (semantic versioning applied to governance):
- MAJOR: A principle is removed or redefined in a backward-incompatible way (e.g., dropping
  the PyProd-only mandate, or allowing an external vector database).
- MINOR: A new principle or materially expanded section is added (e.g., a new mandatory
  security or data-retention principle).
- PATCH: Wording clarifications, typo fixes, or non-semantic refinements.

**Compliance review**: Any plan produced by `/speckit-plan` MUST include a Constitution
Check against the five principles above before task breakdown begins. Reviewers evaluating a
pull request for this project MUST verify PyProd-only hosts, IRIS-native storage, hybrid
retrieval with documented chunking/embeddings, IntegratedML-based predictions, and
observability instrumentation are all present before approving.

**Version**: 1.0.1 | **Ratified**: TODO(RATIFICATION_DATE): confirm original adoption date | **Last Amended**: 2026-08-07
