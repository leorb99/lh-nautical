"""Thin client for querying this project's Databricks AI Search Delta Sync
Index (``dev_natalia_correia_lh_nautical.rag.document_chunks_index``).

Read-only: this module never creates, deletes, or syncs an index. It only
issues query requests against an index that already exists and is
``ready``.

Uses the Databricks SDK's authenticated API client (``WorkspaceClient``,
profile-based auth via ``~/.databrickscfg``, matching the rest of this
project) rather than a bare ``requests`` call, so credential resolution is
identical to every other Databricks interaction in this repo. The typed
``VectorSearchIndexesAPI.query_index()`` wrapper in the installed SDK
version does not expose ``query_type`` (HYBRID vs ANN), so this module
calls the underlying REST endpoint directly via the SDK's own internal
``_api.do(...)`` request helper, which still goes through the SDK's normal
auth/retry machinery — it is not a parallel HTTP implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from databricks.sdk import WorkspaceClient

__all__ = ["RetrievedChunk", "DEFAULT_COLUMNS", "query_index"]

DEFAULT_COLUMNS: tuple[str, ...] = (
    "chunk_id",
    "document_id",
    "document_title",
    "canonical_domain",
    "section_path",
    "section_title",
    "chunk_text",
    "rule_ids",
)


@dataclass(frozen=True)
class RetrievedChunk:
    """One ranked result from an AI Search query, with a 1-based rank."""

    rank: int
    chunk_id: str
    document_id: str
    document_title: str | None
    canonical_domain: str | None
    section_path: str | None
    section_title: str | None
    chunk_text: str
    rule_ids: tuple[str, ...]
    score: float


def query_index(
    client: WorkspaceClient,
    index_name: str,
    query_text: str,
    *,
    num_results: int = 15,
    query_type: str = "HYBRID",
    columns: Sequence[str] = DEFAULT_COLUMNS,
    filters_json: str | None = None,
) -> list[RetrievedChunk]:
    """Query a Databricks AI Search Delta Sync Index and return ranked
    results in the order returned by the service (rank 1 = first).

    Does not filter, re-rank, or otherwise post-process results beyond
    what ``filters_json`` (a raw pass-through of the service's own filter
    syntax, e.g. ``'{"rule_ids": "GOLD-032"}'`` for an ARRAY<STRING>
    column) requests — this is a thin transport layer over the raw query
    response, not a retrieval policy. Callers decide what filter, if any,
    to send; this function never derives one on its own.
    """
    body = {
        "columns": list(columns),
        "query_text": query_text,
        "query_type": query_type,
        "num_results": num_results,
    }
    if filters_json is not None:
        body["filters_json"] = filters_json
    raw = client.vector_search_indexes._api.do(
        "POST",
        f"/api/2.0/vector-search/indexes/{index_name}/query",
        body=body,
    )

    manifest_columns = raw.get("manifest", {}).get("columns", [])
    column_names = [c["name"] for c in manifest_columns]
    data_rows = raw.get("result", {}).get("data_array", [])

    results: list[RetrievedChunk] = []
    for rank, row in enumerate(data_rows, start=1):
        record = dict(zip(column_names, row))
        rule_ids_value = record.get("rule_ids") or []
        results.append(
            RetrievedChunk(
                rank=rank,
                chunk_id=record["chunk_id"],
                document_id=record["document_id"],
                document_title=record.get("document_title"),
                canonical_domain=record.get("canonical_domain"),
                section_path=record.get("section_path"),
                section_title=record.get("section_title"),
                chunk_text=record["chunk_text"],
                rule_ids=tuple(rule_ids_value),
                score=float(record["score"]),
            )
        )
    return results
