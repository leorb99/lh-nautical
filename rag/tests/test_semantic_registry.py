"""Registry tests: the metric / dimension registry is the semantic contract.

Power BI = semantic contract, Gold = runtime data source. These tests check
the registry against BOTH, offline:

- against the dbt Gold YAML in this repository: every table and every
  ``alias.column`` used by a binding, join, metric or filter must exist there
  (nothing is registered that Gold does not have);
- against the Power BI semantic document: every metric names the Power BI
  measure it mirrors, and that measure exists in the document.

No Databricks, no SQL, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.structured.semantic_model import (
    BLOCKED_ATTRIBUTES,
    CUSTOMER_SEGMENTS,
    DIMENSIONS,
    FACTS,
    INVENTORY_HEALTH_STATUSES,
    METRICS,
    Dimension,
    FilterKind,
    Metric,
    MetricFamily,
    Operation,
    SortOrder,
    TimeModifier,
    Unit,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MARTS = REPO_ROOT / "dbt_lh_nautical" / "models" / "marts"
PBI_DOC = Path(__file__).resolve().parents[1] / "knowledge_base" / "powerbi_semantic_rules.md"


def _gold_columns() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for path in MARTS.rglob("*.yml"):
        for model in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models", []) or []:
            tables[model["name"]] = {column["name"] for column in model.get("columns", []) or []}
    return tables


GOLD = _gold_columns()

P0_METRICS = {
    # sales
    Metric.NET_REVENUE, Metric.TOTAL_QUANTITY_SOLD, Metric.EFFECTIVE_ORDERS, Metric.AVERAGE_TICKET,
    # returns
    Metric.RETURN_AMOUNT, Metric.RETURN_RATE,
    # inventory
    Metric.STOCK_ON_HAND, Metric.DAYS_TO_STOCKOUT, Metric.REPLENISHMENT_REQUIRED, Metric.STOCKOUT_RISK,
    Metric.INVENTORY_ITEMS,
    # customer / RFM
    Metric.CUSTOMER_COUNT, Metric.AT_RISK_CUSTOMERS, Metric.AT_RISK_REVENUE,
}  # fmt: skip


def _fragments(metric_def) -> list[str]:
    return [
        text
        for text in (
            metric_def.aggregate,
            metric_def.numerator,
            metric_def.denominator,
            *metric_def.metric_predicates,
        )
        if text
    ]


def _column_references(fragment: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\b([a-z])\.([a-z_][a-z0-9_]*)\b", fragment))


# ---------------------------------------------------------------------------
# completeness / shape
# ---------------------------------------------------------------------------


def test_every_metric_and_dimension_has_a_definition():
    assert set(METRICS) == set(Metric)
    assert set(DIMENSIONS) == set(Dimension)


def test_all_p0_metrics_are_registered():
    assert P0_METRICS <= set(METRICS)


def test_every_metric_names_the_power_bi_measure_it_mirrors_and_that_measure_exists():
    document = PBI_DOC.read_text(encoding="utf-8")
    for metric, definition in METRICS.items():
        measure = definition.pbi_source.split(" = ")[0].strip()
        assert measure, metric
        assert measure in document, f"{metric.value}: Power BI measure {measure!r} not found in the semantic document"


def test_each_metric_has_exactly_one_value_form():
    for metric, definition in METRICS.items():
        forms = [bool(definition.aggregate), definition.is_ratio, definition.order_level]
        assert sum(forms) == 1, metric
        if definition.is_ratio:
            assert definition.numerator and definition.denominator and definition.ratio_noun_pt, metric


def test_value_aliases_are_unique_and_plain_identifiers():
    aliases = [definition.value_alias for definition in METRICS.values()]
    assert len(aliases) == len(set(aliases))
    assert all(re.fullmatch(r"[a-z_][a-z0-9_]*", alias) for alias in aliases)


def test_every_metric_carries_its_presentation_metadata_in_the_registry():
    """The renderer has no per-question or per-metric wording of its own: how
    a metric reads (article + noun, sentence, ranking phrase, value format)
    is registry data."""
    assert {u.value for u in Unit} >= {"CURRENCY_BRL", "INTEGER", "DECIMAL", "PERCENT", "DAYS"}
    for metric, definition in METRICS.items():
        assert definition.display_pt.split()[0] in {"o", "a", "os", "as"}, metric  # article + noun phrase
        assert "{value}" in definition.sentence_pt and definition.sentence_pt.endswith("."), metric
        assert definition.sentence_pt.format(value="X", scope="") and definition.ranking_pt, metric
        assert isinstance(definition.unit, Unit), metric
        if definition.sentence_one_pt:  # singular form only for whole-number values
            assert definition.unit in (Unit.INTEGER, Unit.UNITS), metric
            assert "{value}" in definition.sentence_one_pt
        # nothing user-facing may be an internal identifier
        for text in (definition.display_pt, definition.sentence_pt, definition.ranking_pt):
            assert not re.search(r"[A-Z]{2,}_[A-Z_]+|·", text), (metric, text)


def test_dimension_labels_and_gender_are_registry_data():
    for dimension, definition in DIMENSIONS.items():
        assert definition.label_pt and definition.plural_pt, dimension
    assert DIMENSIONS[Dimension.CATEGORY].feminine and not DIMENSIONS[Dimension.PRODUCT].feminine


def test_metric_families_and_facts_are_consistent():
    for metric, definition in METRICS.items():
        assert definition.fact in FACTS, metric
        assert isinstance(definition.family, MetricFamily)
    assert {METRICS[m].family for m in P0_METRICS} == set(MetricFamily)


# ---------------------------------------------------------------------------
# compatibility data is coherent with the physical bindings
# ---------------------------------------------------------------------------


def test_every_dimension_a_metric_accepts_is_bound_on_its_fact():
    for metric, definition in METRICS.items():
        bound = set(FACTS[definition.fact].bindings)
        assert definition.dimensions <= bound, (metric, definition.dimensions - bound)


def test_filters_are_backed_by_a_binding_or_a_date_column():
    for metric, definition in METRICS.items():
        fact = FACTS[definition.fact]
        for kind in definition.filters:
            if kind is FilterKind.DATE_RANGE:
                assert fact.date_column and not definition.snapshot, metric
            else:
                assert Dimension[kind.value] in fact.bindings, (metric, kind)


def test_snapshot_metrics_never_carry_time_capabilities():
    for metric, definition in METRICS.items():
        if definition.snapshot:
            assert FilterKind.DATE_RANGE not in definition.filters, metric
            assert not definition.time_modifiers, metric
            assert FACTS[definition.fact].date_column is None, metric


def test_time_modifiers_exist_only_on_net_revenue():
    # Power BI defines YTD, LTM and %YoY only for [Receita Líquida].
    with_modifiers = {m for m, d in METRICS.items() if d.time_modifiers}
    assert with_modifiers == {Metric.NET_REVENUE}
    assert METRICS[Metric.NET_REVENUE].time_modifiers == frozenset(TimeModifier)


def test_order_level_metrics_never_accept_item_level_dimensions_or_filters():
    ticket = METRICS[Metric.AVERAGE_TICKET]
    assert ticket.order_level
    assert not ({Dimension.PRODUCT, Dimension.CATEGORY} & ticket.dimensions)
    assert FilterKind.CATEGORY not in ticket.filters


def test_no_metric_lists_a_high_cardinality_dimension_as_a_breakdown_candidate_without_ranking():
    # PRODUCT / SALESPERSON are too large to list; the validator turns them
    # into ranking-only. The registry must mark them as such.
    assert DIMENSIONS[Dimension.PRODUCT].high_cardinality
    assert DIMENSIONS[Dimension.SALESPERSON].high_cardinality
    assert not DIMENSIONS[Dimension.CATEGORY].high_cardinality


def test_only_days_to_stockout_ranks_ascending_and_it_has_no_plain_aggregate():
    ascending = {m for m, d in METRICS.items() if d.ranking_order is SortOrder.ASC}
    assert ascending == {Metric.DAYS_TO_STOCKOUT}
    assert Operation.AGGREGATE not in METRICS[Metric.DAYS_TO_STOCKOUT].operations


def test_days_to_stockout_keeps_the_power_bi_definition():
    """Pinned: [Dias até Ruptura] is SUM(estimated_days_until_stockout) as in
    Power BI (meaningful per item, so no plain aggregate); soonest first."""
    definition = METRICS[Metric.DAYS_TO_STOCKOUT]
    assert definition.pbi_source == "Dias até Ruptura = SUM(fct_inventory_replenishment[estimated_days_until_stockout])"
    assert definition.aggregate == "SUM(r.estimated_days_until_stockout)"
    assert definition.operations == frozenset({Operation.RANKING, Operation.BREAKDOWN})
    assert definition.dimensions == frozenset({Dimension.PRODUCT, Dimension.CATEGORY, Dimension.LOCATION})
    assert definition.ranking_order is SortOrder.ASC and definition.snapshot and definition.nullable_value


def test_tie_semantics_follow_power_bi():
    # Products: RANKX(..., Dense). Salespeople: default RANKX (ties skip positions).
    assert DIMENSIONS[Dimension.PRODUCT].rank_function == "DENSE_RANK"
    assert DIMENSIONS[Dimension.SALESPERSON].rank_function == "RANK"


def test_the_salesperson_ranking_reproduces_the_filters_of_the_power_bi_ranking_visual():
    """[Ranking de Vendedores por Receita] has no role filter itself; the
    report's ranking table (Vendas) filters employee_role IN ('Salesperson')
    and employee_full_name NOT IN (null). That is the rule the dashboard
    shows, and the one reproduced (and stated in the answer)."""
    binding = FACTS["sales"].bindings[Dimension.SALESPERSON]
    assert binding.predicates == ("e.employee_role = 'Salesperson'", "e.employee_full_name IS NOT NULL")
    assert binding.join is not None and binding.join.kind == "INNER"  # unmatched sales cannot enter the ranking
    document = PBI_DOC.read_text(encoding="utf-8")
    assert "dim_employees[employee_role] = 'Salesperson'" in document  # documented Power BI rule
    assert "employee_role" in GOLD["dim_employees"] and "employee_full_name" in GOLD["dim_employees"]
    assert DIMENSIONS[Dimension.SALESPERSON].scope_note_pt  # the answer states the rule
    # no other dimension carries a hidden rule
    with_rules = {d for fact in FACTS.values() for d, b in fact.bindings.items() if b.predicates}
    assert with_rules == {Dimension.SALESPERSON}


def test_state_is_never_ambiguous_it_is_two_distinct_dimensions():
    assert Dimension.LOCATION_STATE is not Dimension.CUSTOMER_STATE
    assert "dim_locations[location_state]" in DIMENSIONS[Dimension.LOCATION_STATE].pbi_source
    assert "dim_customers[primary_address_state]" in DIMENSIONS[Dimension.CUSTOMER_STATE].pbi_source


# ---------------------------------------------------------------------------
# Power BI semantics carried by the registry
# ---------------------------------------------------------------------------


def test_sales_metrics_inherit_the_effective_sale_filter():
    assert FACTS["sales"].base_predicates == ("s.is_effective_sale = TRUE",)
    for metric, definition in METRICS.items():
        if definition.fact == "sales":
            assert definition.family in (MetricFamily.SALES, MetricFamily.RETURNS), metric


@pytest.mark.parametrize(
    "metric, expected",
    [
        (Metric.NET_REVENUE, "SUM(s.order_item_net_revenue_after_returns)"),  # GOLD-004
        (Metric.TOTAL_QUANTITY_SOLD, "SUM(s.order_item_quantity)"),
        (Metric.EFFECTIVE_ORDERS, "COUNT(DISTINCT s.order_id)"),
        (Metric.RETURN_AMOUNT, "SUM(s.completed_refund_amount)"),
        (Metric.STOCK_ON_HAND, "SUM(r.quantity_on_hand)"),
        (Metric.AT_RISK_REVENUE, "SUM(k.monetary_amount)"),
    ],
)
def test_aggregates_are_the_exact_power_bi_expressions(metric, expected):
    assert METRICS[metric].aggregate == expected


def test_ratio_metrics_mirror_the_dax_divide_definitions():
    assert METRICS[Metric.RETURN_RATE].numerator == "SUM(s.completed_returned_quantity)"
    assert METRICS[Metric.RETURN_RATE].denominator == "SUM(s.order_item_quantity)"
    assert METRICS[Metric.STOCKOUT_RISK].numerator == "COUNT_IF(r.will_stockout_before_replenishment = TRUE)"
    assert METRICS[Metric.REPLENISHMENT_REQUIRED].numerator == "COUNT_IF(r.is_replenishment_required = TRUE)"
    for metric in (Metric.RETURN_RATE, Metric.STOCKOUT_RISK, Metric.REPLENISHMENT_REQUIRED):
        assert METRICS[metric].unit is Unit.PERCENT and METRICS[metric].nullable_value  # DIVIDE -> BLANK


def test_at_risk_metrics_use_the_power_bi_segment_literal():
    for metric in (Metric.AT_RISK_CUSTOMERS, Metric.AT_RISK_REVENUE):
        assert METRICS[metric].metric_predicates == ("k.customer_segment = 'at_risk'",)
    assert "at_risk" in CUSTOMER_SEGMENTS


def test_the_five_inventory_health_statuses_are_registered_in_business_order():
    assert INVENTORY_HEALTH_STATUSES == ("out_of_stock", "critical", "attention", "healthy", "no_recent_sales")


# ---------------------------------------------------------------------------
# every table and column exists in the real Gold dbt models
# ---------------------------------------------------------------------------


def test_the_gold_yaml_is_available_for_the_checks_below():
    assert {"fct_sales", "dim_products", "agg_customer_rfm", "fct_current_inventory"} <= set(GOLD)


def test_every_registered_table_is_a_gold_model():
    for fact in FACTS.values():
        assert fact.table in GOLD, fact.table
        for binding in fact.bindings.values():
            if binding.join is not None:
                assert binding.join.table in GOLD, binding.join.table


def test_every_column_referenced_by_the_registry_exists_in_gold():
    for fact in FACTS.values():
        alias_tables = {fact.alias: fact.table}
        fragments: list[str] = list(fact.base_predicates) + ([fact.date_column] if fact.date_column else [])
        for binding in fact.bindings.values():
            if binding.join is not None:
                alias_tables[binding.join.alias] = binding.join.table
                fragments.append(binding.join.on)
            fragments += [column.expr for column in binding.columns]
            fragments += list(binding.predicates)
            if binding.order_by:
                fragments.append(binding.order_by)
        for definition in METRICS.values():
            if definition.fact == fact.key:
                fragments += _fragments(definition)
        for fragment in fragments:
            for alias, column in _column_references(fragment):
                assert alias in alias_tables, (fact.key, fragment, alias)
                assert column in GOLD[alias_tables[alias]], f"{alias_tables[alias]}.{column} (in {fragment!r}) is not a Gold column"


def test_order_channel_label_and_customer_segments_match_gold():
    assert "order_channel_label" in GOLD["fct_sales"]
    assert {"customer_segment", "recency_days", "monetary_amount"} <= GOLD["agg_customer_rfm"]
    sql = (MARTS / "fact" / "agg_customer_rfm.sql").read_text(encoding="utf-8")
    for segment in CUSTOMER_SEGMENTS:
        assert f"'{segment}'" in sql, segment


# ---------------------------------------------------------------------------
# personal data
# ---------------------------------------------------------------------------


def test_personal_data_is_absent_from_the_registry():
    registry_text = " ".join(
        [column.expr for fact in FACTS.values() for b in fact.bindings.values() for column in b.columns]
        + [text for d in METRICS.values() for text in _fragments(d)]
        + [d.pbi_source for d in DIMENSIONS.values()]
        + [m.value for m in Metric]
        + [d.value for d in Dimension]
    ).lower()
    for attribute in BLOCKED_ATTRIBUTES:
        assert attribute not in registry_text, attribute
    for word in ("email", "phone", "telefone", "cpf"):
        assert word not in registry_text, word
    # ...even though Gold does have those columns (Power BI exposes them unmasked).
    assert {"customer_email", "customer_phone"} <= GOLD["dim_customers"]
