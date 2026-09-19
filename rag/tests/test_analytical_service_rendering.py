"""Service normalization and deterministic rendering of analytical results.

The executor is always a fake that returns rows the way the SQL API does
(every value as text). Nothing touches a Warehouse; nothing calls a model.
The rendered text is what the user reads, so the tests assert on it
exactly: values, order, ties, number formatting, and that no citation label
is ever attached to a SQL value.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from src.structured.analytical_request import AnalyticalRequest, DateRange, RequestFilters
from src.structured.compiler import compile_request
from src.structured.models import StructuredExecutionError, StructuredIntent
from src.structured.rendering import render_structured_answer
from src.structured.semantic_model import METRICS, Dimension, Metric, Operation, TimeGrain, TimeModifier, Unit
from src.structured.service import MAX_RESULT_ROWS, StructuredQueryService

ANALYTICS = StructuredIntent.ANALYTICS
YEAR_2025 = DateRange(date(2025, 1, 1), date(2025, 12, 31))


class FakeExecutor:
    def __init__(self, rows=(), exc=None):
        self.rows, self.exc, self.calls = list(rows), exc, []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, dict(parameters or {})))
        if self.exc is not None:
            raise self.exc
        return [dict(row) for row in self.rows]


def _req(metric, operation=Operation.AGGREGATE, dimensions=(), **kwargs) -> AnalyticalRequest:
    return AnalyticalRequest(metric=metric, operation=operation, dimensions=tuple(dimensions), **kwargs)


def _run(request, rows):
    executor = FakeExecutor(rows)
    result = StructuredQueryService(executor, catalog="test_catalog", warehouse_id="wh-1").execute(ANALYTICS, {"request": request})
    return result, executor


def _answer(request, rows) -> str:
    result, _ = _run(request, rows)
    assert result.row_count > 0
    return render_structured_answer(result)


# ---------------------------------------------------------------------------
# service: compile -> execute the exact statement -> validated table
# ---------------------------------------------------------------------------


def test_the_service_executes_exactly_the_compiled_statement_with_its_parameters():
    request = _req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=5, filters=RequestFilters(date_range=YEAR_2025))
    result, executor = _run(request, [])

    expected = compile_request(request, catalog="test_catalog", schema="gold")
    assert executor.calls == [(expected.statement, dict(expected.parameters))]
    assert result.row_count == 0 and result.request == request
    assert result.applied_filters == dict(expected.applied_filters)
    assert result.source_tables == ("gold.fct_sales", "gold.dim_products")
    assert result.warehouse_id == "wh-1" and result.latency_s is not None


def test_ranking_rows_keep_rank_ties_order_and_exact_values():
    rows = [
        {"ranking": "1", "product_id": "456", "product_name": "Colete", "net_revenue": "1234567.89"},
        {"ranking": "2", "product_id": "77", "product_name": "Bateria", "net_revenue": "1000.10"},
        {"ranking": "2", "product_id": "81", "product_name": "Motor", "net_revenue": "1000.10"},
    ]
    result, _ = _run(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=2), rows)

    table = result.table
    assert [row[table.column_index("ranking")] for row in table.rows] == [1, 2, 2]
    assert [row[table.column_index("net_revenue")] for row in table.rows] == [Decimal("1234567.89"), Decimal("1000.10"), Decimal("1000.10")]
    assert result.row_count == 3  # more rows than top_n: the tie is kept


def test_an_aggregate_over_no_rows_is_no_data_not_a_value():
    result, _ = _run(_req(Metric.NET_REVENUE), [{"net_revenue": None}])
    assert result.row_count == 0

    zero, _ = _run(_req(Metric.EFFECTIVE_ORDERS), [{"effective_orders": "0"}])
    assert zero.row_count == 1  # a count of zero is a real answer


def test_missing_columns_wrong_types_and_unexpected_nulls_fail_closed():
    request = _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY])
    for rows, message in (
        ([{"category_name": "x"}], "missing column"),
        ([{"category_name": "x", "net_revenue": "abc"}], "decimal"),
        ([{"category_name": "x", "net_revenue": True}], "unexpected value"),
        ([{"category_name": "x", "net_revenue": 1.5}], "unexpected value"),  # never from a binary float
        ([{"category_name": 7, "net_revenue": "1"}], "string"),
    ):
        with pytest.raises(StructuredExecutionError, match=message):
            _run(request, rows)

    ranking = _req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT])
    for bad in (
        {"ranking": "1", "product_id": "1", "product_name": "", "quantity_sold": "5"},
        {"ranking": "1", "product_id": None, "product_name": "x", "quantity_sold": "5"},
        {"ranking": "1", "product_id": "1", "product_name": "x", "quantity_sold": "5.5"},
    ):
        with pytest.raises(StructuredExecutionError):
            _run(ranking, [bad])


def test_a_result_larger_than_the_cap_is_refused_not_truncated():
    rows = [{"category_name": f"c{i}", "net_revenue": "1"} for i in range(MAX_RESULT_ROWS + 1)]
    with pytest.raises(StructuredExecutionError, match="more than"):
        _run(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]), rows)


def test_executor_failures_become_one_controlled_error():
    service = StructuredQueryService(FakeExecutor(exc=RuntimeError("warehouse down")), catalog="c")
    with pytest.raises(StructuredExecutionError, match="executor failed"):
        service.execute(ANALYTICS, {"request": _req(Metric.NET_REVENUE)})


def test_the_service_never_accepts_sql():
    service = StructuredQueryService(FakeExecutor(), catalog="c")
    for intent, params in (
        (ANALYTICS, {"request": "SELECT 1"}),
        (ANALYTICS, {"sql": "SELECT 1"}),
        ("RAW_SQL", {}),
    ):
        with pytest.raises(Exception):
            service.execute(intent, params)


def test_the_legacy_intent_still_returns_typed_top_product_rows_and_the_table():
    rows = [{"ranking": "1", "product_id": "456", "product_name": "Colete", "quantity_sold": "1737"}]
    executor = FakeExecutor(rows)
    result = StructuredQueryService(executor, catalog="test_catalog").execute(StructuredIntent.TOP_PRODUCTS_BY_QUANTITY, {"top_n": 3})

    assert result.rows[0].product_name == "Colete" and result.rows[0].quantity_sold == 1737
    assert result.table is not None and result.request.metric is Metric.TOTAL_QUANTITY_SOLD


# ---------------------------------------------------------------------------
# rendering: friendly Portuguese, one shape per operation
# ---------------------------------------------------------------------------

SOURCE_SALES = "Fonte: camada Gold — fct_sales."
SOURCE_SALES_PRODUCTS = "Fonte: camada Gold — fct_sales + dim_products."
SOURCE_SALES_EMPLOYEES = "Fonte: camada Gold — fct_sales + dim_employees."


def test_aggregate_currency_is_a_natural_sentence_in_brazilian_reais():
    text = _answer(_req(Metric.NET_REVENUE), [{"net_revenue": "1234567.891"}])

    assert text == f"O faturamento líquido foi de R$ 1.234.567,89.\n\n{SOURCE_SALES}"


@pytest.mark.parametrize(
    "request_, row, sentence",
    [
        (_req(Metric.NET_REVENUE), {"net_revenue": "1234567.89"}, "O faturamento líquido foi de R$ 1.234.567,89."),
        (_req(Metric.EFFECTIVE_ORDERS), {"effective_orders": "12345"}, "Foram 12.345 pedidos efetivos."),
        (_req(Metric.EFFECTIVE_ORDERS), {"effective_orders": "1"}, "Foi 1 pedido efetivo."),
        (
            _req(Metric.AVERAGE_TICKET),
            {"average_ticket_numerator": "4321", "average_ticket_denominator": "10", "average_ticket": "432.1"},
            "O ticket médio foi de R$ 432,10.",
        ),
        (_req(Metric.TOTAL_QUANTITY_SOLD), {"quantity_sold": "12345"}, "Foram vendidas 12.345 unidades."),
        (_req(Metric.TOTAL_QUANTITY_SOLD), {"quantity_sold": "1"}, "Foi vendida 1 unidade."),
        (
            _req(Metric.RETURN_RATE),
            {"return_rate_numerator": "432", "return_rate_denominator": "10000", "return_rate": "0.0432"},
            "A taxa de devolução foi de 4,32% (432 de 10.000 unidades).",
        ),
        (_req(Metric.RETURN_AMOUNT), {"refunded_amount": "999.5"}, "O valor reembolsado foi de R$ 999,50."),
        (_req(Metric.STOCK_ON_HAND), {"stock_on_hand": "1500"}, "O estoque atual é de 1.500 unidades."),
        (_req(Metric.AT_RISK_CUSTOMERS), {"at_risk_customers": "420"}, "Há 420 clientes em risco."),
        (_req(Metric.AT_RISK_CUSTOMERS), {"at_risk_customers": "1"}, "Há 1 cliente em risco."),
        (_req(Metric.CUSTOMER_COUNT), {"customer_count": "12000"}, "Há 12.000 clientes."),
        (_req(Metric.AT_RISK_REVENUE), {"at_risk_revenue": "999999.9"}, "A receita em risco (churn) é de R$ 999.999,90."),
        (_req(Metric.AVERAGE_RECENCY), {"average_recency": "12.34"}, "A recência média é de 12,3 dias."),
        (
            _req(Metric.STOCKOUT_RISK),
            {"stockout_risk_numerator": "12", "stockout_risk_denominator": "100", "stockout_risk": "0.12"},
            "A parcela de itens em risco de ruptura é de 12,00%.",
        ),
    ],
)
def test_every_metric_reads_as_a_sentence_built_from_its_registry_metadata(request_, row, sentence):
    text = _answer(request_, [row])

    assert text.splitlines()[0] == sentence
    assert text.splitlines()[-1].startswith("Fonte: camada Gold — ") and "\n\nFonte:" in text


def test_the_period_and_filters_are_part_of_the_sentence():
    march = DateRange(date(2025, 3, 1), date(2025, 3, 31))
    custom = DateRange(date(2025, 3, 5), date(2025, 4, 20))

    assert _answer(_req(Metric.NET_REVENUE, filters=RequestFilters(date_range=YEAR_2025)), [{"net_revenue": "10"}]).startswith(
        "O faturamento líquido em 2025 foi de R$ 10,00."
    )
    assert _answer(_req(Metric.NET_REVENUE, filters=RequestFilters(date_range=march)), [{"net_revenue": "10"}]).startswith(
        "O faturamento líquido em março de 2025 foi de R$ 10,00."
    )
    assert _answer(_req(Metric.EFFECTIVE_ORDERS, filters=RequestFilters(date_range=custom)), [{"effective_orders": "3"}]).startswith(
        "Foram 3 pedidos efetivos entre 05/03/2025 e 20/04/2025."
    )
    with_category = _req(Metric.NET_REVENUE, filters=RequestFilters(categories=("Motores",)))
    assert _answer(with_category, [{"net_revenue": "10"}]).startswith("O faturamento líquido na categoria Motores foi de R$ 10,00.")
    critical = _req(Metric.INVENTORY_ITEMS, filters=RequestFilters(inventory_health_statuses=("critical", "out_of_stock")))
    assert _answer(critical, [{"inventory_items": "7"}]).startswith("Há 7 itens em estoque com status crítico e fora de estoque.")


def test_brl_formatting_uses_dots_for_thousands_comma_for_cents_and_rounds_half_up():
    def money(raw):
        return _answer(_req(Metric.NET_REVENUE), [{"net_revenue": raw}]).splitlines()[0]

    assert money("0") == "O faturamento líquido foi de R$ 0,00."
    assert money("0.005") == "O faturamento líquido foi de R$ 0,01."
    assert money("1234.5") == "O faturamento líquido foi de R$ 1.234,50."
    assert money("1000000") == "O faturamento líquido foi de R$ 1.000.000,00."
    assert money("-1234.567") == "O faturamento líquido foi de R$ -1.234,57."


def test_integer_formatting_uses_dots_and_never_changes_the_value():
    def orders(raw):
        return _answer(_req(Metric.EFFECTIVE_ORDERS), [{"effective_orders": raw}]).splitlines()[0]

    assert orders("0") == "Foram 0 pedidos efetivos."
    assert orders("999") == "Foram 999 pedidos efetivos."
    assert orders("1000") == "Foram 1.000 pedidos efetivos."
    assert orders("1234567") == "Foram 1.234.567 pedidos efetivos."


def test_percentage_formatting_uses_a_comma_and_two_decimals():
    def rate(ratio, num="1", den="8"):
        row = {"return_rate_numerator": num, "return_rate_denominator": den, "return_rate": ratio}
        return _answer(_req(Metric.RETURN_RATE), [row]).splitlines()[0]

    assert rate("0.0432", "432", "10000").startswith("A taxa de devolução foi de 4,32% ")
    assert rate("0.00125").startswith("A taxa de devolução foi de 0,13% ")  # half-up, deterministic
    assert rate("1", "8", "8").startswith("A taxa de devolução foi de 100,00% ")
    assert rate("0.5", "1", "2").startswith("A taxa de devolução foi de 50,00% ")


def test_the_default_decimal_format_has_two_places():
    from src.structured.rendering import _format_value
    from src.structured.semantic_model import METRICS, Unit
    import dataclasses

    decimal_metric = dataclasses.replace(METRICS[Metric.AVERAGE_RECENCY], unit=Unit.DECIMAL)
    assert _format_value(Decimal("1234.5"), decimal_metric) == "1.234,50"


def test_year_to_date_and_ltm_state_the_end_of_the_period_they_were_computed_up_to():
    march = DateRange(date(2025, 3, 1), date(2025, 3, 31))
    rows = [{"net_revenue": "5000"}]
    ytd = _answer(_req(Metric.NET_REVENUE, time_modifier=TimeModifier.YTD, filters=RequestFilters(date_range=march)), rows)
    ltm = _answer(_req(Metric.NET_REVENUE, time_modifier=TimeModifier.LTM, filters=RequestFilters(date_range=YEAR_2025)), rows)

    assert ytd == f"O faturamento líquido acumulado no ano até 31/03/2025 foi de R$ 5.000,00.\n\n{SOURCE_SALES}"
    assert ltm.startswith("O faturamento líquido nos últimos 12 meses até 31/12/2025 foi de R$ 5.000,00.")


def test_year_over_year_is_one_sentence_with_both_periods_and_the_signed_change():
    request = _req(Metric.NET_REVENUE, time_modifier=TimeModifier.YOY, filters=RequestFilters(date_range=YEAR_2025))
    up = _answer(request, [{"net_revenue": "1100", "net_revenue_previous": "1000", "net_revenue_yoy": "0.1"}])
    down = _answer(request, [{"net_revenue": "900", "net_revenue_previous": "1000", "net_revenue_yoy": "-0.1"}])
    no_base = _answer(request, [{"net_revenue": "900", "net_revenue_previous": None, "net_revenue_yoy": None}])

    assert up.splitlines()[0] == (
        "O faturamento líquido em 2025 foi de R$ 1.100,00, contra R$ 1.000,00 no mesmo período do ano anterior (variação de +10,00%)."
    )
    assert "(variação de -10,00%)" in down
    assert "sem base de comparação" in no_base


# -- ranking ---------------------------------------------------------------


def test_ranking_has_a_natural_title_and_numbered_lines():
    rows = [
        {"ranking": "1", "product_id": "1", "product_name": "Produto A", "net_revenue": "100000"},
        {"ranking": "2", "product_id": "2", "product_name": "Produto B", "net_revenue": "95000"},
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=2), rows)

    assert text == (
        "Os 2 produtos com maior receita líquida foram:\n\n"
        "1. Produto A — R$ 100.000,00\n2. Produto B — R$ 95.000,00\n\n"
        f"{SOURCE_SALES_PRODUCTS}"
    )


def test_ranking_titles_use_the_registry_metadata_and_agree_in_number_and_gender():
    one = [{"ranking": "1", "product_id": "1", "product_name": "Colete", "net_revenue": "1500000.50"}]
    assert _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=1), one).startswith(
        "O produto com maior receita líquida foi:\n\n1. Colete — R$ 1.500.000,50"
    )
    units = [{"ranking": "1", "product_id": "9", "product_name": "Motor", "quantity_sold": "1737"}]
    assert _answer(_req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT], top_n=1), units).startswith(
        "O produto com mais unidades vendidas foi:\n\n1. Motor — 1.737 unidades"
    )
    categories = [
        {"ranking": "1", "category_name": "Coletes", "net_revenue": "5"},
        {"ranking": "2", "category_name": "Motores", "net_revenue": "3"},
    ]
    assert _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.CATEGORY], top_n=2), categories).startswith(
        "As 2 categorias com maior receita líquida foram:"
    )


def test_the_ranking_title_states_the_period_and_never_claims_more_positions_than_exist():
    rows = [{"ranking": "1", "product_id": "1", "product_name": "Colete", "quantity_sold": "5"}]
    text = _answer(
        _req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT], top_n=5, filters=RequestFilters(date_range=YEAR_2025)), rows
    )

    assert text.startswith("O produto com mais unidades vendidas em 2025 foi:")  # 1 shown, not "Os 5"


def test_ranking_keeps_ties_with_their_shared_rank_and_says_so():
    rows = [
        {"ranking": "1", "product_id": "456", "product_name": "Colete", "quantity_sold": "1737"},
        {"ranking": "8", "product_id": "55", "product_name": "GPS", "quantity_sold": "1638"},
        {"ranking": "8", "product_id": "81", "product_name": "Motor", "quantity_sold": "1638"},
    ]
    text = _answer(_req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT]), rows)

    assert text == (
        "Os 3 produtos com mais unidades vendidas foram:\n\n"
        "1. Colete — 1.737 unidades\n8. GPS — 1.638 unidades\n8. Motor — 1.638 unidades\n\n"
        "Produtos empatados compartilham a mesma posição.\n\n"
        f"{SOURCE_SALES_PRODUCTS}"
    )


def test_a_tie_can_make_the_list_longer_than_asked_and_the_text_says_why():
    rows = [
        {"ranking": "1", "product_id": "1", "product_name": "Produto A", "net_revenue": "100"},
        {"ranking": "1", "product_id": "3", "product_name": "Produto C", "net_revenue": "100"},
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=1), rows)

    assert "1. Produto A — R$ 100,00\n1. Produto C — R$ 100,00" in text
    assert "Produtos empatados compartilham a mesma posição, por isso a lista tem mais itens do que o pedido." in text


def test_tied_categories_use_the_feminine_form():
    rows = [
        {"ranking": "1", "category_name": "Coletes", "net_revenue": "5"},
        {"ranking": "1", "category_name": "Motores", "net_revenue": "5"},
        {"ranking": "3", "category_name": "Boias", "net_revenue": "1"},
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.CATEGORY], top_n=3), rows)

    assert "Categorias empatadas compartilham a mesma posição." in text and "3. Boias" in text


def test_the_salesperson_ranking_states_which_employees_it_considers_and_skips_after_ties():
    rows = [
        {"ranking": "1", "salesperson": "Ana Souza", "net_revenue": "900"},
        {"ranking": "1", "salesperson": "Bruno Lima", "net_revenue": "900"},
        {"ranking": "3", "salesperson": "Carla Dias", "net_revenue": "500"},
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.SALESPERSON], top_n=3), rows)

    assert text == (
        "Os 3 vendedores com maior receita líquida foram:\n\n"
        "1. Ana Souza — R$ 900,00\n1. Bruno Lima — R$ 900,00\n3. Carla Dias — R$ 500,00\n\n"
        "Vendedores empatados compartilham a mesma posição.\n\n"
        "Considera apenas funcionários com cargo Salesperson.\n"
        f"{SOURCE_SALES_EMPLOYEES}"
    )  # RANK(): ties skip the next position, like the default RANKX


def test_days_to_stockout_ranks_the_soonest_first_with_one_decimal():
    rows = [
        {"ranking": "1", "product_id": "9", "product_name": "Farol", "days_to_stockout": "3.25"},
        {"ranking": "2", "product_id": "8", "product_name": "Boia", "days_to_stockout": "10"},
    ]
    text = _answer(_req(Metric.DAYS_TO_STOCKOUT, Operation.RANKING, [Dimension.PRODUCT], top_n=2), rows)

    assert text.startswith("Os 2 produtos com menos dias até a ruptura foram:")
    assert "1. Farol — 3,3 dias" in text and "2. Boia — 10,0 dias" in text


def test_a_fifty_row_ranking_is_rendered_completely():
    rows = [
        {"ranking": str(i), "product_id": str(i), "product_name": f"Produto {i:02d}", "net_revenue": str(100000 - i)} for i in range(1, 51)
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=50), rows)

    lines = [line for line in text.splitlines() if re.match(r"^\d+\. ", line)]
    assert text.startswith("Os 50 produtos com maior receita líquida foram:")
    assert len(lines) == 50 and lines[0].startswith("1. Produto 01") and lines[-1].startswith("50. Produto 50")
    assert text.endswith(SOURCE_SALES_PRODUCTS)


# -- breakdown -------------------------------------------------------------


def test_breakdown_has_a_short_introduction_and_one_line_per_group_in_the_order_returned():
    rows = [
        {"category_name": "Coletes", "net_revenue": "300"},
        {"category_name": "Motores", "net_revenue": "200.5"},
        {"category_name": None, "net_revenue": "1"},
    ]
    text = _answer(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]), rows)

    assert text == (
        "O faturamento líquido por categoria foi:\n\n"
        "- Coletes — R$ 300,00\n- Motores — R$ 200,50\n- (não informado) — R$ 1,00\n\n"
        f"{SOURCE_SALES_PRODUCTS}"
    )


def test_breakdown_ratios_show_their_counts_and_a_period_when_given():
    rows = [{"category_name": "Coletes", "return_rate_numerator": "5", "return_rate_denominator": "100", "return_rate": "0.05"}]
    text = _answer(_req(Metric.RETURN_RATE, Operation.BREAKDOWN, [Dimension.CATEGORY], filters=RequestFilters(date_range=YEAR_2025)), rows)

    assert text.startswith("Em 2025, a taxa de devolução por categoria foi:\n\n- Coletes — 5,00% (5 de 100 unidades)")


def test_inventory_health_breakdown_uses_readable_status_names():
    rows = [
        {"inventory_health_status": "out_of_stock", "inventory_items": "5"},
        {"inventory_health_status": "critical", "inventory_items": "12"},
        {"inventory_health_status": "no_recent_sales", "inventory_items": "1300"},
    ]
    text = _answer(_req(Metric.INVENTORY_ITEMS, Operation.BREAKDOWN, [Dimension.INVENTORY_HEALTH_STATUS]), rows)

    assert text.startswith("Os itens em estoque por status de saúde do estoque são:")
    assert "- Fora de estoque — 5\n- Crítico — 12\n- Sem vendas recentes — 1.300" in text


# -- trend -----------------------------------------------------------------


def test_trend_is_chronological_with_readable_period_labels():
    rows = [
        {"period": "2025-03-01", "net_revenue": "30"},
        {"period": "2025-01-01T00:00:00", "net_revenue": "10"},
        {"period": "2025-02-01", "net_revenue": "20"},
    ]  # deliberately out of order
    text = _answer(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.MONTH), rows)

    assert text == (
        "De janeiro a março de 2025, o faturamento líquido mensal foi:\n\n"
        "- jan/2025 — R$ 10,00\n- fev/2025 — R$ 20,00\n- mar/2025 — R$ 30,00\n\n"
        f"{SOURCE_SALES}"
    )


def test_trend_labels_follow_the_grain():
    rows = [{"period": "2025-01-01", "net_revenue": "10"}, {"period": "2025-04-01", "net_revenue": "20"}]
    years = [{"period": "2024-01-01", "net_revenue": "10"}, {"period": "2025-01-01", "net_revenue": "20"}]
    quarterly = _answer(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.QUARTER), rows)
    yearly = _answer(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.YEAR), years)
    daily = _answer(
        _req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.DAY, filters=RequestFilters(date_range=YEAR_2025)), rows
    )

    assert "- 1º tri/2025 — R$ 10,00\n- 2º tri/2025 — R$ 20,00" in quarterly
    assert yearly.startswith("De 2024 a 2025, o faturamento líquido anual foi:") and "- 2025 — R$ 20,00" in yearly
    assert daily.startswith("De 01/01/2025 a 01/04/2025, o faturamento líquido diário foi:") and "- 01/04/2025 — R$ 20,00" in daily


# ---------------------------------------------------------------------------
# rendering guarantees
# ---------------------------------------------------------------------------

INTERNAL_TOKENS = re.compile(
    r"AGGREGATE|RANKING|BREAKDOWN|TREND|[A-Z]+_[A-Z_]+|·|SELECT|structured_|analytical|AnalyticalRequest|STRUCTURED|\bYTD\b|\bLTM\b|\bYOY\b"
)


def _synthetic_rows(columns):
    """One plausible row per group for a compiled query's result columns."""
    def value(column):
        if column.role.value == "RANK":
            return "1"
        if column.type.value == "INT":
            return "7"
        if column.type.value == "DECIMAL":
            return "0.5" if column.role.value in ("METRIC", "PREVIOUS", "CHANGE") else "10"
        if column.type.value == "DATE":
            return "2025-01-01"
        return "Nome"

    return [{column.name: value(column) for column in columns}]


def test_the_answer_is_never_the_internal_semantic_request():
    request = _req(Metric.NET_REVENUE)
    text = _answer(request, [{"net_revenue": "10"}])

    assert text != "AGGREGATE · NET_REVENUE"
    assert "AGGREGATE" not in text and "NET_REVENUE" not in text and "·" not in text


def test_no_valid_request_ever_renders_internal_identifiers():
    """Every valid metric/operation/dimension combination in the registry,
    rendered from fake rows: the answer is Portuguese text, never a metric id,
    operation name, enum, SQL fragment or debug string."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("compiler_cases", Path(__file__).with_name("test_analytical_compiler.py"))
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)

    rendered = 0
    for request in cases.ALL_REQUESTS:
        columns = compile_request(request, catalog="c", schema="gold").columns
        result, _ = _run(request, _synthetic_rows(columns))
        if result.row_count == 0:
            continue
        text = render_structured_answer(result)
        assert text.strip() and not INTERNAL_TOKENS.search(text), (request, text)
        assert "\n\nFonte:" in text or "Fonte:" in text
        rendered += 1
    assert rendered > 140


def test_structured_answers_never_carry_documentary_citation_labels():
    rows = [{"ranking": "1", "product_id": "1", "product_name": "Colete", "net_revenue": "1"}]
    text = _answer(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=1), rows)

    assert re.search(r"\[S\d+\]", text) is None


def test_rendering_reproduces_the_returned_numbers_exactly():
    for raw in ("0.01", "12.5", "999999999.99", "1234.56"):
        text = _answer(_req(Metric.NET_REVENUE), [{"net_revenue": raw}])
        shown = re.search(r"R\$ ([\d.]+,\d{2})", text).group(1).replace(".", "").replace(",", ".")
        assert Decimal(shown) == Decimal(raw).quantize(Decimal("0.01"))


def test_rendering_is_deterministic_and_has_no_model_or_io():
    rows = [{"category_name": "Coletes", "net_revenue": "300"}]
    result, _ = _run(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]), rows)

    assert render_structured_answer(result) == render_structured_answer(result)


# ---------------------------------------------------------------------------
# source line: the Gold tables the query really read; STOCKOUT_RISK without counts
# ---------------------------------------------------------------------------


def _source_line(text: str) -> str:
    return text.splitlines()[-1]


@pytest.mark.parametrize(
    "request_, row, source",
    [
        (_req(Metric.NET_REVENUE), {"net_revenue": "10"}, "Fonte: camada Gold — fct_sales."),
        (
            _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]),
            {"category_name": "Coletes", "net_revenue": "10"},
            "Fonte: camada Gold — fct_sales + dim_products.",
        ),
        (
            _req(Metric.STOCKOUT_RISK, Operation.RANKING, [Dimension.PRODUCT], top_n=1),
            {"ranking": "1", "product_id": "1", "product_name": "Colete", "stockout_risk_numerator": "1",
             "stockout_risk_denominator": "8", "stockout_risk": "0.125"},
            "Fonte: camada Gold — fct_inventory_replenishment + dim_products.",
        ),
        (_req(Metric.INVENTORY_ITEMS), {"inventory_items": "5"}, "Fonte: camada Gold — fct_current_inventory."),
    ],
)
def test_the_source_line_lists_only_the_tables_the_query_used(request_, row, source):
    result, _ = _run(request_, [row])
    text = render_structured_answer(result)

    assert _source_line(text) == source
    assert [name.split(".")[-1] for name in result.source_tables] == source.removeprefix("Fonte: camada Gold — ").rstrip(".").split(" + ")
    assert "test_catalog" not in text and "gold." not in text  # never the environment's catalog or schema
    assert "[S" not in text  # not a documentary citation


def test_the_source_line_follows_the_result_tables_and_never_repeats_one():
    result, _ = _run(_req(Metric.NET_REVENUE), [{"net_revenue": "10"}])
    from dataclasses import replace

    tables = ("prod_catalog.gold.fct_sales", "gold.dim_products", "gold.fct_sales")
    text = render_structured_answer(replace(result, source_tables=tables))

    assert _source_line(text) == "Fonte: camada Gold — fct_sales + dim_products."
    assert "prod_catalog" not in text


def test_replenishment_ratios_show_the_main_value_only_until_the_replenishment_grain_is_validated():
    """STOCKOUT_RISK and REPLENISHMENT_REQUIRED are percentages of fct_inventory_replenishment
    rows; "N de M itens monitorados" is not shown until what a row is has been validated."""
    def row(metric):
        alias = METRICS[metric].value_alias
        return {f"{alias}_numerator": "120", f"{alias}_denominator": "1000", alias: "0.12"}

    for metric, sentence, title in (
        (Metric.STOCKOUT_RISK, "A parcela de itens em risco de ruptura é de 12,00%.", "O produto com maior risco de ruptura foi:"),
        (Metric.REPLENISHMENT_REQUIRED, "A parcela de itens com necessidade de reposição é de 12,00%.",
         "O produto com maior necessidade de reposição foi:"),
    ):
        aggregate = _answer(_req(metric), [row(metric)])
        ranking = _answer(
            _req(metric, Operation.RANKING, [Dimension.PRODUCT], top_n=1),
            [{"ranking": "1", "product_id": "1", "product_name": "Colete Salva-Vidas Pro", **row(metric)}],
        )
        breakdown = _answer(
            _req(metric, Operation.BREAKDOWN, [Dimension.CATEGORY]),
            [{"category_name": "Coletes", **row(metric)}],
        )

        assert aggregate.splitlines()[0] == sentence
        assert ranking == (
            f"{title}\n\n1. Colete Salva-Vidas Pro — 12,00%\n\n"
            "Fonte: camada Gold — fct_inventory_replenishment + dim_products."
        )
        assert "- Coletes — 12,00%\n" in breakdown
        for text in (aggregate, ranking, breakdown):
            assert "monitorados" not in text and " de 1.000" not in text and "120" not in text


def test_other_ratios_still_show_their_counts():
    text = _answer(
        _req(Metric.RETURN_RATE),
        [{"return_rate_numerator": "432", "return_rate_denominator": "10000", "return_rate": "0.0432"}],
    )

    assert text.splitlines()[0] == "A taxa de devolução foi de 4,32% (432 de 10.000 unidades)."


# ---------------------------------------------------------------------------
# introductions: built from the registry's presentation metadata, not per question
# ---------------------------------------------------------------------------


def _trend_rows(metric, periods):
    """Rows for a trend of ``metric`` (whatever its columns), one per period."""
    definition = METRICS[metric]
    alias = definition.value_alias
    cells = {alias: "0.5" if definition.unit is Unit.PERCENT else "10"}
    if definition.numerator or definition.order_level:  # ratios and the order-level average
        cells |= {f"{alias}_numerator": "5", f"{alias}_denominator": "10"}
    return [{"period": period, **cells} for period in periods]


def _intro(metric, periods, grain, **kwargs):
    request = _req(metric, Operation.TREND, time_grain=grain, **kwargs)
    return _answer(request, _trend_rows(metric, periods)).splitlines()[0]


MONTHS_2025 = [f"2025-{m:02d}-01" for m in range(1, 13)]


@pytest.mark.parametrize(
    "periods, intro",
    [
        (MONTHS_2025, "Ao longo de 2025, o faturamento líquido mensal foi:"),  # jan-dez, same year
        (["2026-01-01", "2026-02-01", "2026-08-01"], "De janeiro a agosto de 2026, o faturamento líquido mensal foi:"),  # partial
        (["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"], "De novembro de 2025 a fevereiro de 2026, o faturamento líquido mensal foi:"),
        (["2026-01-01"], "Em janeiro de 2026, o faturamento líquido foi:"),  # a single month
    ],
)
def test_a_monthly_trend_is_introduced_by_the_months_actually_returned(periods, intro):
    assert _intro(Metric.NET_REVENUE, periods, TimeGrain.MONTH) == intro


def test_the_introduction_describes_what_was_returned_not_what_was_asked():
    # jan-dez 2025 requested, but sales only exist from March: the text must not claim the whole year.
    request = _req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.MONTH, filters=RequestFilters(date_range=YEAR_2025))
    text = _answer(request, _trend_rows(Metric.NET_REVENUE, MONTHS_2025[2:]))

    assert text.splitlines()[0] == "De março a dezembro de 2025, o faturamento líquido mensal foi:"


@pytest.mark.parametrize(
    "grain, periods, intro",
    [
        (TimeGrain.QUARTER, ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"], "Ao longo de 2025, o faturamento líquido trimestral foi:"),
        (TimeGrain.QUARTER, ["2025-01-01", "2025-04-01"], "Do 1º ao 2º trimestre de 2025, o faturamento líquido trimestral foi:"),
        (TimeGrain.QUARTER, ["2025-10-01", "2026-01-01"], "Do 4º trimestre de 2025 ao 1º trimestre de 2026, o faturamento líquido trimestral foi:"),
        (TimeGrain.QUARTER, ["2026-04-01"], "No 2º trimestre de 2026, o faturamento líquido foi:"),
        (TimeGrain.YEAR, ["2023-01-01", "2024-01-01", "2025-01-01"], "De 2023 a 2025, o faturamento líquido anual foi:"),
        (TimeGrain.YEAR, ["2025-01-01"], "Em 2025, o faturamento líquido foi:"),
        (TimeGrain.DAY, ["2026-03-01", "2026-03-15", "2026-03-31"], "Em março de 2026, o faturamento líquido diário foi:"),
        (TimeGrain.DAY, ["2026-03-05", "2026-03-20"], "De 05/03/2026 a 20/03/2026, o faturamento líquido diário foi:"),
        (TimeGrain.DAY, ["2026-03-05"], "Em 05/03/2026, o faturamento líquido foi:"),
    ],
)
def test_other_grains_follow_the_same_rules(grain, periods, intro):
    filters = RequestFilters(date_range=DateRange(date(2026, 1, 1), date(2026, 12, 31)))
    assert _intro(Metric.NET_REVENUE, periods, grain, filters=filters if grain is TimeGrain.DAY else RequestFilters()) == intro


@pytest.mark.parametrize("metric", [m for m, d in METRICS.items() if Operation.TREND in d.operations])
def test_every_trend_metric_gets_an_agreeing_introduction_from_its_metadata(metric):
    definition = METRICS[metric]
    plural = definition.display_pt.split()[0] in ("os", "as")
    feminine = definition.display_pt.split()[0] in ("a", "as")
    monthly = {(False, False): "mensal", (True, False): "mensal", (False, True): "mensais", (True, True): "mensais"}[(feminine, plural)]

    multi = _intro(metric, ["2025-01-01", "2025-02-01"], TimeGrain.MONTH)
    single = _intro(metric, ["2025-01-01"], TimeGrain.MONTH)
    daily = _intro(metric, ["2025-01-05", "2025-01-06"], TimeGrain.DAY, filters=RequestFilters(date_range=YEAR_2025))

    verb = "foram" if plural else "foi"
    assert multi == f"De janeiro a fevereiro de 2025, {definition.display_pt} {monthly} {verb}:"
    assert single == f"Em janeiro de 2025, {definition.display_pt} {verb}:"
    assert daily == f"De 05/01/2025 a 06/01/2025, {definition.display_pt} {'diárias' if feminine and plural else 'diários' if plural else 'diária' if feminine else 'diário'} {verb}:"
    assert "Veja" not in multi + single + daily


def test_the_trend_introduction_keeps_the_filters_of_the_question():
    request = _req(
        Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.MONTH, filters=RequestFilters(categories=("Motores",))
    )
    text = _answer(request, _trend_rows(Metric.NET_REVENUE, MONTHS_2025))

    assert text.splitlines()[0] == "Ao longo de 2025, o faturamento líquido mensal na categoria Motores foi:"


@pytest.mark.parametrize(
    "request_, rows, intro",
    [
        (
            _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]),
            [{"category_name": "Coletes", "net_revenue": "1"}],
            "O faturamento líquido por categoria foi:",
        ),
        (
            _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY], filters=RequestFilters(date_range=YEAR_2025)),
            [{"category_name": "Coletes", "net_revenue": "1"}],
            "Em 2025, o faturamento líquido por categoria foi:",
        ),
        (
            _req(
                Metric.EFFECTIVE_ORDERS, Operation.BREAKDOWN, [Dimension.CATEGORY],
                filters=RequestFilters(date_range=DateRange(date(2026, 1, 1), date(2026, 1, 31))),
            ),
            [{"category_name": "Coletes", "effective_orders": "3"}],
            "Em janeiro de 2026, os pedidos efetivos por categoria foram:",
        ),
        (
            _req(
                Metric.TOTAL_QUANTITY_SOLD, Operation.BREAKDOWN, [Dimension.CATEGORY],
                filters=RequestFilters(date_range=DateRange(date(2026, 3, 1), date(2026, 3, 15))),
            ),
            [{"category_name": "Coletes", "quantity_sold": "3"}],
            "Entre 01/03/2026 e 15/03/2026, as unidades vendidas por categoria foram:",
        ),
        (
            _req(Metric.STOCK_ON_HAND, Operation.BREAKDOWN, [Dimension.CATEGORY]),
            [{"category_name": "Coletes", "stock_on_hand": "3"}],
            "O estoque atual por categoria é:",  # a snapshot is stated in the present
        ),
    ],
)
def test_breakdown_introductions_carry_metric_dimension_and_period(request_, rows, intro):
    assert _answer(request_, rows).splitlines()[0] == intro
