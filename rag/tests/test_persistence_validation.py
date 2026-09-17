"""Unit tests for rag/src/ingestion/persistence_validation.py.

Pure Python only — this file must never import pyspark, delta, or any
Databricks package, directly or transitively. It imports persistence_validation,
chunker, loader, and parser, all of which are themselves pure Python.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.ingestion.chunker import Chunk, chunk_document
from src.ingestion.loader import CANONICAL_DOCUMENTS, load_all_canonical_documents
from src.ingestion.parser import parse_document
from src.ingestion.persistence_validation import (
    PersistenceValidationError,
    validate_chunks_for_persistence,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_DOC_SPEC = next(s for s in CANONICAL_DOCUMENTS if s.document_id == "gold_business_rules")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324 - test fixture only


def _valid_chunk(**overrides) -> Chunk:
    defaults = dict(
        chunk_id=_sha256_hex("chunk-0"),
        chunk_index=0,
        document_id=_DOC_SPEC.document_id,
        document_title="Regras de Negócio da Camada Gold",
        source_path=_DOC_SPEC.relative_path,
        canonical_domain=_DOC_SPEC.canonical_domain,
        document_status="CLOSED",
        section_path="Vendas > Vendas efetivas",
        section_title="Vendas efetivas",
        chunk_text="Regra de exemplo.",
        rule_ids=("GOLD-020",),
        line_start=10,
        line_end=12,
        content_hash=_sha256_hex("content-0"),
        document_hash=_sha256_hex("document-0"),
        source_git_commit=_git_sha("commit-0"),
    )
    defaults.update(overrides)
    return Chunk(**defaults)


# ---------------------------------------------------------------------------
# processed_document_ids
# ---------------------------------------------------------------------------


def test_empty_processed_document_ids_rejected():
    chunks = [_valid_chunk()]
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence(chunks, [])


def test_unknown_processed_document_id_rejected():
    chunks = [_valid_chunk()]
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence(chunks, ["not_a_real_document"])


# ---------------------------------------------------------------------------
# document_id scoping
# ---------------------------------------------------------------------------


def test_chunk_outside_canonical_allowlist_rejected():
    chunk = _valid_chunk(document_id="not_a_real_document")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_chunk_outside_processed_document_ids_rejected():
    other_spec = next(s for s in CANONICAL_DOCUMENTS if s.document_id != _DOC_SPEC.document_id)
    chunk = _valid_chunk()
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [other_spec.document_id])


# ---------------------------------------------------------------------------
# chunk_id uniqueness / shape
# ---------------------------------------------------------------------------


def test_duplicate_chunk_id_rejected():
    chunk_a = _valid_chunk(chunk_index=0)
    chunk_b = _valid_chunk(chunk_index=1)  # same chunk_id as chunk_a on purpose
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk_a, chunk_b], [_DOC_SPEC.document_id])


def test_malformed_chunk_id_rejected():
    chunk = _valid_chunk(chunk_id="not-a-sha256")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_malformed_content_hash_rejected():
    chunk = _valid_chunk(content_hash="short")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_malformed_document_hash_rejected():
    chunk = _valid_chunk(document_hash="short")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# chunk_index contiguity
# ---------------------------------------------------------------------------


def test_chunk_index_not_starting_at_zero_rejected():
    chunk = _valid_chunk(chunk_id=_sha256_hex("a"), chunk_index=1)
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_chunk_index_gap_rejected():
    chunk_a = _valid_chunk(chunk_id=_sha256_hex("a"), chunk_index=0)
    chunk_b = _valid_chunk(chunk_id=_sha256_hex("b"), chunk_index=2)
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk_a, chunk_b], [_DOC_SPEC.document_id])


def test_contiguous_chunk_index_across_multiple_chunks_accepted():
    chunk_a = _valid_chunk(chunk_id=_sha256_hex("a"), chunk_index=0)
    chunk_b = _valid_chunk(chunk_id=_sha256_hex("b"), chunk_index=1)
    chunk_c = _valid_chunk(chunk_id=_sha256_hex("c"), chunk_index=2)
    validate_chunks_for_persistence([chunk_a, chunk_b, chunk_c], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# source_git_commit
# ---------------------------------------------------------------------------


def test_invalid_source_git_commit_rejected():
    chunk = _valid_chunk(source_git_commit="not-a-sha")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_none_source_git_commit_accepted():
    chunk = _valid_chunk(source_git_commit=None)
    validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# document_status
# ---------------------------------------------------------------------------


def test_document_status_not_closed_rejected():
    chunk = _valid_chunk(document_status="approved")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# rule_ids
# ---------------------------------------------------------------------------


def test_rule_ids_none_rejected():
    chunk = _valid_chunk(rule_ids=None)
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_invalid_rule_id_format_rejected():
    chunk = _valid_chunk(rule_ids=("GOLD-1",))
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_empty_rule_ids_accepted():
    chunk = _valid_chunk(rule_ids=())
    validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# line provenance
# ---------------------------------------------------------------------------


def test_line_start_below_one_rejected():
    chunk = _valid_chunk(line_start=0, line_end=5)
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_line_end_below_line_start_rejected():
    chunk = _valid_chunk(line_start=10, line_end=5)
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


def test_empty_chunk_text_rejected():
    chunk = _valid_chunk(chunk_text="")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# source_path / canonical_domain mapping
# ---------------------------------------------------------------------------


def test_wrong_source_path_for_document_id_rejected():
    chunk = _valid_chunk(source_path="rag/knowledge_base/some_other_file.md")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_wrong_canonical_domain_for_document_id_rejected():
    chunk = _valid_chunk(canonical_domain="not_the_real_domain")
    with pytest.raises(PersistenceValidationError):
        validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# section_path / section_title nullability
# ---------------------------------------------------------------------------


def test_section_path_none_accepted_when_otherwise_valid():
    chunk = _valid_chunk(section_path=None, section_title=None)
    validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


def test_section_title_none_accepted_when_otherwise_valid():
    chunk = _valid_chunk(section_title=None)
    validate_chunks_for_persistence([chunk], [_DOC_SPEC.document_id])


# ---------------------------------------------------------------------------
# Zero-chunk batch
# ---------------------------------------------------------------------------


def test_zero_chunks_with_nonempty_processed_document_ids_accepted():
    validate_chunks_for_persistence([], [_DOC_SPEC.document_id])


def test_zero_chunks_across_multiple_processed_document_ids_accepted():
    other_spec = next(s for s in CANONICAL_DOCUMENTS if s.document_id != _DOC_SPEC.document_id)
    validate_chunks_for_persistence([], [_DOC_SPEC.document_id, other_spec.document_id])


# ---------------------------------------------------------------------------
# Real corpus validation — the 6 canonical documents, end to end.
# ---------------------------------------------------------------------------


def test_real_six_document_corpus_passes_validation():
    documents = load_all_canonical_documents(_REPO_ROOT)
    assert len(documents) == 6

    all_chunks: list[Chunk] = []
    processed_document_ids: list[str] = []
    for document in documents:
        parsed = parse_document(document)
        chunks = chunk_document(parsed)
        all_chunks.extend(chunks)
        processed_document_ids.append(document.document_id)

    assert len(all_chunks) == 163

    # Does not depend on source_git_commit being non-null: rag/ may be
    # untracked in the working tree, in which case every chunk's
    # source_git_commit is legitimately None (see loader.py) — validation
    # must still pass.
    validate_chunks_for_persistence(all_chunks, processed_document_ids)
