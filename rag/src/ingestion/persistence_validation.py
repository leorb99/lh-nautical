"""Pure-Python pre-write validation for the RAG Delta persistence layer.

100% Python standard library plus this project's own pure-Python modules
(``chunker.py``, ``loader.py``). Must never import ``pyspark``, ``delta``,
any Databricks package, ``spark_schema``, or ``persistence`` — local tests
import this module directly, without PySpark installed, and it must never
depend on the module that depends on it (no circular imports).

``validate_chunks_for_persistence`` is the single entry point. It never
touches Spark/Delta and never mutates its inputs — it either returns
normally (batch is valid) or raises :class:`PersistenceValidationError`
(batch violates the persistence pre-write contract). It does not
re-validate anything already guaranteed by ``chunker.py`` (in particular,
token-budget compliance — that is the chunker's closed responsibility, not
persistence's).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence

from .chunker import Chunk
from .loader import CANONICAL_DOCUMENTS

__all__ = ["PersistenceValidationError", "validate_chunks_for_persistence"]


class PersistenceValidationError(ValueError):
    """Raised when a chunk batch violates the persistence pre-write contract.

    Never raised for a partial/incremental fix — validation either passes
    the whole batch or rejects it before any write is attempted.
    """


# content_hash / document_hash / chunk_id are all produced by
# loader.compute_document_hash, i.e. hashlib.sha256(...).hexdigest():
# lowercase, 64 hex characters.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# source_git_commit is produced by loader._resolve_source_git_commit from
# `git rev-parse HEAD`'s stdout: a standard Git SHA-1 hex object id,
# lowercase, 40 hex characters. Not invented — this is the real shape
# produced by the existing, closed loader.py contract.
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_RULE_ID_RE = re.compile(r"^GOLD-\d{3}$")

_DOCS_BY_ID = {spec.document_id: spec for spec in CANONICAL_DOCUMENTS}
_ALLOWED_DOCUMENT_IDS = frozenset(_DOCS_BY_ID)


def validate_chunks_for_persistence(
    chunks: Sequence[Chunk],
    processed_document_ids: Sequence[str],
) -> None:
    """Validate a batch of chunks before any Delta write is attempted.

    ``processed_document_ids`` declares which documents this run actually
    processed — it is NOT inferred from ``chunks`` (a document producing
    zero chunks, e.g. because every section is heading-only, is a valid,
    expected batch state: it must still appear in ``processed_document_ids``
    so its stale rows can be correctly scoped for deletion downstream).

    Raises:
        PersistenceValidationError: any contract violation. Nothing about
            ``chunks`` is corrected or modified.
    """
    if not processed_document_ids:
        raise PersistenceValidationError("processed_document_ids must not be empty.")

    processed_ids_set = set(processed_document_ids)
    unknown_processed = processed_ids_set - _ALLOWED_DOCUMENT_IDS
    if unknown_processed:
        raise PersistenceValidationError(
            f"processed_document_ids contains document_id(s) outside the canonical "
            f"allowlist: {sorted(unknown_processed)}"
        )

    seen_chunk_ids: set[str] = set()
    indices_by_document: dict[str, list[int]] = defaultdict(list)

    for chunk in chunks:
        if chunk.document_id not in _ALLOWED_DOCUMENT_IDS:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: document_id {chunk.document_id!r} is "
                "outside the canonical allowlist."
            )
        if chunk.document_id not in processed_ids_set:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: document_id {chunk.document_id!r} is "
                "outside processed_document_ids for this run."
            )

        if chunk.chunk_id in seen_chunk_ids:
            raise PersistenceValidationError(f"Duplicate chunk_id in batch: {chunk.chunk_id!r}")
        seen_chunk_ids.add(chunk.chunk_id)

        if not _SHA256_HEX_RE.match(chunk.chunk_id):
            raise PersistenceValidationError(
                f"chunk_id {chunk.chunk_id!r} is not a valid SHA-256 hex digest."
            )
        if not _SHA256_HEX_RE.match(chunk.content_hash):
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: content_hash {chunk.content_hash!r} is not "
                "a valid SHA-256 hex digest."
            )
        if not _SHA256_HEX_RE.match(chunk.document_hash):
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: document_hash {chunk.document_hash!r} is not "
                "a valid SHA-256 hex digest."
            )

        if chunk.source_git_commit is not None and not _GIT_SHA_RE.match(chunk.source_git_commit):
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: source_git_commit "
                f"{chunk.source_git_commit!r} is neither None nor a valid Git SHA."
            )

        if chunk.document_status != "CLOSED":
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: document_status must be 'CLOSED', got "
                f"{chunk.document_status!r}."
            )

        if chunk.rule_ids is None:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: rule_ids must never be None (use an empty tuple)."
            )
        for rule_id in chunk.rule_ids:
            if not _RULE_ID_RE.match(rule_id):
                raise PersistenceValidationError(
                    f"chunk_id={chunk.chunk_id!r}: invalid Rule ID {rule_id!r} "
                    "(expected exactly 'GOLD-' + 3 digits)."
                )

        if chunk.line_start < 1:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: line_start must be >= 1, got {chunk.line_start}."
            )
        if chunk.line_end < chunk.line_start:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: line_end ({chunk.line_end}) must be >= "
                f"line_start ({chunk.line_start})."
            )

        if not chunk.chunk_text:
            raise PersistenceValidationError(f"chunk_id={chunk.chunk_id!r}: chunk_text is empty.")

        expected_spec = _DOCS_BY_ID[chunk.document_id]
        if chunk.source_path != expected_spec.relative_path:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: source_path {chunk.source_path!r} does not "
                f"match the allowlisted path {expected_spec.relative_path!r} for document_id "
                f"{chunk.document_id!r}."
            )
        if chunk.canonical_domain != expected_spec.canonical_domain:
            raise PersistenceValidationError(
                f"chunk_id={chunk.chunk_id!r}: canonical_domain {chunk.canonical_domain!r} "
                f"does not match the expected {expected_spec.canonical_domain!r} for "
                f"document_id {chunk.document_id!r}."
            )

        indices_by_document[chunk.document_id].append(chunk.chunk_index)

    for document_id, indices in indices_by_document.items():
        sorted_indices = sorted(indices)
        if sorted_indices != list(range(len(sorted_indices))):
            raise PersistenceValidationError(
                f"document_id {document_id!r}: chunk_index values must be contiguous, "
                f"0-based, and unique; got {sorted_indices}."
            )

    # A zero-chunk batch (chunks == []) is valid whenever processed_document_ids
    # is non-empty — the checks above simply have nothing to iterate over in
    # that case, and no separate branch is needed to accept it.
