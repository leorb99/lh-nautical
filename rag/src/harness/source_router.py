"""Deterministic source router — Phase 6B.1.

Decides *which source family* a question needs (``AI_SEARCH``,
``STRUCTURED_DATA`` or ``BOTH``). It is deliberately a small, rule-based,
fully testable classifier — no LLM, no SQL, no network:

- ``STRUCTURED_DATA`` — the question asks for live analytical values that
  an implemented structured intent can produce (today only
  ``TOP_PRODUCTS_BY_QUANTITY``);
- ``AI_SEARCH`` — the question asks for a rule, definition or explanation
  (or anything not recognized as a data request);
- ``BOTH`` — it asks for the values *and* the rule behind them.

This is **not** the AI Search retrieval policy: ``EXACT_RULE_ID`` /
``NORMAL`` / ``CROSS_SOURCE`` still decide how documentary retrieval runs
once ``AI_SEARCH`` (or ``BOTH``) has been chosen, and are untouched here.

Fail-closed rule: a question that clearly asks for live values this system
cannot produce (an unsupported metric, period, location filter, or an
analytical question with no implemented intent) is *not* routed to
``AI_SEARCH``. Answering "what are the best-selling products in 2024?" with
documentation about how rankings are calculated would be misleading, so it
becomes a controlled "unsupported" decision instead.

How a question is read (four cue families, applied in a fixed precedence):

A. *Documentary cues* — the user asks how something is calculated, defined
   or found ("como é calculado", "qual a regra", "onde vejo", "qual a
   medida"). The text from the first such cue onwards is the **rule
   clause**; everything else is the **data clause**. A question with no
   data clause is purely documentary → ``AI_SEARCH``.
B. *Supported analytical cues* — a request for the implemented
   ``TOP_PRODUCTS_BY_QUANTITY`` ranking, in any wording ("mais vendidos",
   "venderam mais", "campeão de vendas", "itens que mais saem", ...).
C. *Unsupported analytical cues* — a metric or ranking that no implemented
   intent produces (revenue, "menos vendidos", stores/customers rankings,
   totals), and scope qualifiers (period, month, store, ...) that the
   implemented ranking cannot honour. These are read from the **data
   clause only**, and never depend on a "mais vendidos" phrase match.
D. *Explanatory cues* — a rule clause next to a data clause turns the
   supported request into ``BOTH``. Words such as "período" or "ano" inside
   the rule clause are irrelevant to the data request and are ignored.

Precedence: (1) no data clause → ``AI_SEARCH``; (2) unsupported metric →
unsupported; (3) supported ranking with an unsupported scope → unsupported;
(4) supported ranking → ``STRUCTURED_DATA``, or ``BOTH`` with a rule clause;
(5) anything else → ``AI_SEARCH``.
"""

from __future__ import annotations

import re
import unicodedata

from src.harness.harness import RouteDecision, SourceRoute
from src.structured.models import StructuredIntent

__all__ = ["RuleBasedSourceRouter", "normalize_query"]

_NUMBER_WORDS = {
    "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
}  # fmt: skip


def normalize_query(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace, so
    the patterns below work for ``é``/``e``, ``média``/``media``, etc."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_punct = re.sub(r"[^\w\s-]", " ", without_accents)
    return re.sub(r"\s+", " ", without_punct).strip()


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _compile(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


_ITEM = r"(?:produtos?|itens?|item)"
_SALES_VERB = r"(?:vendem|vende|vendeu|venderam|saem|sairam|sai)"
_ENTITY = (
    r"(?:lojas?|filiais|filial|clientes?|vendedores?|vendedor|categorias?|marcas?|"
    r"fornecedores?|fornecedor|regioes|regiao|estados?|cidades?|canais|canal)"
)

# --- A. documentary cues ----------------------------------------------------
# The user asks for a rule, a definition, an explanation, or where/how to find
# something — as opposed to asking for the values themselves.
_DOC_CUES = _compile(
    r"\bcomo\b.{0,40}\b(?:calcul|defin|apur|mede|medid|constr|obtid|deriv)\w*",
    r"\bcomo\b.{0,30}\b(?:mostr|exib|apresent|list|represent|aparec|identific|encontr)\w*",
    r"\bcomo\s+(?:se\s+|eu\s+)?(?:identific|ver|vejo|visualiz|encontr|obter|obtenho|consult|acess|localiz|filtr|"
    r"cri|mont|configur|usar|utiliz|funcion)\w*",
    r"\bonde\s+(?:eu\s+)?(?:vejo|ver|encontro|encontrar|acho|achar|fica|esta|localizo|consulto|visualizo)\b",
    r"\bqual\s+(?:e\s+)?(?:a\s+|o\s+)?(?:regra|definicao|metodologia|logica|formula|criterio|medida|metrica|"
    r"coluna|tabela|visao|campo|fonte|dashboard|relatorio)\b",
    r"\bquais\s+(?:sao\s+)?(?:as\s+|os\s+)?(?:regras|criterios|definicoes|medidas|metricas)\b",
    r"\bque\s+(?:regra|criterio|logica|medida|metrica|tabela)\b",
    r"\bregras?\b.{0,20}\bdefin(?:e|em)\b",
    r"\bo\s+que\s+(?:e|significa|quer\s+dizer)\b",
    r"\b(?:explique|explica|explicar|explicacao|definicao|significado)\b",
    r"\bpor\s+que\b",
)

# Inside a rule clause, a fresh request for values ("... e liste os produtos
# mais vendidos") starts a data clause again.
_VALUE_RESTART = re.compile(r"\b(?:quais|quem|liste|listar|mostre|mostrar|exiba|exibir|me\s+(?:diga|mostre|passe|traga))\b")

# --- B. supported analytical cues: TOP_PRODUCTS_BY_QUANTITY -----------------
# The one implemented structured intent, in the wordings people actually use.
_TOP_PRODUCTS = _compile(
    r"\bmais\s+vendid[oa]s?\b",
    rf"\b{_ITEM}\b.{{0,40}}\b(?:que\s+)?mais\s+{_SALES_VERB}\b",
    rf"\b{_ITEM}\b.{{0,40}}\b{_SALES_VERB}\s+mais\b",
    rf"\b{_ITEM}\b.{{0,30}}\bmais\s+unidades\b",
    rf"\b{_ITEM}\s+mais\s+(?:popular(?:es)?|comprad[oa]s?|procurad[oa]s?)\b",
    rf"\b{_ITEM}\b.{{0,20}}\bcampe(?:ao|oes)\b",
    r"\bcampe(?:ao|oes)\s+de\s+vendas\b",
    rf"\bmelhores?\s+{_ITEM}\b.{{0,20}}\bvendas?\b",
    r"\btop\s*\d*\s+(?:produtos?|itens?)\b",
    r"\branking\s+d[eo]s?\s+(?:produtos?|itens?)\b.{0,30}\b(?:vend|quantidade)",
)

# --- C. unsupported analytical cues -----------------------------------------
# A metric or ranking no implemented intent produces. Independent of any
# "mais vendidos" wording: these decide on their own.
_UNSUPPORTED_METRIC = _compile(
    # revenue-style metrics ("receita" alone is too common in definition
    # questions — "cálculo de receita" — so it counts only when it is being
    # ranked, totalled or split)
    r"\b(?:faturamento|faturou|faturaram|lucro|margem)\b",
    r"\breceita\s+(?:total|liquida|bruta|do|da|de|por)\b",
    r"\b(?:mais|menos|maior|maiores|menor|menores|melhor|melhores|por)\s+(?:receita|valor)\b",
    r"\bger\w*\b.{0,20}\breceita\b",
    r"\bem\s+valor\b",
    # bottom rankings
    r"\bmenos\s+(?:vend|saem|comprad)",
    rf"\b(?:vend\w*|{_SALES_VERB})\s+menos\b",
    # rankings of anything that is not a product
    rf"\b{_ENTITY}\b.{{0,30}}\b(?:vend|compr|fatur|ger|gast)\w*\s+(?:mais|menos)\b",
    rf"\b{_ENTITY}\b.{{0,30}}\b(?:mais|menos)\b.{{0,20}}\b(?:vend|compr|fatur|ger|gast|receit)\w*",
    # totals, counts, stock and forecasts
    r"\b(?:quanto|quantos|quantas)\b.{0,40}\b(?:vend|fatur|receit|pedido|cliente|estoque|devol|lucr)\w*",
    r"\bvendas\s+(?:totais|do\s+mes|do\s+ano|por)\b",
    r"\btotal\s+de\s+vendas\b",
    r"\bestoque\s+(?:atual|hoje)\b",
    r"\bprevisao\s+de\s+demanda\b",
)

# Scope qualifiers the implemented ranking cannot honour: it is always all
# history, all locations, by quantity sold. Applying them silently would give
# a wrong answer. Only consulted when a supported ranking was requested.
_UNSUPPORTED_SCOPE = _compile(
    r"\b(?:19|20)\d{2}\b",
    r"\b(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b",
    r"\b(?:hoje|ontem|dia|dias|semana|semanas|mes|meses|ano|anos|trimestre|semestre|periodo|recente|recentes|"
    r"ultimo|ultima|ultimos|ultimas|verao|inverno|outono|primavera|natal|feriado|feriados)\b",
    r"\b(?:durante|desde|entre|apos|depois|antes|ate)\b",
    r"\bblack\s+friday\b|\bfim\s+de\s+semana\b",
    r"\breceita\b",
    rf"\b{_ENTITY}\b",
)

_TOP_N_PATTERNS = _compile(
    r"\btop\s*(\d{1,4})\b",
    r"\bos\s+(\d{1,4})\s+(?:produtos?|itens?|mais)\b",
    r"\b(\d{1,4})\s+(?:produtos?|itens?)\b",
)
_TOP_N_WORD = re.compile(r"\b(?:top\s+|os\s+)(" + "|".join(_NUMBER_WORDS) + r")\b")

_UNSUPPORTED_SCOPE_REASON = (
    "the product ranking is only available for all available history and all "
    "locations, by total quantity sold"
)
_UNSUPPORTED_METRIC_REASON = "no structured analytical intent is implemented for this kind of question"


def _extract_top_n(text: str) -> int | None:
    for pattern in _TOP_N_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    word = _TOP_N_WORD.search(text)
    return _NUMBER_WORDS[word.group(1)] if word else None


def _split_clauses(query: str) -> tuple[str, str]:
    """Split a question into its (data clause, rule clause), both normalized.

    Sentences are separated on the raw punctuation (normalization removes
    it). Within a sentence the rule clause starts at the first documentary
    cue; a new request for values inside it ("... e liste os produtos") goes
    back to the data clause.
    """
    data_parts: list[str] = []
    rule_parts: list[str] = []
    for sentence in re.split(r"[?!.;\n]+", query):
        text = normalize_query(sentence)
        if not text:
            continue
        cues = [m for m in (pattern.search(text) for pattern in _DOC_CUES) if m]
        if not cues:
            data_parts.append(text)
            continue
        first = min(cues, key=lambda m: m.start())
        data_parts.append(text[: first.start()])
        rule = text[first.start() :]
        restart = _VALUE_RESTART.search(text, first.end())
        if restart:
            data_parts.append(text[restart.start() :])
            rule = text[first.start() : restart.start()]
        rule_parts.append(rule)
    return " ".join(p.strip() for p in data_parts if p.strip()), " ".join(p.strip() for p in rule_parts if p.strip())


class RuleBasedSourceRouter:
    """Implements :class:`~src.harness.harness.SourceRouter`."""

    def route(self, query: str) -> RouteDecision:
        data, rule = _split_clauses(query)

        # 1. Purely documentary wording (or nothing recognizable): no data clause.
        if not data or not _any(_TOP_PRODUCTS + _UNSUPPORTED_METRIC, data):
            reason = "rule/definition question" if rule else "default documentary route"
            return RouteDecision(SourceRoute.AI_SEARCH, reason=reason)

        # 2. A metric or ranking no implemented intent can produce.
        if _any(_UNSUPPORTED_METRIC, data):
            return RouteDecision(
                SourceRoute.STRUCTURED_DATA,
                unsupported_reason=_UNSUPPORTED_METRIC_REASON,
                reason="analytical question with no implemented intent",
            )

        # 3. The supported ranking, but with a scope it cannot honour.
        if _any(_UNSUPPORTED_SCOPE, data):
            return RouteDecision(
                SourceRoute.STRUCTURED_DATA,
                unsupported_reason=_UNSUPPORTED_SCOPE_REASON,
                reason="product ranking with an unsupported scope or metric",
            )

        # 4. The supported ranking; BOTH when the rule behind it is also asked.
        params = {}
        top_n = _extract_top_n(data)
        if top_n is not None:
            params["top_n"] = top_n
        return RouteDecision(
            SourceRoute.BOTH if rule else SourceRoute.STRUCTURED_DATA,
            structured_intent=StructuredIntent.TOP_PRODUCTS_BY_QUANTITY,
            structured_params=params,
            reason="product ranking by quantity" + (" plus its rule" if rule else ""),
        )
