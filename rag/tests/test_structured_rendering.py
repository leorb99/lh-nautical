"""Unit tests for src.structured.rendering — Phase 6B.1.

The ranked list a user sees is produced by plain code from the validated
rows; these tests assert on the FINAL text, not on a prompt, and no model is
involved anywhere in this module.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.structured.models import StructuredIntent, StructuredResult, TopProductRow, UnsupportedIntentError
from src.structured.rendering import format_quantity, render_structured_answer

TOP = StructuredIntent.TOP_PRODUCTS_BY_QUANTITY

ROWS = (
    TopProductRow(1, 456, "Colete Salva-Vidas 2374", 1737),
    TopProductRow(2, 77, "Bateria Náutica 1945", 1731),
    TopProductRow(3, 12, "Hélice Inox 316", 1700),
    TopProductRow(4, 13, "Âncora Galvanizada 9", 1699),
    TopProductRow(5, 14, "Bússola Marítima 11", 1690),
    TopProductRow(6, 15, "Cabo de Amarração 20m", 1680),
    TopProductRow(7, 16, "Boia de Sinalização 7", 1670),
    TopProductRow(8, 55, "GPS Plotter 6249", 1638),
    TopProductRow(8, 81, "Motor de Popa 7678", 1638),
    TopProductRow(9, 20, "Farol LED 5", 1600),
    TopProductRow(10, 21, "Remo Alumínio 3", 1500),
)


def _result(rows=ROWS, intent=TOP) -> StructuredResult:
    return StructuredResult(
        intent=intent,
        rows=tuple(rows),
        row_count=len(rows),
        source_tables=("gold.fct_sales", "gold.dim_products"),
        catalog="test_catalog",
        applied_filters={"top_n": 10},
    )


def _ranked_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.match(r"^\d+\. ", line)]


def test_format_quantity_groups_thousands_with_dots_and_never_changes_the_value():
    assert format_quantity(1737) == "1.737 unidades"
    assert format_quantity(1_234_567) == "1.234.567 unidades"
    assert format_quantity(999) == "999 unidades"
    assert format_quantity(1000) == "1.000 unidades"
    for value in (0, 7, 999, 1000, 1737, 1_234_567):
        assert int(format_quantity(value).split()[0].replace(".", "")) == value


def test_singular_and_plural_noun():
    assert format_quantity(1) == "1 unidade"
    assert format_quantity(2) == "2 unidades"


def test_every_row_is_rendered_with_its_exact_rank_name_and_quantity():
    lines = _ranked_lines(render_structured_answer(_result()))

    assert lines == [
        "1. Colete Salva-Vidas 2374 — 1.737 unidades",
        "2. Bateria Náutica 1945 — 1.731 unidades",
        "3. Hélice Inox 316 — 1.700 unidades",
        "4. Âncora Galvanizada 9 — 1.699 unidades",
        "5. Bússola Marítima 11 — 1.690 unidades",
        "6. Cabo de Amarração 20m — 1.680 unidades",
        "7. Boia de Sinalização 7 — 1.670 unidades",
        "8. GPS Plotter 6249 — 1.638 unidades",
        "8. Motor de Popa 7678 — 1.638 unidades",
        "9. Farol LED 5 — 1.600 unidades",
        "10. Remo Alumínio 3 — 1.500 unidades",
    ]


def test_tie_rows_are_both_kept_and_share_their_rank():
    text = render_structured_answer(_result())

    assert "8. GPS Plotter 6249 — 1.638 unidades" in text
    assert "8. Motor de Popa 7678 — 1.638 unidades" in text
    assert "9. Farol LED 5" in text  # dense rank: no gap after the tie
    assert "compartilham a mesma posição" in text


def test_no_tie_note_when_every_rank_is_distinct():
    assert "compartilham" not in render_structured_answer(_result(ROWS[:3]))


def test_order_of_the_rows_is_preserved_exactly():
    text = render_structured_answer(_result())

    positions = [text.index(row.product_name) for row in ROWS]
    assert positions == sorted(positions)


def test_no_row_is_omitted_and_none_is_invented():
    lines = _ranked_lines(render_structured_answer(_result()))

    assert len(lines) == len(ROWS)
    rendered_names = {line.split(". ", 1)[1].rsplit(" — ", 1)[0] for line in lines}
    assert rendered_names == {row.product_name for row in ROWS}


def test_a_maximum_size_result_is_rendered_completely():
    # top_n=50 (the service maximum) plus a tie at the last position: 51 rows.
    rows = [TopProductRow(i, 1000 + i, f"Produto {i:03d}", 10_000 - i) for i in range(1, 50)]
    rows += [TopProductRow(50, 2001, "Produto Empatado A", 5000), TopProductRow(50, 2002, "Produto Empatado B", 5000)]

    text = render_structured_answer(_result(rows))

    lines = _ranked_lines(text)
    assert len(lines) == 51
    assert lines[0] == "1. Produto 001 — 9.999 unidades"
    assert lines[-2:] == ["50. Produto Empatado A — 5.000 unidades", "50. Produto Empatado B — 5.000 unidades"]
    assert text.endswith("Fonte: dados de vendas efetivas.")  # the tail is never cut off


def test_product_names_are_copied_verbatim():
    rows = [TopProductRow(1, 1, "Motor {top_n} [S1] *bold* 100%", 5)]

    assert "1. Motor {top_n} [S1] *bold* 100% — 5 unidades" in render_structured_answer(_result(rows))


def test_text_frames_the_ranking_and_names_its_source_without_sql_details():
    text = render_structured_answer(_result())

    assert text.startswith("Os produtos mais vendidos, por quantidade de unidades, são:")
    assert text.rstrip().endswith("Fonte: dados de vendas efetivas.")
    for leaked in ("SELECT", "fct_sales", "dim_products", "test_catalog", "DENSE_RANK", "warehouse", "Product ID"):
        assert leaked not in text


def test_the_structured_answer_carries_no_documentary_citation_labels():
    assert re.search(r"\[S\d+\]", render_structured_answer(_result())) is None


def test_rendering_is_deterministic():
    assert render_structured_answer(_result()) == render_structured_answer(_result())


def test_an_intent_without_a_renderer_is_rejected():
    class _OtherIntent:
        value = "OTHER"

    result = _result()
    object.__setattr__(result, "intent", _OtherIntent())

    with pytest.raises(UnsupportedIntentError):
        render_structured_answer(result)


def test_module_imports_nothing_that_could_call_a_model_or_do_io():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / "structured" / "rendering.py").read_text(encoding="utf-8"))
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }

    assert imported == {"__future__", "src.structured.models"}
