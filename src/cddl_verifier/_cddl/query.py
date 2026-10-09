"""Answer questions about the type text in the ``CDDLParser`` tables (#111).

The validator and EDN generator still pass type expressions around as text
(``'tstr .size (1..64)'``, ``'#6.501(m)'``, ``'uint / tstr'``). Every question
they ask about that text is answered here, from the parsed node, instead of
with regular expressions. Text is parsed with the real grammar
(:func:`parse_type_expr`) and cached. Pieces handed back are slices of the
original text, so their spelling does not change.

Text that does not parse as a type (a plain name the caller invented, or a
group fragment) is treated as opaque: no alternatives, controls, tag or
literal.

Removing the text from the tables altogether is tracked in a follow-up to #111.
"""

from functools import lru_cache
from typing import List, NamedTuple, Optional, Set, Tuple

from .ast import (Array, BytesLit, Control, FloatLit, GroupRef, IntLit, Member, Name,
                  Tag, TextLit, Type, walk)
from .errors import CDDLSyntaxError
from .parser import parse_type_expr


class Parsed(NamedTuple):
    node: Type
    text: str

    def slice(self, node) -> str:
        return self.text[node.span.start:node.span.end].strip()


#: Longer type text is parsed each time instead of being kept in the
#: process-wide cache, so large or many distinct schemas cannot pin memory.
_CACHE_MAX_TEXT = 1024


def parse(text: str) -> Optional[Parsed]:
    """The parsed type for *text*, or ``None`` if it is not one type expression."""
    if len(text) > _CACHE_MAX_TEXT:
        return _parse(text)
    return _parse_cached(text)


def _parse(text: str) -> Optional[Parsed]:
    try:
        return Parsed(parse_type_expr(text), text)
    except (CDDLSyntaxError, RecursionError):
        return None


_parse_cached = lru_cache(maxsize=4096)(_parse)


def _single(text: str):
    """``(parsed, node)`` for text with exactly one alternative, else ``None``."""
    parsed = parse(text)
    if parsed is None or len(parsed.node.alternatives) != 1:
        return None
    return parsed, parsed.node.alternatives[0]


def alternatives(text: str) -> List[str]:
    """The top-level type choices in *text* (``'a / b'`` gives ``['a', 'b']``).

    Text that is not a type expression is one alternative: itself.
    """
    parsed = parse(text)
    if parsed is None or len(parsed.node.alternatives) < 2:
        return [text.strip()]
    return [parsed.slice(alt) for alt in parsed.node.alternatives]


def _unwrap_controls(node):
    """``(base, [(op, arg), ...])`` for a control chain, in source order."""
    ops = []
    while isinstance(node, Control):
        ops.append((node.op, node.arg))
        node = node.target
    ops.reverse()
    return node, ops


def head(text: str) -> str:
    """The type a single-alternative expression is built on.

    ``'tstr .size 2'`` gives ``'tstr'``, ``'coswid.tag-id'`` gives itself (a
    name may contain dots), ``'#6.1(x)'`` gives itself. Anything else, or
    text that does not parse, gives the stripped text.
    """
    single = _single(text)
    if single is None:
        return text.strip()
    parsed, node = single
    base, _ = _unwrap_controls(node)
    if isinstance(base, Name):
        return base.name
    return parsed.slice(base)


class ControlOp(NamedTuple):
    op: str
    arg: object       # the argument node
    arg_text: str     # its source spelling
    parsed: Parsed


def controls(text: str) -> List[ControlOp]:
    """The control operators applied to a single-alternative expression."""
    single = _single(text)
    if single is None:
        return []
    parsed, node = single
    _, ops = _unwrap_controls(node)
    return [ControlOp(op, arg, parsed.slice(arg), parsed) for op, arg in ops]


def tag(text: str) -> Optional[Tuple[int, str]]:
    """``(tag, inner text)`` for ``#6.N(inner)``, else ``None``."""
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    if not isinstance(node, Tag) or not isinstance(node.tag, int):
        return None
    return node.tag, parsed.slice(node.type)


def cbor_control(text: str) -> Optional[Tuple[str, str]]:
    """``(base, inner text)`` for ``bstr .cbor inner`` / ``bytes .cbor inner``.

    Chained controls are looked through, so ``bstr .cbor uint .size 1`` gives
    ``('bstr', 'uint')``: the embedded item must still be checked.
    """
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    base, ops = _unwrap_controls(node)
    if not isinstance(base, Name) or base.name not in ('bstr', 'bytes'):
        return None
    for op, arg in ops:
        if op == 'cbor':
            return base.name, parsed.slice(arg)
    return None


def literal(text: str):
    """The literal a single-alternative expression is built on, else ``None``.

    Controls are looked through: ``'"t" .regexp "[a-z]+"'`` gives ``"t"``.
    """
    single = _single(text)
    if single is None:
        return None
    node, _ = _unwrap_controls(single[1])
    return node if isinstance(node, (IntLit, FloatLit, TextLit, BytesLit)) else None


def inline_array(text: str) -> Optional[Tuple[str, str]]:
    """``(occurrence, element text)`` for ``[ + T ]`` / ``[ * T ]`` / ``[ T ]``.

    Only an array with one entry and no member key qualifies. The occurrence
    is ``'+'``, ``'*'`` or ``''``.
    """
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    if not isinstance(node, Array):
        return None
    entries = [e for choice in node.group.choices for e in choice.entries]
    if len(entries) != 1:
        return None
    entry = entries[0]
    if isinstance(entry, GroupRef):
        element = Name(entry.name, entry.args, span=_after_occurrence(entry))
    elif isinstance(entry, Member) and entry.key is None:
        element = entry.value
    else:
        return None
    occurrence = entry.occurrence.text if entry.occurrence is not None \
        and entry.occurrence.text in ('+', '*') else ''
    return occurrence, parsed.slice(element)


def _after_occurrence(entry):
    from .ast import Span
    start = entry.occurrence.span.end if entry.occurrence is not None else entry.span.start
    return Span(start, entry.span.end)


def is_array(text: str) -> bool:
    """True for a single-alternative inline array (``[ ... ]``)."""
    single = _single(text)
    return single is not None and isinstance(single[1], Array)


def names(text: str) -> Set[str]:
    """Every name referenced in *text* (empty if it does not parse)."""
    parsed = parse(text)
    if parsed is None:
        return set()
    return {node.name for node in walk(parsed.node) if isinstance(node, (Name, GroupRef))}
