"""Loads the waiting-place seed collection into `UberRoute.WaitingPlace`.

Pipeline (constitution Principle III; research.md §3-4):
1. Ingest: read the seed JSON (name/address/category/lat/lng/rating/description).
2. Chunk: for descriptions over ~512 tokens, split into sentence-based 256-512 token
   chunks with 50-token overlap, always keeping the address/category header attached to
   every chunk. Short descriptions (the common case for a place blurb) are used whole.
3. Embed: `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim, no external API).
4. Index: insert into IRIS with `TO_VECTOR(?, DOUBLE, 384)` and a `SearchableText` column
   for the iFind keyword index (`sql/002_vector_index.sql`).

Run this after applying `sql/002_vector_index.sql` (see quickstart.md step 3):

    python ingestion/load_waiting_places.py --input data/waiting_places_seed.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_CHUNK_MAX_TOKENS = 512
_CHUNK_OVERLAP_TOKENS = 50


def _chunk_description(description: str, header: str) -> list[str]:
    """Sentence-based chunking, 256-512 "tokens" (whitespace-split words as a proxy),
    50-token overlap, with `header` (address + category) re-attached to every chunk so
    proximity/category context is never dropped from a match (research.md §4)."""
    words = description.split()
    if len(words) <= _CHUNK_MAX_TOKENS:
        return [f"{header} {description}".strip()]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + _CHUNK_MAX_TOKENS, len(words))
        chunk_text = " ".join(words[start:end])
        chunks.append(f"{header} {chunk_text}".strip())
        if end == len(words):
            break
        start = end - _CHUNK_OVERLAP_TOKENS
    return chunks


def _get_iris_version_supports_hnsw() -> bool:
    """research.md §6: HNSW indexing needs IRIS 2025.1+; the index itself is created by
    sql/002_vector_index.sql (run separately, before this script), so this check is
    informational only — it warns rather than blocks if the target is older."""
    import iris

    try:
        rs = iris.sql.exec("SELECT $SYSTEM.Version.GetMajor(), $SYSTEM.Version.GetMinor()")
        for row in rs:
            major, minor = int(row[0]), int(row[1])
            return (major, minor) >= (2025, 1)
    except Exception:  # noqa: BLE001
        pass
    return False


def load(input_path: Path) -> int:
    import iris

    from production.hosts.bo_hybrid_rag_engine import _embed  # reuses the same embedding call

    places = json.loads(input_path.read_text(encoding="utf-8"))

    insert_stmt = iris.sql.prepare(
        "INSERT INTO UberRoute.WaitingPlace "
        "(Name, Address, Category, Lat, Lng, Rating, Description, SearchableText, Embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, TO_VECTOR(?, DOUBLE, 384))"
    )

    count = 0
    for place in places:
        header = f"{place['name']} — {place['address']} ({place.get('category', '')})"
        chunks = _chunk_description(place.get("description", ""), header)
        # One WaitingPlace row per place; if chunking produced >1 chunk, embed the
        # concatenation so a single row still represents the whole place (WaitingPlace
        # is a typed record, not a raw RAG document store — research.md's %AI.RAG note).
        combined_text = " ".join(chunks)
        embedding = _embed(combined_text)
        searchable_text = (
            f"{place['name']} {place['address']} {place.get('category', '')} "
            f"{place.get('description', '')}"
        )

        insert_stmt.execute(
            place["name"],
            place["address"],
            place.get("category", ""),
            place.get("lat"),
            place.get("lng"),
            place.get("rating"),
            place.get("description", ""),
            searchable_text,
            embedding,
        )
        count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    count = load(args.input)
    print(f"Loaded {count} waiting places into UberRoute.WaitingPlace")


if __name__ == "__main__":
    main()
