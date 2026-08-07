-- WaitingPlace (RAG source collection) + WaitingPlaceSuggestion, per data-model.md.
-- Embedding uses sentence-transformers/all-MiniLM-L6-v2 → 384 dimensions (research.md §3).
-- HNSW index requires IRIS 2025.1+; on older 2024.1.x images, skip the CREATE INDEX
-- statement and VECTOR_COSINE still works as an unindexed scan (research.md §6) — the
-- ingestion script (ingestion/load_waiting_places.py) detects the version and applies
-- the index conditionally when running end to end.

CREATE TABLE UberRoute.WaitingPlace (
    Name            VARCHAR(200) NOT NULL,
    Address         VARCHAR(300) NOT NULL,
    Category        VARCHAR(64),
    Lat             DOUBLE,
    Lng             DOUBLE,
    Rating          NUMERIC(2,1),
    Description     VARCHAR(4000),
    SearchableText  VARCHAR(4000),
    Embedding       VECTOR(DOUBLE, 384)
);

-- iFind index for %CONTAINS keyword search (constitution Principle III: hybrid retrieval).
CREATE INDEX SearchableTextIdx ON TABLE UberRoute.WaitingPlace (SearchableText)
    AS %iFind.Index.Basic;

-- HNSW vector index — only if the target instance is 2025.1+ (research.md §6).
CREATE INDEX EmbeddingIdx ON TABLE UberRoute.WaitingPlace (Embedding)
    AS HNSW(Distance='Cosine');

CREATE TABLE UberRoute.WaitingPlaceSuggestion (
    RouteRecommendationID   INTEGER NOT NULL,
    WaitingPlaceID          INTEGER NOT NULL,
    VectorScore             DOUBLE,
    KeywordScore            DOUBLE,
    FinalScore              DOUBLE,
    Rank                    INTEGER,
    Rationale               VARCHAR(300)
);
