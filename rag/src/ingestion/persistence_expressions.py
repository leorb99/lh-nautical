"""Pure-Python MERGE expression builders and identifier/literal helpers for
the RAG Delta persistence layer.

100% Python standard library plus ``schema_contract`` (also pure). Must
never import ``pyspark``, ``delta``, any Databricks package, or
``persistence.py`` — local tests import this module directly, without
PySpark installed, and it must never depend on the module that depends on
it (no circular imports).

Encodes the MERGE semantics closed in the Delta Persistence Design: which
fields may trigger a same-``chunk_id`` metadata update, how the insert
column mapping is built (explicit, by name, never ``INSERT *``), how the
scoped stale-delete predicate is built, and safe SQL string-literal
escaping / three-part identifier parsing. ``persistence.py`` imports these
helpers rather than re-declaring any of this logic — there is exactly one
copy of each.
"""

from __future__ import annotations

import re
from typing import Sequence

from .schema_contract import CHUNK_SOURCE_FIELDS, field_names

__all__ = [
    "PersistenceTargetError",
    "parse_three_part_name",
    "sql_string_literal",
    "MATCHED_UPDATE_FIELDS",
    "build_update_condition",
    "build_update_set",
    "build_insert_values",
    "build_delete_condition",
]


class PersistenceTargetError(ValueError):
    """Raised for an invalid target table identifier, or (from
    ``persistence.py``) when the target table does not exist or is not
    schema-compatible.

    Never triggers a CREATE/ALTER/DROP/REPAIR — this module and
    ``persistence.py`` only validate.
    """


# ---------------------------------------------------------------------------
# Target table identifier safety — never interpolate an unvalidated string
# into SQL text.
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_three_part_name(target_table: str) -> tuple[str, str, str]:
    """Parse and validate a strict ``catalog.schema.table`` identifier.

    Deliberately conservative: each segment must match
    ``[A-Za-z_][A-Za-z0-9_]*`` exactly. Unity Catalog securable object
    names cannot contain ``.`` (it is the namespace separator between
    catalog/schema/table), so this parser does not attempt to support a
    literal dot inside a single segment, and none should be added here —
    see ``rag/README.md`` for the open runtime question of whether any
    real resolved catalog name in this project needs a character (e.g.
    ``-``) that this conservative regex would also reject; that is a
    Databricks-only confirmation, not something to preemptively work
    around by loosening this parser.
    """
    parts = target_table.split(".")
    if len(parts) != 3:
        raise PersistenceTargetError(
            f"target_table must be a three-part 'catalog.schema.table' name, got "
            f"{target_table!r}."
        )
    catalog, schema, table = parts
    for part in (catalog, schema, table):
        if not _IDENTIFIER_RE.match(part):
            raise PersistenceTargetError(
                f"target_table {target_table!r} contains an invalid identifier segment "
                f"{part!r}."
            )
    return catalog, schema, table


def sql_string_literal(value: str) -> str:
    """Standard SQL single-quote escaping, for a value used as a SQL STRING
    LITERAL (never as an unescaped identifier).
    """
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# MERGE — closed semantics (see the Delta Persistence Alignment Reports).
# ---------------------------------------------------------------------------

# Fields that can legitimately change while chunk_id stays stable. Does NOT
# include document_id, section_path, chunk_index, content_hash (chunk_id's
# own hash inputs), chunk_text (determines content_hash), or rule_ids
# (derived from chunk_text) — a real change to any of those is a new
# chunk_id (delete old + insert new), never an update. Does NOT include
# section_title either: it is always the trailing segment of section_path
# under the current, closed parser contract, so it cannot legitimately
# differ while section_path (hence chunk_id) stays fixed.
MATCHED_UPDATE_FIELDS: tuple[str, ...] = (
    "document_title",
    "source_path",
    "canonical_domain",
    "document_status",
    "document_hash",
    "source_git_commit",
    "line_start",
    "line_end",
)


def build_update_condition() -> str:
    """WHEN MATCHED AND <this> — fires only when at least one of the 8
    approved metadata fields genuinely differs, using the null-safe ``<=>``
    operator throughout (so two NULLs on either side never trigger a
    spurious update)."""
    return " OR ".join(
        f"NOT (target.{name} <=> source.{name})" for name in MATCHED_UPDATE_FIELDS
    )


def build_update_set() -> dict[str, str]:
    """UPDATE SET mapping: exactly the 8 approved metadata fields plus
    ``ingested_at = current_timestamp()``. No identity/content field is
    ever included here."""
    update_set = {name: f"source.{name}" for name in MATCHED_UPDATE_FIELDS}
    update_set["ingested_at"] = "current_timestamp()"
    return update_set


def build_insert_values() -> dict[str, str]:
    """INSERT column mapping: all 16 source fields, explicit and by name,
    plus ``ingested_at = current_timestamp()`` — never ``INSERT *``, never
    positional."""
    values = {name: f"source.{name}" for name in field_names(CHUNK_SOURCE_FIELDS)}
    values["ingested_at"] = "current_timestamp()"
    return values


def build_delete_condition(processed_document_ids: Sequence[str]) -> str:
    """WHEN NOT MATCHED BY SOURCE AND <this> — always scoped to
    ``processed_document_ids``. Raises rather than ever building an
    unscoped predicate: an empty ``processed_document_ids`` here would
    otherwise degrade to a malformed/ambiguous ``IN ()``, and this
    function refuses to produce that instead of guessing at its meaning.
    ``persist_chunks`` already guarantees non-emptiness via
    ``validate_chunks_for_persistence`` before this is ever called in
    practice — this is a second, independent guarantee at the expression
    level.
    """
    if not processed_document_ids:
        raise PersistenceTargetError(
            "build_delete_condition requires a non-empty processed_document_ids; "
            "an empty scope must never be used to build a stale-delete predicate."
        )
    literals = ", ".join(sql_string_literal(doc_id) for doc_id in processed_document_ids)
    return f"target.document_id IN ({literals})"
