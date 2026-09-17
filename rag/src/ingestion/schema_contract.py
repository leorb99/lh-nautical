"""Pure-Python schema contract for the RAG Delta persistence layer.

This module is 100% Python standard library. It must never import
``pyspark``, ``delta``, or any Databricks package — local tests import this
module directly, without PySpark installed. See ``spark_schema.py`` for the
PySpark ``StructType`` materialization of this same contract (Databricks
runtime only).

Two contracts are defined here, deliberately kept separate (see
``rag/README.md`` — Delta Persistence Design):

- ``CHUNK_SOURCE_FIELDS`` (16 fields): the shape of the in-memory
  ``Chunk`` dataclass, and therefore of the Spark DataFrame built directly
  from a sequence of ``Chunk`` instances. Does NOT include ``ingested_at``.
- ``DOCUMENT_CHUNKS_FIELDS`` (17 fields): the shape of the physical Delta
  target table (``rag/sql/create_document_chunks.sql``). Includes
  ``ingested_at``, which exists only at the persistence layer, generated at
  MERGE time (``current_timestamp()``), never carried in the source.

The two contracts' field orders differ (compare ``document_hash`` /
``source_git_commit`` / ``section_path`` / ``section_title`` between them).
This is incidental, not architectural: correctness of the MERGE write path
comes from explicit column-name mapping, never from positional or
incidental ordering alignment between source and target. Each contract's
order is validated only against its own declared expectation.

``FieldSpec.nullable`` represents PHYSICAL Delta nullability, not logical
requiredness. A field can be logically required by the ingestion contract
(see ``rag/README.md``) while still being physically nullable in the DDL —
that distinction is deliberate and is enforced separately by
``persistence_validation.py``, not by this module.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "FieldSpec",
    "CHUNK_SOURCE_FIELDS",
    "DOCUMENT_CHUNKS_FIELDS",
    "field_names",
    "physical_not_null_field_names",
    "get_field",
]

# Only these four logical types are needed by the current 16/17-field
# contracts. Not a general-purpose type system — extend only if a real new
# field requires it.
_ALLOWED_LOGICAL_TYPES = frozenset({"string", "int", "array<string>", "timestamp"})


@dataclass(frozen=True)
class FieldSpec:
    """One field of a schema contract, expressed in Python-native terms —
    no PySpark type is referenced here.
    """

    name: str
    logical_type: str
    nullable: bool
    array_element_type: str | None = None
    contains_null: bool | None = None

    def __post_init__(self) -> None:
        if self.logical_type not in _ALLOWED_LOGICAL_TYPES:
            raise ValueError(
                f"Field {self.name!r}: unknown logical_type {self.logical_type!r}; "
                f"allowed: {sorted(_ALLOWED_LOGICAL_TYPES)}"
            )
        if self.logical_type == "array<string>":
            if self.array_element_type != "string":
                raise ValueError(
                    f"Field {self.name!r}: array<string> requires array_element_type='string', "
                    f"got {self.array_element_type!r}"
                )
            if self.contains_null is None:
                raise ValueError(
                    f"Field {self.name!r}: array<string> requires an explicit contains_null."
                )
        else:
            if self.array_element_type is not None or self.contains_null is not None:
                raise ValueError(
                    f"Field {self.name!r}: array_element_type/contains_null only apply to "
                    "array<string> fields."
                )


# ---------------------------------------------------------------------------
# CHUNK_SOURCE_FIELDS — 16 fields, mirrors chunker.Chunk exactly.
# No ingested_at.
# ---------------------------------------------------------------------------

CHUNK_SOURCE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="chunk_id", logical_type="string", nullable=False),
    FieldSpec(name="chunk_index", logical_type="int", nullable=False),
    FieldSpec(name="document_id", logical_type="string", nullable=False),
    FieldSpec(name="document_title", logical_type="string", nullable=True),
    FieldSpec(name="source_path", logical_type="string", nullable=False),
    FieldSpec(name="canonical_domain", logical_type="string", nullable=True),
    FieldSpec(name="document_status", logical_type="string", nullable=True),
    FieldSpec(name="section_path", logical_type="string", nullable=True),
    FieldSpec(name="section_title", logical_type="string", nullable=True),
    FieldSpec(name="chunk_text", logical_type="string", nullable=False),
    FieldSpec(
        name="rule_ids",
        logical_type="array<string>",
        nullable=False,
        array_element_type="string",
        contains_null=False,
    ),
    FieldSpec(name="line_start", logical_type="int", nullable=True),
    FieldSpec(name="line_end", logical_type="int", nullable=True),
    FieldSpec(name="content_hash", logical_type="string", nullable=True),
    FieldSpec(name="document_hash", logical_type="string", nullable=True),
    FieldSpec(name="source_git_commit", logical_type="string", nullable=True),
)

# ---------------------------------------------------------------------------
# DOCUMENT_CHUNKS_FIELDS — 17 fields, mirrors the physical Delta DDL exactly
# (rag/sql/create_document_chunks.sql). Includes ingested_at.
# ---------------------------------------------------------------------------

DOCUMENT_CHUNKS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="chunk_id", logical_type="string", nullable=False),
    FieldSpec(name="chunk_index", logical_type="int", nullable=False),
    FieldSpec(name="document_id", logical_type="string", nullable=False),
    FieldSpec(name="document_title", logical_type="string", nullable=True),
    FieldSpec(name="source_path", logical_type="string", nullable=False),
    FieldSpec(name="canonical_domain", logical_type="string", nullable=True),
    FieldSpec(name="document_status", logical_type="string", nullable=True),
    FieldSpec(name="document_hash", logical_type="string", nullable=True),
    FieldSpec(name="source_git_commit", logical_type="string", nullable=True),
    FieldSpec(name="section_path", logical_type="string", nullable=True),
    FieldSpec(name="section_title", logical_type="string", nullable=True),
    FieldSpec(name="chunk_text", logical_type="string", nullable=False),
    FieldSpec(
        name="rule_ids",
        logical_type="array<string>",
        nullable=False,
        array_element_type="string",
        contains_null=False,
    ),
    FieldSpec(name="line_start", logical_type="int", nullable=True),
    FieldSpec(name="line_end", logical_type="int", nullable=True),
    FieldSpec(name="content_hash", logical_type="string", nullable=True),
    FieldSpec(name="ingested_at", logical_type="timestamp", nullable=True),
)


# ---------------------------------------------------------------------------
# Small, generic helpers — deliberately not a framework.
# ---------------------------------------------------------------------------


def field_names(fields: tuple[FieldSpec, ...]) -> tuple[str, ...]:
    """Field names, in declared order."""
    return tuple(f.name for f in fields)


def physical_not_null_field_names(fields: tuple[FieldSpec, ...]) -> tuple[str, ...]:
    """Names of fields that are physically NOT NULL, in declared order."""
    return tuple(f.name for f in fields if not f.nullable)


def get_field(fields: tuple[FieldSpec, ...], name: str) -> FieldSpec:
    """Look up one field by name. Raises KeyError if not present."""
    for f in fields:
        if f.name == name:
            return f
    raise KeyError(f"No field named {name!r} in this schema contract.")
