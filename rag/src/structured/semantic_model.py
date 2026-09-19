"""Semantic model of the analytical layer — the metric / dimension registry.

Power BI is the semantic contract, Gold is the runtime data source. Every
metric below is one measure (or one filtered variant of a measure) of the
"Dashboard Gerencial Compass 360 - LH Nautical" model, documented in
``rag/knowledge_base/powerbi_semantic_rules.md``, expressed against the
Gold tables that model imports unchanged. ``MetricDef.pbi_source`` names the
exact Power BI measure so a definition can always be traced back.

Nothing here is executable on its own and nothing here comes from a user:
every SQL fragment is authored and reviewed, and the compiler
(:mod:`src.structured.compiler`) only ever assembles these fragments.

Adding a metric is normally one :class:`MetricDef` entry (plus, if it needs
a new table, one :class:`FactDef`). The compatibility rules — which
operations, dimensions and filters a metric accepts — are data on the
``MetricDef``, so the validator (:mod:`src.structured.analytical_request`)
never changes when a metric is added.

Time semantics that are *modifiers* of a base metric in Power BI (YTD, LTM,
year-over-year) are modeled as modifiers of ``NET_REVENUE``, not as
separate metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from src.structured.models import ColumnType

__all__ = [
    "Operation",
    "TimeGrain",
    "TimeModifier",
    "SortOrder",
    "Unit",
    "MetricFamily",
    "Metric",
    "Dimension",
    "FilterKind",
    "JoinSpec",
    "GroupColumn",
    "DimensionBinding",
    "FactDef",
    "MetricDef",
    "DimensionDef",
    "FACTS",
    "METRICS",
    "DIMENSIONS",
    "INVENTORY_HEALTH_STATUSES",
    "CUSTOMER_SEGMENTS",
    "BLOCKED_ATTRIBUTES",
]


class Operation(Enum):
    AGGREGATE = "AGGREGATE"
    RANKING = "RANKING"
    BREAKDOWN = "BREAKDOWN"
    TREND = "TREND"


class TimeGrain(Enum):
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"


class TimeModifier(Enum):
    """Time-intelligence variants of a base metric (Power BI ``TOTALYTD``,
    ``DATESINPERIOD(..., -12, MONTH)``, ``SAMEPERIODLASTYEAR``)."""

    YTD = "YTD"
    LTM = "LTM"
    YOY = "YOY"


class SortOrder(Enum):
    DESC = "DESC"
    ASC = "ASC"


class Unit(Enum):
    """How a metric's value is presented (and, for the compiler, whether it
    is a whole number): Brazilian reais, a whole number, a whole number of
    units (an integer that names its noun), a plain decimal, a percentage
    (a ratio shown x 100) or days."""

    CURRENCY_BRL = "CURRENCY_BRL"
    INTEGER = "INTEGER"
    UNITS = "UNITS"
    DECIMAL = "DECIMAL"
    PERCENT = "PERCENT"
    DAYS = "DAYS"


class MetricFamily(Enum):
    SALES = "SALES"
    RETURNS = "RETURNS"
    INVENTORY = "INVENTORY"
    CUSTOMER = "CUSTOMER"


class Metric(Enum):
    # SALES
    NET_REVENUE = "NET_REVENUE"
    TOTAL_QUANTITY_SOLD = "TOTAL_QUANTITY_SOLD"
    EFFECTIVE_ORDERS = "EFFECTIVE_ORDERS"
    AVERAGE_TICKET = "AVERAGE_TICKET"
    # RETURNS
    RETURN_AMOUNT = "RETURN_AMOUNT"
    RETURN_RATE = "RETURN_RATE"
    # INVENTORY (current snapshot)
    STOCK_ON_HAND = "STOCK_ON_HAND"
    DAYS_TO_STOCKOUT = "DAYS_TO_STOCKOUT"
    REPLENISHMENT_REQUIRED = "REPLENISHMENT_REQUIRED"
    STOCKOUT_RISK = "STOCKOUT_RISK"
    INVENTORY_ITEMS = "INVENTORY_ITEMS"
    # CUSTOMER / RFM (current snapshot)
    CUSTOMER_COUNT = "CUSTOMER_COUNT"
    AT_RISK_CUSTOMERS = "AT_RISK_CUSTOMERS"
    AT_RISK_REVENUE = "AT_RISK_REVENUE"
    # P1 — added only to prove that a metric is a registry entry (see README)
    AVERAGE_RECENCY = "AVERAGE_RECENCY"


class Dimension(Enum):
    PRODUCT = "PRODUCT"
    CATEGORY = "CATEGORY"
    LOCATION = "LOCATION"
    LOCATION_STATE = "LOCATION_STATE"
    CUSTOMER_STATE = "CUSTOMER_STATE"
    CHANNEL = "CHANNEL"
    SALESPERSON = "SALESPERSON"
    CUSTOMER_SEGMENT = "CUSTOMER_SEGMENT"
    INVENTORY_HEALTH_STATUS = "INVENTORY_HEALTH_STATUS"


class FilterKind(Enum):
    DATE_RANGE = "DATE_RANGE"
    CATEGORY = "CATEGORY"
    CUSTOMER_SEGMENT = "CUSTOMER_SEGMENT"
    INVENTORY_HEALTH_STATUS = "INVENTORY_HEALTH_STATUS"


#: Values of ``fct_current_inventory.inventory_health_status`` (GOLD-017,
#: ``classify_inventory_health``; the Power BI calculated column
#: ``'Ordem Status Saúde'`` enumerates the same five, in this order).
INVENTORY_HEALTH_STATUSES: tuple[str, ...] = (
    "out_of_stock",
    "critical",
    "attention",
    "healthy",
    "no_recent_sales",
)

#: Values of ``agg_customer_rfm.customer_segment`` (the ``case`` in
#: ``agg_customer_rfm.sql``).
CUSTOMER_SEGMENTS: tuple[str, ...] = (
    "champions",
    "loyal",
    "at_risk",
    "new",
    "needs_attention",
    "hibernating",
)

#: Gold attributes that are personal data. They are deliberately absent from
#: every binding below; the parser also refuses questions that ask for them.
#: (Power BI exposes ``customer_email`` / ``customer_phone`` unmasked with
#: no row-level security — the Harness must not inherit that.)
BLOCKED_ATTRIBUTES: frozenset[str] = frozenset(
    {"customer_email", "customer_phone", "primary_address_street", "customer_document"}
)


# ---------------------------------------------------------------------------
# Physical bindings (Gold tables)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JoinSpec:
    table: str
    alias: str
    on: str
    kind: str = "INNER"  # LEFT keeps rows whose optional foreign key is NULL


@dataclass(frozen=True)
class GroupColumn:
    """One grouping column: SQL expression, output name and how to parse it."""

    expr: str
    name: str
    type: ColumnType = ColumnType.TEXT
    nullable: bool = True


@dataclass(frozen=True)
class DimensionBinding:
    """How one dimension attaches to one fact table."""

    columns: tuple[GroupColumn, ...]
    join: JoinSpec | None = None
    #: SQL expression that overrides the default ordering of a breakdown
    #: (e.g. the fixed business order of the health statuses).
    order_by: str | None = None
    #: Predicates that always accompany this dimension (a Power BI rule that
    #: lives in the report's visuals rather than in the measure — see the
    #: salesperson binding). Applied whenever the dimension is used.
    predicates: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactDef:
    key: str
    table: str
    alias: str
    #: Business filters that every metric on this fact inherits (for sales:
    #: Power BI's ``is_effective_sale = TRUE()``).
    base_predicates: tuple[str, ...]
    #: Text describing that population in answers ("Fonte: ...").
    source_note_pt: str
    #: ``applied_filters["population"]``.
    population: str
    date_column: str | None
    bindings: Mapping[Dimension, DimensionBinding]


_PRODUCT_JOIN = "dim_products"


def _product_bindings(fact_alias: str) -> dict[Dimension, DimensionBinding]:
    join = JoinSpec(_PRODUCT_JOIN, "p", f"{fact_alias}.product_variant_id = p.product_variant_id", "INNER")
    return {
        # Product grain: variants are folded into product_id (Power BI
        # ranks/counts on ``dim_products[product_id]``).
        Dimension.PRODUCT: DimensionBinding(
            columns=(
                GroupColumn("p.product_id", "product_id", ColumnType.INT, nullable=False),
                GroupColumn("p.product_name", "product_name", ColumnType.TEXT, nullable=False),
            ),
            join=join,
        ),
        Dimension.CATEGORY: DimensionBinding(columns=(GroupColumn("p.category_name", "category_name"),), join=join),
    }


def _location_bindings(fact_alias: str) -> dict[Dimension, DimensionBinding]:
    join = JoinSpec("dim_locations", "l", f"{fact_alias}.location_id = l.location_id", "LEFT")
    return {
        Dimension.LOCATION: DimensionBinding(columns=(GroupColumn("l.location_name", "location_name"),), join=join),
        Dimension.LOCATION_STATE: DimensionBinding(columns=(GroupColumn("l.location_state", "location_state"),), join=join),
    }


def _customer_state_binding(fact_alias: str) -> DimensionBinding:
    return DimensionBinding(
        columns=(GroupColumn("c.primary_address_state", "customer_state"),),
        join=JoinSpec("dim_customers", "c", f"{fact_alias}.customer_id = c.customer_id", "LEFT"),
    )


_HEALTH_ORDER = "CASE i.inventory_health_status " + " ".join(
    f"WHEN '{status}' THEN {index}" for index, status in enumerate(INVENTORY_HEALTH_STATUSES, start=1)
) + " ELSE 99 END"

FACTS: Mapping[str, FactDef] = MappingProxyType(
    {
        # Power BI: fct_sales, every measure filtered by is_effective_sale = TRUE()
        "sales": FactDef(
            key="sales",
            table="fct_sales",
            alias="s",
            base_predicates=("s.is_effective_sale = TRUE",),
            source_note_pt="Fonte: dados de vendas efetivas.",
            population="effective_sales",
            # The business date of a sale is the date of its placed_at timestamp.
            # The deployed gold.fct_sales has no order_date (nor a date_id to
            # join dim_dates), so the date is derived here, without a join.
            date_column="CAST(s.placed_at AS DATE)",
            bindings=MappingProxyType(
                {
                    **_product_bindings("s"),
                    **_location_bindings("s"),
                    Dimension.CUSTOMER_STATE: _customer_state_binding("s"),
                    Dimension.CHANNEL: DimensionBinding(columns=(GroupColumn("s.order_channel_label", "order_channel"),)),
                    # The Power BI measure [Ranking de Vendedores por Receita] has no
                    # role filter of its own; the report's ranking visual (Vendas,
                    # tableEx e4e2d1a2fd6621dc4f5b) filters
                    # employee_role IN ('Salesperson') and
                    # employee_full_name NOT IN (null). The dashboard's salesperson
                    # ranking is the ranking among those employees, so that is the
                    # rule reproduced here — and stated in the answer.
                    Dimension.SALESPERSON: DimensionBinding(
                        columns=(GroupColumn("e.employee_full_name", "salesperson"),),
                        join=JoinSpec("dim_employees", "e", "s.salesperson_id = e.employee_id", "INNER"),
                        predicates=("e.employee_role = 'Salesperson'", "e.employee_full_name IS NOT NULL"),
                    ),
                }
            ),
        ),
        # Power BI: fct_inventory_replenishment (stock, ruptura, reposição)
        "replenishment": FactDef(
            key="replenishment",
            table="fct_inventory_replenishment",
            alias="r",
            base_predicates=(),
            source_note_pt="Fonte: posição atual de estoque e modelo de reabastecimento (snapshot).",
            population="current_replenishment_snapshot",
            date_column=None,
            bindings=MappingProxyType({**_product_bindings("r"), **_location_bindings("r")}),
        ),
        # Power BI: fct_current_inventory (status de saúde do estoque)
        "inventory": FactDef(
            key="inventory",
            table="fct_current_inventory",
            alias="i",
            base_predicates=(),
            source_note_pt="Fonte: posição atual de estoque (snapshot).",
            population="current_inventory_snapshot",
            date_column=None,
            bindings=MappingProxyType(
                {
                    **_product_bindings("i"),
                    **_location_bindings("i"),
                    Dimension.INVENTORY_HEALTH_STATUS: DimensionBinding(
                        columns=(GroupColumn("i.inventory_health_status", "inventory_health_status"),),
                        order_by=_HEALTH_ORDER,
                    ),
                }
            ),
        ),
        # Power BI: agg_customer_rfm (one row per customer with an effective sale)
        "customer_rfm": FactDef(
            key="customer_rfm",
            table="agg_customer_rfm",
            alias="k",
            base_predicates=(),
            source_note_pt="Fonte: segmentação RFM de clientes com ao menos uma venda efetiva (snapshot).",
            population="customer_rfm_snapshot",
            date_column=None,
            bindings=MappingProxyType(
                {
                    Dimension.CUSTOMER_SEGMENT: DimensionBinding(columns=(GroupColumn("k.customer_segment", "customer_segment"),)),
                    Dimension.CUSTOMER_STATE: _customer_state_binding("k"),
                }
            ),
        ),
    }
)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_ALL_OPS = frozenset(Operation)
_SALES_DIMS = frozenset(
    {
        Dimension.PRODUCT,
        Dimension.CATEGORY,
        Dimension.LOCATION,
        Dimension.LOCATION_STATE,
        Dimension.CUSTOMER_STATE,
        Dimension.CHANNEL,
        Dimension.SALESPERSON,
    }
)
# Order-level attributes only: an order has one location, channel, salesperson
# and customer, but many products/categories.
_ORDER_LEVEL_DIMS = _SALES_DIMS - {Dimension.PRODUCT, Dimension.CATEGORY}
_INVENTORY_DIMS = frozenset({Dimension.PRODUCT, Dimension.CATEGORY, Dimension.LOCATION, Dimension.LOCATION_STATE})


@dataclass(frozen=True)
class MetricDef:
    metric: Metric
    family: MetricFamily
    fact: str
    unit: Unit
    label_pt: str
    #: Presentation (Portuguese), used only by the deterministic renderer —
    #: never by a model, never per question:
    #: the noun phrase with its article ("o faturamento líquido"), used in
    #: introductions ("Veja o faturamento líquido por categoria:");
    display_pt: str
    #: the sentence for a single value; ``{value}`` is the formatted value and
    #: ``{scope}`` the period/filter phrase ("" or " em 2025");
    sentence_pt: str
    #: what the metric is ranked by ("maior receita líquida", "mais unidades
    #: vendidas"): "Os 5 produtos com <ranking_pt> foram:".
    ranking_pt: str
    #: The Power BI measure this definition mirrors, and where its DAX is documented.
    pbi_source: str
    #: SQL column alias of the value.
    value_alias: str
    #: ``SUM(...)`` / ``COUNT(...)`` expression, for a plain aggregate.
    aggregate: str | None = None
    #: For ratios (Power BI ``DIVIDE``): value = numerator / NULLIF(denominator, 0).
    numerator: str | None = None
    denominator: str | None = None
    #: Words for "N de M <noun>" next to a ratio.
    ratio_noun_pt: str = ""
    #: Extra ``CALCULATE`` filters of the measure (e.g. ``customer_segment = 'at_risk'``).
    metric_predicates: tuple[str, ...] = ()
    #: One row per order, amount = MAX(order_total_amount) per order (Power BI
    #: ``Ticket Médio``: order_total_amount is repeated on every item).
    order_level: bool = False
    #: Singular form of ``sentence_pt`` for a value of exactly 1 ("Foi 1 pedido efetivo.").
    sentence_one_pt: str = ""
    #: Value can be NULL (ratios, days) — such groups are not ranked.
    nullable_value: bool = False
    operations: frozenset[Operation] = _ALL_OPS
    dimensions: frozenset[Dimension] = frozenset()
    filters: frozenset[FilterKind] = frozenset()
    time_modifiers: frozenset[TimeModifier] = frozenset()
    ranking_order: SortOrder = SortOrder.DESC
    #: Whether the metric is a current snapshot: no date filter, no trend.
    snapshot: bool = False

    @property
    def is_ratio(self) -> bool:
        return self.numerator is not None


@dataclass(frozen=True)
class DimensionDef:
    dimension: Dimension
    label_pt: str
    plural_pt: str
    #: The Power BI table/column(s) this dimension mirrors.
    pbi_source: str
    #: Too many distinct values to list as a breakdown: only a ranking (top N).
    high_cardinality: bool = False
    #: ``DENSE_RANK`` (Power BI ``RANKX(..., Dense)``, used for products) or
    #: ``RANK`` (default ``RANKX``: ties skip positions, used for salespeople).
    rank_function: str = "DENSE_RANK"
    #: A rule this dimension applies that the answer should state.
    scope_note_pt: str = ""
    #: Grammatical gender, for "As 5 categorias ..." / "Os 5 produtos ...".
    feminine: bool = False


DIMENSIONS: Mapping[Dimension, DimensionDef] = MappingProxyType(
    {
        Dimension.PRODUCT: DimensionDef(
            Dimension.PRODUCT, "produto", "produtos", "dim_products[product_id], dim_products[product_name]",
            high_cardinality=True,
        ),
        Dimension.CATEGORY: DimensionDef(
            Dimension.CATEGORY, "categoria", "categorias", "dim_products[category_name]", feminine=True
        ),
        Dimension.LOCATION: DimensionDef(Dimension.LOCATION, "estabelecimento", "estabelecimentos", "dim_locations[location_name]"),
        Dimension.LOCATION_STATE: DimensionDef(
            Dimension.LOCATION_STATE, "estado do estabelecimento", "estados dos estabelecimentos", "dim_locations[location_state]"
        ),
        Dimension.CUSTOMER_STATE: DimensionDef(
            Dimension.CUSTOMER_STATE, "estado do cliente", "estados dos clientes", "dim_customers[primary_address_state]"
        ),
        Dimension.CHANNEL: DimensionDef(Dimension.CHANNEL, "canal", "canais", "fct_sales[order_channel_label]"),
        Dimension.SALESPERSON: DimensionDef(
            Dimension.SALESPERSON, "vendedor", "vendedores", "dim_employees[employee_full_name]",
            high_cardinality=True, rank_function="RANK",
            scope_note_pt="Considera apenas funcionários com cargo Salesperson.",
        ),
        Dimension.CUSTOMER_SEGMENT: DimensionDef(
            Dimension.CUSTOMER_SEGMENT, "segmento RFM", "segmentos RFM", "agg_customer_rfm[customer_segment]"
        ),
        Dimension.INVENTORY_HEALTH_STATUS: DimensionDef(
            Dimension.INVENTORY_HEALTH_STATUS, "status de saúde do estoque", "status de saúde do estoque",
            "fct_current_inventory[inventory_health_status]",
        ),
    }
)

_SALES_FILTERS = frozenset({FilterKind.DATE_RANGE, FilterKind.CATEGORY})

METRICS: Mapping[Metric, MetricDef] = MappingProxyType(
    {
        # ------------------------------------------------------------------ SALES
        Metric.NET_REVENUE: MetricDef(
            metric=Metric.NET_REVENUE, family=MetricFamily.SALES, fact="sales", unit=Unit.CURRENCY_BRL,
            label_pt="receita líquida",
            display_pt="o faturamento líquido", sentence_pt="O faturamento líquido{scope} foi de {value}.", ranking_pt="maior receita líquida",
            pbi_source="Receita Líquida = CALCULATE(SUM(fct_sales[order_item_net_revenue_after_returns]), is_effective_sale = TRUE)",
            value_alias="net_revenue",
            aggregate="SUM(s.order_item_net_revenue_after_returns)",
            dimensions=_SALES_DIMS, filters=_SALES_FILTERS,
            time_modifiers=frozenset({TimeModifier.YTD, TimeModifier.LTM, TimeModifier.YOY}),
        ),
        Metric.TOTAL_QUANTITY_SOLD: MetricDef(
            metric=Metric.TOTAL_QUANTITY_SOLD, family=MetricFamily.SALES, fact="sales", unit=Unit.UNITS,
            label_pt="quantidade de unidades vendidas",
            display_pt="as unidades vendidas", sentence_pt="Foram vendidas {value}{scope}.", ranking_pt="mais unidades vendidas",
            sentence_one_pt="Foi vendida {value}{scope}.",
            pbi_source="Quantidade Total Vendida = CALCULATE(SUM(fct_sales[order_item_quantity]), is_effective_sale = TRUE)",
            value_alias="quantity_sold",
            aggregate="SUM(s.order_item_quantity)",
            dimensions=_SALES_DIMS, filters=_SALES_FILTERS,
        ),
        Metric.EFFECTIVE_ORDERS: MetricDef(
            metric=Metric.EFFECTIVE_ORDERS, family=MetricFamily.SALES, fact="sales", unit=Unit.INTEGER,
            label_pt="pedidos efetivos",
            display_pt="os pedidos efetivos", sentence_pt="Foram {value} pedidos efetivos{scope}.", ranking_pt="mais pedidos efetivos",
            sentence_one_pt="Foi {value} pedido efetivo{scope}.",
            pbi_source="Pedidos Efetivos = CALCULATE(DISTINCTCOUNT(fct_sales[order_id]), is_effective_sale = TRUE)",
            value_alias="effective_orders",
            aggregate="COUNT(DISTINCT s.order_id)",
            dimensions=_SALES_DIMS, filters=_SALES_FILTERS,
        ),
        Metric.AVERAGE_TICKET: MetricDef(
            metric=Metric.AVERAGE_TICKET, family=MetricFamily.SALES, fact="sales", unit=Unit.CURRENCY_BRL,
            label_pt="ticket médio",
            display_pt="o ticket médio", sentence_pt="O ticket médio{scope} foi de {value}.", ranking_pt="maior ticket médio",
            pbi_source=(
                "Ticket Médio = DIVIDE(SUMX(VALUES(order_id), MAX(order_total_amount)) [effective], [Pedidos Efetivos]); "
                "order_total_amount is order-level (GOLD-008)"
            ),
            value_alias="average_ticket",
            order_level=True, nullable_value=True,
            # Order-level metric: never split by item-level product/category,
            # never filtered by category (that would change which items count).
            dimensions=_ORDER_LEVEL_DIMS, filters=frozenset({FilterKind.DATE_RANGE}),
        ),
        # ---------------------------------------------------------------- RETURNS
        Metric.RETURN_AMOUNT: MetricDef(
            metric=Metric.RETURN_AMOUNT, family=MetricFamily.RETURNS, fact="sales", unit=Unit.CURRENCY_BRL,
            label_pt="valor reembolsado",
            display_pt="o valor reembolsado", sentence_pt="O valor reembolsado{scope} foi de {value}.", ranking_pt="maior valor reembolsado",
            pbi_source="Valor Total Reembolsado = CALCULATE(SUM(fct_sales[completed_refund_amount]), is_effective_sale = TRUE)",
            value_alias="refunded_amount",
            aggregate="SUM(s.completed_refund_amount)",
            dimensions=_SALES_DIMS, filters=_SALES_FILTERS,
        ),
        Metric.RETURN_RATE: MetricDef(
            metric=Metric.RETURN_RATE, family=MetricFamily.RETURNS, fact="sales", unit=Unit.PERCENT,
            label_pt="taxa de devolução",
            display_pt="a taxa de devolução", sentence_pt="A taxa de devolução{scope} foi de {value}.", ranking_pt="maior taxa de devolução",
            pbi_source=(
                "Taxa de Devolução = DIVIDE(SUM(completed_returned_quantity), SUM(order_item_quantity)), "
                "both on is_effective_sale = TRUE"
            ),
            value_alias="return_rate",
            numerator="SUM(s.completed_returned_quantity)", denominator="SUM(s.order_item_quantity)",
            ratio_noun_pt="unidades", nullable_value=True,
            dimensions=_SALES_DIMS, filters=_SALES_FILTERS,
        ),
        # -------------------------------------------------------------- INVENTORY
        Metric.STOCK_ON_HAND: MetricDef(
            metric=Metric.STOCK_ON_HAND, family=MetricFamily.INVENTORY, fact="replenishment", unit=Unit.UNITS,
            label_pt="estoque atual",
            display_pt="o estoque atual", sentence_pt="O estoque atual{scope} é de {value}.", ranking_pt="maior estoque atual",
            pbi_source="Estoque Atual = SUM(fct_inventory_replenishment[quantity_on_hand])",
            value_alias="stock_on_hand",
            aggregate="SUM(r.quantity_on_hand)",
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN, Operation.RANKING}),
            dimensions=_INVENTORY_DIMS, filters=frozenset({FilterKind.CATEGORY}), snapshot=True,
        ),
        Metric.DAYS_TO_STOCKOUT: MetricDef(
            metric=Metric.DAYS_TO_STOCKOUT, family=MetricFamily.INVENTORY, fact="replenishment", unit=Unit.DAYS,
            label_pt="dias até a ruptura",
            display_pt="os dias até a ruptura", sentence_pt="Os dias até a ruptura{scope} são {value}.", ranking_pt="menos dias até a ruptura",
            pbi_source="Dias até Ruptura = SUM(fct_inventory_replenishment[estimated_days_until_stockout])",
            value_alias="days_to_stockout",
            aggregate="SUM(r.estimated_days_until_stockout)", nullable_value=True,
            # A sum of days across items is not meaningful as a single total
            # (Power BI only shows it per item), so no AGGREGATE; the soonest
            # ruptures come first.
            operations=frozenset({Operation.RANKING, Operation.BREAKDOWN}),
            dimensions=frozenset({Dimension.PRODUCT, Dimension.CATEGORY, Dimension.LOCATION}),
            filters=frozenset({FilterKind.CATEGORY}), snapshot=True, ranking_order=SortOrder.ASC,
        ),
        Metric.REPLENISHMENT_REQUIRED: MetricDef(
            metric=Metric.REPLENISHMENT_REQUIRED, family=MetricFamily.INVENTORY, fact="replenishment", unit=Unit.PERCENT,
            label_pt="itens com necessidade de reposição",
            display_pt="os itens com necessidade de reposição", sentence_pt="A parcela de itens com necessidade de reposição{scope} é de {value}.", ranking_pt="maior necessidade de reposição",
            pbi_source=(
                "Itens em Reposição (%) = DIVIDE(COUNTROWS(fct_inventory_replenishment) "
                "where is_replenishment_required = TRUE, COUNTROWS(fct_inventory_replenishment))"
            ),
            value_alias="replenishment_required",
            numerator="COUNT_IF(r.is_replenishment_required = TRUE)", denominator="COUNT(*)",
            ratio_noun_pt="itens monitorados", nullable_value=True,
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN, Operation.RANKING}),
            dimensions=_INVENTORY_DIMS, filters=frozenset({FilterKind.CATEGORY}), snapshot=True,
        ),
        Metric.STOCKOUT_RISK: MetricDef(
            metric=Metric.STOCKOUT_RISK, family=MetricFamily.INVENTORY, fact="replenishment", unit=Unit.PERCENT,
            label_pt="itens em risco de ruptura",
            display_pt="os itens em risco de ruptura", sentence_pt="A parcela de itens em risco de ruptura{scope} é de {value}.", ranking_pt="maior risco de ruptura",
            pbi_source=(
                "Itens em Risco de Ruptura = DIVIDE(COUNTROWS(fct_inventory_replenishment) "
                "where will_stockout_before_replenishment = TRUE, COUNTROWS(fct_inventory_replenishment))"
            ),
            value_alias="stockout_risk",
            numerator="COUNT_IF(r.will_stockout_before_replenishment = TRUE)", denominator="COUNT(*)",
            ratio_noun_pt="itens monitorados", nullable_value=True,
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN, Operation.RANKING}),
            dimensions=_INVENTORY_DIMS, filters=frozenset({FilterKind.CATEGORY}), snapshot=True,
        ),
        Metric.INVENTORY_ITEMS: MetricDef(
            metric=Metric.INVENTORY_ITEMS, family=MetricFamily.INVENTORY, fact="inventory", unit=Unit.INTEGER,
            label_pt="itens em estoque",
            display_pt="os itens em estoque", sentence_pt="Há {value} itens em estoque{scope}.", ranking_pt="mais itens em estoque",
            sentence_one_pt="Há {value} item em estoque{scope}.",
            pbi_source=(
                "Itens por Status de Saúde = COUNTROWS(fct_current_inventory); the four status measures "
                "(Fora de Estoque / Crítico / Atenção / Saudável) are the inventory_health_status filter"
            ),
            value_alias="inventory_items",
            aggregate="COUNT(*)",
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN, Operation.RANKING}),
            dimensions=_INVENTORY_DIMS | {Dimension.INVENTORY_HEALTH_STATUS},
            filters=frozenset({FilterKind.INVENTORY_HEALTH_STATUS, FilterKind.CATEGORY}), snapshot=True,
        ),
        # ---------------------------------------------------------- CUSTOMER / RFM
        Metric.CUSTOMER_COUNT: MetricDef(
            metric=Metric.CUSTOMER_COUNT, family=MetricFamily.CUSTOMER, fact="customer_rfm", unit=Unit.INTEGER,
            label_pt="clientes",
            display_pt="os clientes", sentence_pt="Há {value} clientes{scope}.", ranking_pt="mais clientes",
            sentence_one_pt="Há {value} cliente{scope}.",
            pbi_source="Total Clientes = COUNTROWS(agg_customer_rfm)",
            value_alias="customer_count",
            aggregate="COUNT(*)",
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN}),
            dimensions=frozenset({Dimension.CUSTOMER_SEGMENT, Dimension.CUSTOMER_STATE}),
            filters=frozenset({FilterKind.CUSTOMER_SEGMENT}), snapshot=True,
        ),
        Metric.AT_RISK_CUSTOMERS: MetricDef(
            metric=Metric.AT_RISK_CUSTOMERS, family=MetricFamily.CUSTOMER, fact="customer_rfm", unit=Unit.INTEGER,
            label_pt="clientes em risco",
            display_pt="os clientes em risco", sentence_pt="Há {value} clientes em risco{scope}.", ranking_pt="mais clientes em risco",
            sentence_one_pt="Há {value} cliente em risco{scope}.",
            pbi_source="Clientes em Risco = CALCULATE(COUNTROWS(agg_customer_rfm), customer_segment = \"at_risk\")",
            value_alias="at_risk_customers",
            aggregate="COUNT(*)", metric_predicates=("k.customer_segment = 'at_risk'",),
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN}),
            dimensions=frozenset({Dimension.CUSTOMER_STATE}), snapshot=True,
        ),
        Metric.AT_RISK_REVENUE: MetricDef(
            metric=Metric.AT_RISK_REVENUE, family=MetricFamily.CUSTOMER, fact="customer_rfm", unit=Unit.CURRENCY_BRL,
            label_pt="receita em risco (churn)",
            display_pt="a receita em risco (churn)", sentence_pt="A receita em risco (churn){scope} é de {value}.", ranking_pt="maior receita em risco",
            pbi_source="Receita em Risco (Churn) = CALCULATE(SUM(agg_customer_rfm[monetary_amount]), customer_segment = \"at_risk\")",
            value_alias="at_risk_revenue",
            aggregate="SUM(k.monetary_amount)", metric_predicates=("k.customer_segment = 'at_risk'",),
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN}),
            dimensions=frozenset({Dimension.CUSTOMER_STATE}), snapshot=True,
        ),
        Metric.AVERAGE_RECENCY: MetricDef(
            metric=Metric.AVERAGE_RECENCY, family=MetricFamily.CUSTOMER, fact="customer_rfm", unit=Unit.DAYS,
            label_pt="recência média",
            display_pt="a recência média", sentence_pt="A recência média{scope} é de {value}.", ranking_pt="maior recência média",
            pbi_source="Recência Média = AVERAGE(agg_customer_rfm[recency_days])",
            value_alias="average_recency",
            aggregate="AVG(k.recency_days)", nullable_value=True,
            operations=frozenset({Operation.AGGREGATE, Operation.BREAKDOWN}),
            dimensions=frozenset({Dimension.CUSTOMER_SEGMENT, Dimension.CUSTOMER_STATE}),
            filters=frozenset({FilterKind.CUSTOMER_SEGMENT}), snapshot=True,
        ),
    }
)
