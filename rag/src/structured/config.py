"""Structured-data configuration — Phase 6B.1.

Follows the same portable pattern as ``rag/app.py``: values come from the
process environment (populated by the Bundle in a real deployment), with
no hardcoded personal catalog and no ``prod`` default.

- ``DATA_CATALOG``: falls back to the RAG catalog the caller already
  resolved from the Bundle's ``catalog_name`` variable.
- ``DATA_SCHEMA``: defaults to ``gold`` (constant across every target).
- ``SQL_WAREHOUSE_ID``: no default. Without it the structured layer is
  simply not enabled, and structured questions get a controlled
  "unavailable" outcome instead of a wrong or documentation-only answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.structured.service import DEFAULT_SCHEMA

__all__ = ["StructuredDataConfig"]


@dataclass(frozen=True)
class StructuredDataConfig:
    catalog: str
    schema: str
    warehouse_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, default_catalog: str) -> "StructuredDataConfig | None":
        """Return the config, or ``None`` when no SQL Warehouse is
        configured (structured data disabled)."""
        warehouse_id = (env.get("SQL_WAREHOUSE_ID") or "").strip()
        if not warehouse_id:
            return None
        return cls(
            catalog=(env.get("DATA_CATALOG") or "").strip() or default_catalog,
            schema=(env.get("DATA_SCHEMA") or "").strip() or DEFAULT_SCHEMA,
            warehouse_id=warehouse_id,
        )
