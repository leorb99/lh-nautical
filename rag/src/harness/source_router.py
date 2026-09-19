"""Deterministic source router.

Decides *which source family* a question needs (``AI_SEARCH``,
``STRUCTURED_DATA`` or ``BOTH``). It is deliberately a small, rule-based,
fully testable classifier — no LLM, no SQL, no network:

- ``STRUCTURED_DATA`` — the question asks for live analytical values that
  the approved semantic model can produce (see ``src.structured``);
- ``AI_SEARCH`` — the question asks for a rule, definition or explanation
  (or anything not recognized as a data request);
- ``BOTH`` — it asks for the values *and* the rule behind them.

This is **not** the AI Search retrieval policy: ``EXACT_RULE_ID`` /
``NORMAL`` / ``CROSS_SOURCE`` still decide how documentary retrieval runs
once ``AI_SEARCH`` (or ``BOTH``) has been chosen, and are untouched here.

Fail-closed rule: a question that clearly asks for live values this system
cannot produce (an unapproved metric or combination, an ambiguous or relative
period, personal data, ...) is *not* routed to ``AI_SEARCH``. Answering "what
is the revenue in March?" with documentation about how revenue is calculated
would be misleading, so it becomes a controlled "unsupported" decision.

How a question is read:

1. *Documentary cues* — the user asks how something is calculated, defined
   or found ("como é calculado", "qual a regra", "onde vejo"). The text from
   the first such cue onwards is the **rule clause**; everything else is the
   **data clause**. A question with no data clause is purely documentary →
   ``AI_SEARCH``.
2. The **data clause** is handed to
   :func:`src.structured.parser.parse_analytical_request`, the single place
   that maps natural language to an approved ``AnalyticalRequest`` (metric,
   operation, dimensions, period, ``top_n``), a refusal, or "not analytical".
   Words such as "período" inside the rule clause never affect the data
   request.
3. A supported request → ``STRUCTURED_DATA``, or ``BOTH`` when a rule clause
   is also present; a refusal → ``STRUCTURED_DATA`` with
   ``unsupported_reason``; otherwise ``AI_SEARCH``.
"""

from __future__ import annotations

import re
import unicodedata

from src.harness.harness import RouteDecision, SourceRoute
from src.structured.models import StructuredIntent
from src.structured.parser import parse_analytical_request

__all__ = ["RuleBasedSourceRouter", "normalize_query"]


def normalize_query(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace, so
    the patterns below work for ``é``/``e``, ``média``/``media``, etc."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_punct = re.sub(r"[^\w\s-]", " ", without_accents)
    return re.sub(r"\s+", " ", without_punct).strip()


def _compile(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


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

# Inside a rule clause, a fresh request for values ("... e liste os produtos
# mais vendidos") starts a data clause again.
_VALUE_RESTART = re.compile(r"\b(?:quais|quem|liste|listar|mostre|mostrar|exiba|exibir|me\s+(?:diga|mostre|passe|traga))\b")


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

        # 1. Purely documentary wording (or nothing recognizable as a data request).
        outcome = parse_analytical_request(data) if data else None
        if outcome is None or (outcome.request is None and outcome.unsupported_reason is None):
            reason = "rule/definition question" if rule else "default documentary route"
            return RouteDecision(SourceRoute.AI_SEARCH, reason=reason)

        # 2. Live values this system cannot produce: fail closed.
        if outcome.unsupported_reason is not None:
            return RouteDecision(
                SourceRoute.STRUCTURED_DATA,
                unsupported_reason=outcome.unsupported_reason,
                reason="analytical question that is not supported",
            )

        # 3. A supported analytical request; BOTH when the rule behind it is also asked.
        request = outcome.request
        return RouteDecision(
            SourceRoute.BOTH if rule else SourceRoute.STRUCTURED_DATA,
            structured_intent=StructuredIntent.ANALYTICS,
            structured_params={"request": request},
            reason=f"{request.operation.value} {request.metric.value}" + (" plus its rule" if rule else ""),
        )
