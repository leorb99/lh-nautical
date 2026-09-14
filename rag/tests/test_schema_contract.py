"""Unit tests for rag/src/ingestion/schema_contract.py.

Pure Python only — this file must never import pyspark, delta, or any
Databricks package, directly or transitively. It imports only
schema_contract, which guarantees the same.
"""

from __future__ import annotations

import pytest

from src.ingestion.schema_contract import (
    CHUNK_SOURCE_FIELDS,
    DOCUMENT_CHUNKS_FIELDS,
    FieldSpec,
    field_names,
    get_field,
    physical_not_null_field_names,
)

_ALLOWED_LOGICAL_TYPES = {"string", "int", "array<string>", "timestamp"}

_EXPECTED_PHYSICAL_NOT_NULL = frozenset(
    {"chunk_id", "chunk_index", "document_id", "source_path", "chunk_text", "rule_ids"}
)

_EXPECTED_SOURCE_ORDER = (
    "chunk_id",
    "chunk_index",
    "document_id",
    "document_title",
    "source_path",
    "canonical_domain",
    "document_status",
    "section_path",
    "section_title",
    "chunk_text",
    "rule_ids",
    "line_start",
    "line_end",
    "content_hash",
    "document_hash",
    "source_git_commit",
)

_EXPECTED_TARGET_ORDER = (
    "chunk_id",
    "chunk_index",
    "document_id",
    "document_title",
    "source_path",
    "canonical_domain",
    "document_status",
    "document_hash",
    "source_git_commit",
    "section_path",
    "section_title",
    "chunk_text",
    "rule_ids",
    "line_start",
    "line_end",
    "content_hash",
    "ingested_at",
)


# ---------------------------------------------------------------------------
# Field counts
# ---------------------------------------------------------------------------


def test_chunk_source_fields_has_16_fields():
    assert len(CHUNK_SOURCE_FIELDS) == 16


def test_document_chunks_fields_has_17_fields():
    assert len(DOCUMENT_CHUNKS_FIELDS) == 17


# ---------------------------------------------------------------------------
# Order — each contract tested against its OWN declared expectation only.
# ---------------------------------------------------------------------------


def test_chunk_source_fields_order_matches_expected_source_contract():
    assert field_names(CHUNK_SOURCE_FIELDS) == _EXPECTED_SOURCE_ORDER


def test_document_chunks_fields_order_matches_expected_target_contract():
    assert field_names(DOCUMENT_CHUNKS_FIELDS) == _EXPECTED_TARGET_ORDER


def test_source_and_target_order_relationship_is_not_asserted():
    # Deliberately NOT a test of "source_order != target_order" as an
    # invariant. The two contracts' field orders differ today (see the two
    # expected-order constants above), but that difference is incidental,
    # not architectural — correctness comes from explicit named MERGE
    # mapping (see persistence.py), never from positional/ordering
    # alignment between source and target. This test exists only to
    # document that no ordering-relationship assertion is made here.
    assert True


# ---------------------------------------------------------------------------
# ingested_at presence
# ---------------------------------------------------------------------------


def test_chunk_source_fields_excludes_ingested_at():
    assert "ingested_at" not in field_names(CHUNK_SOURCE_FIELDS)


def test_document_chunks_fields_includes_ingested_at():
    assert "ingested_at" in field_names(DOCUMENT_CHUNKS_FIELDS)


def test_ingested_at_field_is_timestamp():
    ingested_at = get_field(DOCUMENT_CHUNKS_FIELDS, "ingested_at")
    assert ingested_at.logical_type == "timestamp"


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_chunk_source_field_names_are_unique():
    names = field_names(CHUNK_SOURCE_FIELDS)
    assert len(names) == len(set(names))


def test_document_chunks_field_names_are_unique():
    names = field_names(DOCUMENT_CHUNKS_FIELDS)
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Physical nullability
# ---------------------------------------------------------------------------


def test_chunk_source_physical_not_null_matches_closed_contract():
    assert set(physical_not_null_field_names(CHUNK_SOURCE_FIELDS)) == _EXPECTED_PHYSICAL_NOT_NULL


def test_document_chunks_physical_not_null_matches_closed_contract():
    assert (
        set(physical_not_null_field_names(DOCUMENT_CHUNKS_FIELDS)) == _EXPECTED_PHYSICAL_NOT_NULL
    )


def test_every_other_field_is_physically_nullable_in_both_contracts():
    for fields in (CHUNK_SOURCE_FIELDS, DOCUMENT_CHUNKS_FIELDS):
        for f in fields:
            expected_nullable = f.name not in _EXPECTED_PHYSICAL_NOT_NULL
            assert f.nullable is expected_nullable, f"{f.name}: nullable={f.nullable}"


# ---------------------------------------------------------------------------
# rule_ids type metadata
# ---------------------------------------------------------------------------


def test_rule_ids_is_array_of_string_with_no_nulls_in_source():
    rule_ids = get_field(CHUNK_SOURCE_FIELDS, "rule_ids")
    assert rule_ids.logical_type == "array<string>"
    assert rule_ids.array_element_type == "string"
    assert rule_ids.contains_null is False


def test_rule_ids_is_array_of_string_with_no_nulls_in_target():
    rule_ids = get_field(DOCUMENT_CHUNKS_FIELDS, "rule_ids")
    assert rule_ids.logical_type == "array<string>"
    assert rule_ids.array_element_type == "string"
    assert rule_ids.contains_null is False


# ---------------------------------------------------------------------------
# Logical type restriction
# ---------------------------------------------------------------------------


def test_only_allowed_logical_types_appear_in_either_contract():
    for fields in (CHUNK_SOURCE_FIELDS, DOCUMENT_CHUNKS_FIELDS):
        for f in fields:
            assert f.logical_type in _ALLOWED_LOGICAL_TYPES


def test_field_spec_rejects_unknown_logical_type():
    with pytest.raises(ValueError):
        FieldSpec(name="bad", logical_type="float", nullable=True)


def test_field_spec_rejects_array_metadata_on_non_array_field():
    with pytest.raises(ValueError):
        FieldSpec(name="bad", logical_type="string", nullable=True, contains_null=False)


def test_field_spec_rejects_array_field_without_contains_null():
    with pytest.raises(ValueError):
        FieldSpec(
            name="bad", logical_type="array<string>", nullable=True, array_element_type="string"
        )


# ---------------------------------------------------------------------------
# Field-set difference between the two contracts
# ---------------------------------------------------------------------------


def test_target_minus_source_field_set_is_exactly_ingested_at():
    source_names = set(field_names(CHUNK_SOURCE_FIELDS))
    target_names = set(field_names(DOCUMENT_CHUNKS_FIELDS))
    assert target_names - source_names == {"ingested_at"}


def test_source_minus_target_field_set_is_empty():
    source_names = set(field_names(CHUNK_SOURCE_FIELDS))
    target_names = set(field_names(DOCUMENT_CHUNKS_FIELDS))
    assert source_names - target_names == set()


# ---------------------------------------------------------------------------
# get_field lookup helper
# ---------------------------------------------------------------------------


def test_get_field_raises_for_unknown_name():
    with pytest.raises(KeyError):
        get_field(CHUNK_SOURCE_FIELDS, "not_a_real_field")
