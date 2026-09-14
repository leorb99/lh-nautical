"""Unit tests for rag/src/ingestion/persistence_expressions.py.

Pure Python only — this file must never import pyspark, delta, or any
Databricks package, directly or transitively. It imports only
persistence_expressions, which guarantees the same (it depends only on
stdlib and schema_contract).
"""

from __future__ import annotations

import pytest

from src.ingestion.persistence_expressions import (
    MATCHED_UPDATE_FIELDS,
    PersistenceTargetError,
    build_delete_condition,
    build_insert_values,
    build_update_condition,
    build_update_set,
    parse_three_part_name,
    sql_string_literal,
)
from src.ingestion.schema_contract import CHUNK_SOURCE_FIELDS, field_names

_IDENTITY_AND_CONTENT_FIELDS = {
    "document_id",
    "section_path",
    "section_title",
    "chunk_index",
    "chunk_text",
    "rule_ids",
    "content_hash",
}


# ---------------------------------------------------------------------------
# parse_three_part_name — identifier parsing
# ---------------------------------------------------------------------------


def test_valid_three_part_identifier_accepted():
    assert parse_three_part_name("dev_lh_nautical.rag.document_chunks") == (
        "dev_lh_nautical",
        "rag",
        "document_chunks",
    )


def test_fewer_than_three_parts_rejected():
    with pytest.raises(PersistenceTargetError):
        parse_three_part_name("rag.document_chunks")


def test_more_than_three_parts_rejected():
    with pytest.raises(PersistenceTargetError):
        parse_three_part_name("a.b.c.d")


def test_sql_metacharacters_in_segment_rejected():
    with pytest.raises(PersistenceTargetError):
        parse_three_part_name("catalog; DROP TABLE x.rag.document_chunks")


def test_blank_segment_rejected():
    with pytest.raises(PersistenceTargetError):
        parse_three_part_name("catalog..document_chunks")


def test_single_hyphen_segment_rejected_conservatively():
    # Deliberate: the parser stays conservative and does not attempt to
    # support characters like '-' preemptively. This is a documented,
    # intentional limitation, not an oversight — see the module docstring.
    with pytest.raises(PersistenceTargetError):
        parse_three_part_name("dev-catalog.rag.document_chunks")


# ---------------------------------------------------------------------------
# sql_string_literal — escaping
# ---------------------------------------------------------------------------


def test_sql_string_literal_ordinary_value():
    assert sql_string_literal("gold_business_rules") == "'gold_business_rules'"


def test_sql_string_literal_escapes_apostrophe():
    assert sql_string_literal("o'brien") == "'o''brien'"


# ---------------------------------------------------------------------------
# build_update_condition — matched update trigger
# ---------------------------------------------------------------------------


def test_matched_update_fields_is_exactly_the_approved_eight():
    assert set(MATCHED_UPDATE_FIELDS) == {
        "document_title",
        "source_path",
        "canonical_domain",
        "document_status",
        "document_hash",
        "source_git_commit",
        "line_start",
        "line_end",
    }
    assert len(MATCHED_UPDATE_FIELDS) == 8


def test_matched_update_fields_excludes_identity_and_content_fields():
    assert set(MATCHED_UPDATE_FIELDS).isdisjoint(_IDENTITY_AND_CONTENT_FIELDS)


def test_build_update_condition_uses_null_safe_comparison_for_every_field():
    condition = build_update_condition()
    for name in MATCHED_UPDATE_FIELDS:
        assert f"NOT (target.{name} <=> source.{name})" in condition


def test_build_update_condition_never_uses_bare_inequality():
    condition = build_update_condition()
    assert "!=" not in condition
    assert "<>" not in condition


def test_build_update_condition_mentions_no_identity_or_content_field():
    condition = build_update_condition()
    for name in _IDENTITY_AND_CONTENT_FIELDS:
        assert f"target.{name}" not in condition


# ---------------------------------------------------------------------------
# build_update_set
# ---------------------------------------------------------------------------


def test_build_update_set_has_exactly_eight_metadata_fields_plus_ingested_at():
    update_set = build_update_set()
    assert set(update_set) == set(MATCHED_UPDATE_FIELDS) | {"ingested_at"}
    assert len(update_set) == 9


def test_build_update_set_ingested_at_is_current_timestamp():
    update_set = build_update_set()
    assert update_set["ingested_at"] == "current_timestamp()"


def test_build_update_set_never_mutates_identity_or_content_fields():
    update_set = build_update_set()
    assert _IDENTITY_AND_CONTENT_FIELDS.isdisjoint(update_set)


def test_build_update_set_values_reference_source_by_name():
    update_set = build_update_set()
    for name in MATCHED_UPDATE_FIELDS:
        assert update_set[name] == f"source.{name}"


# ---------------------------------------------------------------------------
# build_insert_values
# ---------------------------------------------------------------------------


def test_build_insert_values_has_all_sixteen_source_fields():
    values = build_insert_values()
    for name in field_names(CHUNK_SOURCE_FIELDS):
        assert name in values


def test_build_insert_values_maps_by_name_not_position():
    values = build_insert_values()
    for name in field_names(CHUNK_SOURCE_FIELDS):
        assert values[name] == f"source.{name}"


def test_build_insert_values_adds_ingested_at():
    values = build_insert_values()
    assert values["ingested_at"] == "current_timestamp()"


def test_build_insert_values_has_exactly_seventeen_keys():
    values = build_insert_values()
    assert len(values) == 17


def test_build_insert_values_contains_no_wildcard():
    # Structural guard against ever reintroducing INSERT * — every value
    # is an explicit "source.<name>" or a literal, never "*" or "source.*".
    values = build_insert_values()
    assert "*" not in values
    assert all(v != "source.*" for v in values.values())


# ---------------------------------------------------------------------------
# build_delete_condition — scoped stale-delete
# ---------------------------------------------------------------------------


def test_build_delete_condition_scoped_to_single_processed_document_id():
    condition = build_delete_condition(["gold_business_rules"])
    assert condition == "target.document_id IN ('gold_business_rules')"


def test_build_delete_condition_scoped_to_multiple_processed_document_ids_deterministic():
    ids = ["gold_business_rules", "inventory_and_stockout", "demand_forecasting"]
    condition_a = build_delete_condition(ids)
    condition_b = build_delete_condition(ids)
    assert condition_a == condition_b
    assert condition_a == (
        "target.document_id IN "
        "('gold_business_rules', 'inventory_and_stockout', 'demand_forecasting')"
    )


def test_build_delete_condition_escapes_apostrophes_in_document_ids():
    condition = build_delete_condition(["o'brien_doc"])
    assert condition == "target.document_id IN ('o''brien_doc')"


def test_build_delete_condition_rejects_empty_processed_document_ids():
    # An empty scope must never be used to build a stale-delete predicate —
    # this function refuses outright rather than producing a malformed or
    # ambiguous "IN ()" clause.
    with pytest.raises(PersistenceTargetError):
        build_delete_condition([])


def test_build_delete_condition_never_produces_unscoped_predicate():
    condition = build_delete_condition(["gold_business_rules"])
    assert "document_id IN (" in condition
    # No path to a bare "TRUE"/unconditional predicate.
    assert "1=1" not in condition
    assert "IN ()" not in condition
