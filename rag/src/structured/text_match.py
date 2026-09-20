"""Controlled free-text matching for name filters (product, category, location).

A user phrase ("coletes", "Motor de Popa 6014", "Loja Leão") is never put in
SQL. It is reduced here to *search terms* — lowercase ASCII words made only of
``[a-z0-9]`` — and the compiler binds each term as a named parameter of a
``LIKE`` predicate over an accent-insensitive, case-insensitive version of a
registry-approved column. Because a term can only contain letters and digits,
it can carry neither a wildcard (``%``, ``_``) nor a quote.

Matching rule: every term must appear in the name (AND); a term matches
through any of its simple singular/plural variants (OR), so "coletes" finds
"Colete Salva-Vidas" and "motores" finds "Motor de Popa". Words that only
link ("de", "do", "e", ...) are not terms.

Nothing here knows any product, category or location: it is pure text
normalization.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["MAX_TEXT_LENGTH", "MAX_TERMS", "SQL_ACCENTS_FROM", "SQL_ACCENTS_TO", "normalize_text", "search_terms"]

MAX_TEXT_LENGTH = 80
#: More terms than this cannot be represented; the request is refused instead
#: of silently dropping the tail.
MAX_TERMS = 6

#: The SQL side folds these characters (and the hyphen) the same way
#: :func:`normalize_text` does: ``translate(lower(col), FROM, TO)``.
SQL_ACCENTS_FROM = "áàâãäéèêëíìîïóòôõöúùûüçñ-"
SQL_ACCENTS_TO = "aaaaaeeeeiiiiooooouuuucn "

_LINKING_WORDS = frozenset({"de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "um", "uma", "em", "no", "na", "nos", "nas", "com", "para", "por"})


def normalize_text(value: str) -> str:
    """Lowercase, accent-free, only ``[a-z0-9]`` words separated by single spaces."""
    decomposed = unicodedata.normalize("NFKD", value.lower())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _forms(token: str) -> tuple[str, ...]:
    """The token and its simple singular form (Portuguese plurals): the first
    rule that fits, so a plural never yields a stem that is not a word."""
    if len(token) <= 3 or not token.endswith("s"):
        return (token,)
    if token.endswith(("oes", "aes")) and len(token) > 4:
        return (token, token[:-3] + "ao")  # botoes -> botao
    if token.endswith(("ais", "eis", "ois", "uis")) and len(token) > 4:
        return (token, token[:-2] + "l")  # canais -> canal
    if token.endswith("ns"):
        return (token, token[:-2] + "m")  # bons -> bom
    if token.endswith(("res", "zes")) and len(token) > 4:
        return (token, token[:-2])  # motores -> motor, luzes -> luz
    return (token, token[:-1])  # coletes -> colete


def _variants(token: str) -> tuple[str, ...]:
    """The forms worth searching: a form that contains another one (``coletes``
    contains ``colete``) is implied by it, so only the shortest are kept."""
    forms = _forms(token)
    return tuple(form for form in forms if not any(other != form and other in form for other in forms))


def search_terms(phrase: str) -> tuple[tuple[str, ...], ...]:
    """One tuple of variants per term, in phrase order. Empty when the phrase
    has no term; raises ``ValueError`` when it has more than ``MAX_TERMS``."""
    tokens = [token for token in normalize_text(phrase).split() if token not in _LINKING_WORDS]
    if len(tokens) > MAX_TERMS:
        raise ValueError(f"at most {MAX_TERMS} search terms are supported, got {len(tokens)}")
    return tuple(_variants(token) for token in tokens)
