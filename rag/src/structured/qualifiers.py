"""Qualifiers of a stock question: which product, category or location it names.

"Quantos coletes temos em estoque?" is a question about *coletes*, not about
all stock. The parser must therefore never let a noun phrase vanish while it
recognizes the rest of the sentence. This module reads what is left of a
normalized question (after the metric words, period, "por X" phrases and top-N
have been taken out) and accounts for every word:

- a *category* phrase is ``categoria`` followed by a name;
- a *location* phrase is a location type followed by a name ("loja leao",
  "armazem camargo", "centro de distribuicao norte");
- a *product* phrase is the one run of words that is neither vocabulary
  nor part of the two above ("coletes", "motor de popa 6014");
- everything else must be vocabulary (question words, verbs of having,
  stock words, articles, ranking words). Any other leftover — a second run of
  words, a place without a location type, a product identifier — cannot be
  represented, so the question is refused, not answered more broadly.

There is no list of products or locations here, only the *shape* of a phrase.
The result is text, never SQL: :mod:`src.structured.text_match` turns a
phrase into search terms and the compiler binds them as parameters.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Qualifiers", "extract_qualifiers", "item_after_quantity_word"]

_QUESTION = frozenset(
    "quanto quantos quantas quantidade quantidades quais qual quem que me diga mostre mostrar liste listar exiba exibir "
    "passe traga favor gentileza pf pra".split()
)
_HAVING = frozenset(
    "temos tem tenho tenha tinhamos tinha tinham tivemos ha havia haviam houve existe existem existiam restam resta "
    "sobram sobrou esta estao estavam estava ficam fica ficou possuimos possui possuem posso ter haver estar ser sao "
    "era eram foi somos".split()
)
_STOCK = frozenset(
    "estoque estoques disponivel disponiveis disponibilidade saldo total totais unidade unidades atual atuais "
    "atualmente agora hoje momento neste nesse esse este posicao".split()
)
_ENTITIES = frozenset(
    "produto produtos item itens sku skus categoria categorias estabelecimento estabelecimentos localidade "
    "localidades local locais loja lojas filial filiais armazem armazens deposito depositos".split()
)
_RANKING = frozenset("mais menos maior maiores menor menores top ranking primeiros primeiras melhores piores maximo minimo".split())
_FUNCTION = frozenset(
    "o a os as um uma uns umas de do da dos das no na nos nas em ao aos por pelo pela pelos pelas para com sem e ou "
    "nao ja ainda tambem so apenas somente cada todos todas todo toda nosso nossa nossos nossas seu sua voce voces "
    "gente ai aqui la isso".split()
)
# Time words are not names: they stay in the text so the parser refuses a relative
# period ("ontem"), or a month without a year, with the right reason.
_TIME = frozenset(
    "ontem amanha semana semanas mes meses trimestre semestre periodo recente recentes ultimo ultima ultimos ultimas "
    "passado passada dia dias ano anos desde ate apos depois antes durante entre verao inverno outono primavera natal "
    "feriado feriados janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro".split()
)
# Words of a forecast question ("qual a previsão de demanda do X na loja Y nos próximos 3 meses").
_FORECAST = frozenset(
    "previsao previsoes projecao prevista futura prever forecast demanda proximo proximos tres vamos vender".split()
)
_VOCABULARY = _QUESTION | _HAVING | _STOCK | _ENTITIES | _RANKING | _FUNCTION | _TIME | _FORECAST

#: Words that may sit *inside* a name ("motor de popa", "armazem de santos").
_CONNECTORS = frozenset({"de", "do", "da", "dos", "das"})
#: A place introduced by one of these is a location *name*'s first word.
_LOCATION_TYPES = frozenset({"loja", "filial", "armazem", "deposito", "galpao", "matriz"})
_PLURAL_LOCATION_TYPES = frozenset({"lojas", "filiais", "armazens", "depositos", "galpoes", "matrizes"})
_CATEGORY_WORDS = frozenset({"categoria", "categorias"})
_LOCATIVE = frozenset({"em", "na", "no", "nas", "nos", "ao", "aos"})
#: Verbs that place a position in the past: they need a date.
_PAST = frozenset("havia haviam tinhamos tinha tinham tivemos existiam estavam estava houve ficou sobrou".split())
_MAX_NAME_TOKENS = 4
_MAX_PHRASE_TOKENS = 6


@dataclass(frozen=True)
class Qualifiers:
    product: str | None = None
    category: str | None = None
    location: str | None = None
    #: The question is phrased in the past ("havia", "tinhamos").
    past_tense: bool = False
    #: Set when a qualifier cannot be represented: the whole question is refused.
    refusal: str | None = None
    #: The words that were not consumed as a qualifier (vocabulary), for the
    #: parser's remaining period/filter checks.
    remaining: str = ""


def _refuse(reason: str) -> Qualifiers:
    return Qualifiers(refusal=reason)


def _is_content(token: str) -> bool:
    return token not in _VOCABULARY


def _take_name(tokens: list[str], start: int, taken: list[bool], *, after_type: bool = False) -> int:
    """End (exclusive) of the name that starts at ``start``: content words,
    with linking words allowed between two of them (or right after a location
    type: "armazem de santos"); at most a few words."""
    end = start
    while end < len(tokens) and not taken[end] and end - start < _MAX_NAME_TOKENS:
        token = tokens[end]
        if _is_content(token):
            end += 1
        elif token in _CONNECTORS and (end > start or after_type) and end + 1 < len(tokens) and _is_content(tokens[end + 1]) and not taken[end + 1]:
            end += 1
        else:
            break
    return end


def extract_qualifiers(text: str) -> Qualifiers:
    tokens = text.split()
    taken = [False] * len(tokens)
    category = location = product = None

    def mark(start: int, end: int) -> None:
        for index in range(start, end):
            taken[index] = True

    for index, token in enumerate(tokens):
        if taken[index]:
            continue

        # -- category: "categoria <name>" ---------------------------------
        if token in _CATEGORY_WORDS:
            begin = index + 1
            if begin < len(tokens) and tokens[begin] in _CONNECTORS:
                begin += 1
            end = _take_name(tokens, begin, taken)
            if end > begin:
                if category is not None:
                    return _refuse("more than one category cannot be filtered at once")
                category = " ".join(tokens[begin:end])
                mark(index, end)
            continue

        # -- location: "<type> <name>" ------------------------------------
        centro = tokens[index : index + 3] == ["centro", "de", "distribuicao"]
        if centro or token in _LOCATION_TYPES:
            type_end = index + 3 if centro else index + 1
            end = _take_name(tokens, type_end, taken, after_type=True)
            if end > type_end:
                if location is not None:
                    return _refuse("more than one location cannot be filtered at once")
                location = " ".join(tokens[index:end])
                mark(index, end)
                continue
        if token in _PLURAL_LOCATION_TYPES or token in _LOCATION_TYPES or centro:
            before = tokens[index - 1] if index else ""
            if before in _LOCATIVE or (index >= 2 and tokens[index - 2] in _LOCATIVE and before in {"as", "os", "todas", "todos"}):
                return _refuse("filtering by a type of location ('nas lojas') is not supported; name the location (e.g. 'Loja X')")

    # -- product: the one run of words that is not vocabulary ------------------
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens):
        if taken[index] or not _is_content(tokens[index]):
            index += 1
            continue
        end = index + 1
        while end < len(tokens) and not taken[end]:
            if _is_content(tokens[end]):
                end += 1
            elif tokens[end] in _CONNECTORS and end + 1 < len(tokens) and _is_content(tokens[end + 1]) and not taken[end + 1]:
                end += 1
            else:
                break
        runs.append((index, end))
        index = end

    if len(runs) > 1:
        return _refuse("the question names more than one item or place; ask one at a time")
    if runs:
        start, end = runs[0]
        if end - start > _MAX_PHRASE_TOKENS:
            return _refuse("the name is too long to search")
        before = tokens[start - 1] if start else ""
        if before in _LOCATIVE:
            return _refuse("filtering by a place needs its type and name (e.g. 'Loja X', 'Armazém Y')")
        phrase = " ".join(tokens[start:end])
        if all(part.isdigit() for part in phrase.split()):
            return _refuse("filtering by a specific product identifier is not supported; use the product name")
        product = phrase
        # "do produto coletes": the noun that introduces the name is part of the phrase, not vocabulary
        if before in {"produto", "item", "sku"}:
            mark(start - 1, start)
        mark(start, end)

    remaining = " ".join(token for token, used in zip(tokens, taken) if not used)
    return Qualifiers(
        product=product,
        category=category,
        location=location,
        past_tense=any(token in _PAST for token in tokens),
        remaining=remaining,
    )


_QUANTITY_WORDS = frozenset({"quantos", "quantas"})


def item_after_quantity_word(text: str) -> str | None:
    """The item a "quantos <item> ..." question is about ("quantos coletes
    estão em risco?" -> "coletes"), or ``None``. Used to refuse such a question
    for a metric that cannot be filtered by item, instead of answering for all."""
    tokens = text.split()
    for index, token in enumerate(tokens[:-1]):
        if token not in _QUANTITY_WORDS:
            continue
        end = index + 1
        while end < len(tokens) and (_is_content(tokens[end]) or (tokens[end] in _CONNECTORS and end + 1 < len(tokens) and _is_content(tokens[end + 1]))):
            end += 1
        if end > index + 1:
            return " ".join(tokens[index + 1 : end])
    return None
