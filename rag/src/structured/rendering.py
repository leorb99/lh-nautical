"""Deterministic rendering of structured results — Phase 6B.1.

Values that come from SQL are facts, so the user-facing text for them is
produced by plain code from the validated rows — never by a language model.
That guarantees every returned row (ties included), its rank, its product
name and its quantity reach the user exactly as the query produced them,
independent of any model behavior or token limit.

Pure and offline: no LLM, no I/O, no SQL. Only the natural-language
*framing* (a heading and a source line, in Portuguese like the rest of the
user-facing surface) is fixed text; every value is copied from the rows.
"""

from __future__ import annotations

from src.structured.models import StructuredIntent, StructuredResult, TopProductRow, UnsupportedIntentError

__all__ = ["format_quantity", "render_structured_answer"]

_SOURCE_LINE = "Fonte: dados de vendas efetivas."
_TIES_NOTE = "Produtos com a mesma quantidade vendida compartilham a mesma posição."


def format_quantity(value: int) -> str:
    """Integer with ``.`` as the thousands separator (Portuguese style) and
    the correct singular/plural noun. Formatting only — the numeric value is
    never changed."""
    grouped = f"{value:,}".replace(",", ".")
    return f"{grouped} {'unidade' if value == 1 else 'unidades'}"


def _render_top_products(rows: tuple[TopProductRow, ...]) -> str:
    lines = ["Os produtos mais vendidos, por quantidade de unidades, são:", ""]
    lines.extend(f"{row.rank}. {row.product_name} — {format_quantity(row.quantity_sold)}" for row in rows)
    lines.append("")
    if len({row.rank for row in rows}) < len(rows):
        lines.append(_TIES_NOTE)
    lines.append(_SOURCE_LINE)
    return "\n".join(lines)


def render_structured_answer(result: StructuredResult) -> str:
    """The final answer text for a non-empty :class:`StructuredResult`.

    Rows are emitted in the order received, one line each, with the rank the
    query assigned (tied products repeat their shared rank). Nothing is
    dropped, added, reordered or re-ranked.
    """
    if result.intent is StructuredIntent.TOP_PRODUCTS_BY_QUANTITY:
        return _render_top_products(result.rows)
    raise UnsupportedIntentError(f"no renderer for intent {result.intent!r}")
