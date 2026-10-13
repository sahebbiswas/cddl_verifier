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

Every function also takes a node (a ``Type``, or any type node) in place of
text (#175). Then the pieces it hands back are nodes too (a ``Type`` per
alternative, per tag content, per parenthesized alternative), not slices of
text, and :func:`head` and ``ControlOp.arg_text`` give the canonical text of the
node. Removing the text path altogether is tracked in #177.
"""

from functools import lru_cache
from typing import List, NamedTuple, Optional, Set, Tuple

from .ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control, FloatLit, GroupRef, IntLit, Member,
                  Major, Name, Paren, Range, Tag, TextLit, Type, walk)
from .errors import CDDLSyntaxError
from .parser import parse_type_expr
from .printer import format_node


class Parsed(NamedTuple):
    node: Type
    text: Optional[str]    # ``None`` when the caller gave a node

    def slice(self, node) -> str:
        """The text of *node*: its source spelling, or its canonical text for a node input."""
        if self.text is None:
            return format_node(node)
        return self.text[node.span.start:node.span.end].strip()

    def piece(self, node):
        """*node* as the caller's kind of type: a slice of text, or a ``Type`` node."""
        if self.text is not None:
            return self.text[node.span.start:node.span.end].strip()
        return node if isinstance(node, Type) else Type((node,))


#: Longer type text is parsed each time instead of being kept in the
#: process-wide cache, so large or many distinct schemas cannot pin memory.
_CACHE_MAX_TEXT = 1024


def parse(text) -> Optional[Parsed]:
    """The parsed type for *text* (or a node), or ``None`` if it is not one type expression."""
    if not isinstance(text, str):
        return Parsed(text if isinstance(text, Type) else Type((text,)), None)
    if len(text) > _CACHE_MAX_TEXT:
        return _parse(text)
    return _parse_cached(text)


def _parse(text: str) -> Optional[Parsed]:
    try:
        return Parsed(parse_type_expr(text), text)
    except (CDDLSyntaxError, RecursionError):
        return None


_parse_cached = lru_cache(maxsize=4096)(_parse)


#: Stands for "the caller gave a node": it has no text, so pieces are nodes.
_NODE_INPUT = Parsed(None, None)


def _single(text):
    """``(parsed, node)`` for text (or a node) with exactly one alternative, else ``None``."""
    if text.__class__ is Type:
        alternatives = text.alternatives
        return (_NODE_INPUT, alternatives[0]) if len(alternatives) == 1 else None
    if not isinstance(text, str):
        return (_NODE_INPUT, text)
    parsed = parse(text)
    if parsed is None or len(parsed.node.alternatives) != 1:
        return None
    return parsed, parsed.node.alternatives[0]


def alternatives(text) -> list:
    """The top-level type choices in *text* (``'a / b'`` gives ``['a', 'b']``).

    Text that is not a type expression is one alternative: itself. For a node,
    one ``Type`` per alternative; a node with one alternative is its own.
    """
    if not isinstance(text, str):
        node = text if isinstance(text, Type) else Type((text,))
        if len(node.alternatives) < 2:
            return [node]
        return [alt if isinstance(alt, Type) else Type((alt,)) for alt in node.alternatives]
    parsed = parse(text)
    if parsed is None or len(parsed.node.alternatives) < 2:
        return [text.strip()]
    return [parsed.piece(alt) for alt in parsed.node.alternatives]


def _unwrap_controls(node):
    """``(base, [(op, arg), ...])`` for a control chain, in source order."""
    ops = []
    while isinstance(node, Control):
        ops.append((node.op, node.arg))
        node = node.target
    ops.reverse()
    return node, ops


def head(text) -> str:
    """The type a single-alternative expression is built on.

    ``'tstr .size 2'`` gives ``'tstr'``, ``'coswid.tag-id'`` gives itself (a
    name may contain dots), ``'#6.1(x)'`` gives itself. Anything else, or
    text that does not parse, gives the stripped text.
    """
    single = _single(text)
    if single is None:
        return text.strip() if isinstance(text, str) else format_node(text)
    parsed, node = single
    base, _ = _unwrap_controls(node)
    if isinstance(base, Name):
        return base.name
    return parsed.slice(base)


def base(text):
    """The node a single-alternative expression is built on, controls looked through.

    ``'tstr .size 2'`` gives the ``Name`` ``tstr``. ``None`` for a choice or
    text that does not parse.
    """
    single = _single(text)
    return None if single is None else _unwrap_controls(single[1])[0]


class ControlOp(NamedTuple):
    op: str
    arg: object       # the argument node
    arg_text: str     # its source spelling
    parsed: Parsed


def controls(text) -> List[ControlOp]:
    """The control operators applied to a single-alternative expression."""
    single = _single(text)
    if single is None:
        return []
    parsed, node = single
    _, ops = _unwrap_controls(node)
    return [ControlOp(op, arg, parsed.slice(arg), parsed) for op, arg in ops]


class ParenParts(NamedTuple):
    inner: list                # the alternatives inside the parentheses (text, or ``Type`` nodes)
    controls: List[ControlOp]  # controls applied to the parenthesized type


def paren(text) -> Optional[ParenParts]:
    """The parts of a parenthesized type (``(T)``, ``(a / b) .size 1``), else ``None``.

    ``'(uint / nil) .size 1'`` gives ``inner=['uint', 'nil']`` and the
    ``.size`` control. Controls inside the parentheses stay with their
    alternative: ``'(tstr .size 3) .regexp "a+"'`` gives ``['tstr .size 3']``.
    """
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    base, ops = _unwrap_controls(node)
    if not isinstance(base, Paren):
        return None
    inner = [parsed.piece(alt) for alt in base.type.alternatives]
    return ParenParts(inner, [ControlOp(op, arg, parsed.slice(arg), parsed) for op, arg in ops])


def without_controls(text):
    """A single-alternative expression with its own controls removed.

    ``'tstr .size 3'`` gives ``'tstr'``, ``'foo<uint>'`` gives itself. A
    range (``1..5``), which cannot take a control, or text that is not one
    alternative, gives ``None``.
    """
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    base, _ = _unwrap_controls(node)
    return None if isinstance(base, Range) else parsed.piece(base)


def choice_from(text) -> Optional[ChoiceFrom]:
    """The node for ``&( group )`` or ``&name``, else ``None``."""
    single = _single(text)
    if single is None or not isinstance(single[1], ChoiceFrom):
        return None
    return single[1]


def tag(text) -> Optional[Tuple[int, object]]:
    """``(tag, inner text)`` for ``#6.N(inner)``, else ``None``."""
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    if not isinstance(node, Tag) or not isinstance(node.tag, int):
        return None
    return node.tag, parsed.piece(node.type)


class ItemRef(NamedTuple):
    """A reference to data items by their encoding: ``#``, ``#M``, ``#M.N``, ``#6(t)``.

    *node* is the ``AnyItem``, ``Major`` or ``Tag`` node. For a tag, *tag_type*
    is the text of ``<type>`` in ``#6.<type>(t)`` (``None`` for ``#6(t)``, any
    tag) and *inner* the text of ``t``. For ``#7.<type>``, *tag_type* is the
    text of ``<type>``.
    """

    node: object
    tag_type: object = None    # text, or a ``Type`` node
    inner: object = None       # text, or a ``Type`` node


def item_ref(text) -> Optional[ItemRef]:
    """The item reference *text* is, else ``None``.

    A tag with a number (``#6.32(tstr)``) is not one: :func:`tag` gives that.
    """
    single = _single(text)
    if single is None:
        return None
    parsed, node = single
    if isinstance(node, Major):
        info = None if node.info is None or isinstance(node.info, int) \
            else parsed.piece(node.info)
        return ItemRef(node, info)
    if isinstance(node, AnyItem):
        return ItemRef(node)
    if isinstance(node, Tag) and not isinstance(node.tag, int):
        tag_type = None if node.tag is None else parsed.piece(node.tag)
        return ItemRef(node, tag_type, parsed.piece(node.type))
    return None


def cbor_control(text) -> Optional[Tuple[str, object]]:
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
            return base.name, parsed.piece(arg)
    return None


def literal(text):
    """The literal a single-alternative expression is built on, else ``None``.

    Controls are looked through: ``'"t" .regexp "[a-z]+"'`` gives ``"t"``.
    """
    single = _single(text)
    if single is None:
        return None
    node, _ = _unwrap_controls(single[1])
    return node if isinstance(node, (IntLit, FloatLit, TextLit, BytesLit)) else None


def range_of(text) -> Optional[Range]:
    """The ``Range`` node when *text* is exactly a range (``0..10``, ``lo ... hi``)."""
    single = _single(text)
    if single is None:
        return None
    node = single[1]
    while isinstance(node, Paren) and len(node.type.alternatives) == 1:
        node = node.type.alternatives[0]
    return node if isinstance(node, Range) else None


def inline_array(text) -> Optional[Tuple[str, object]]:
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
    return occurrence, parsed.piece(element)


def _after_occurrence(entry):
    from .ast import Span
    start = entry.occurrence.span.end if entry.occurrence is not None else entry.span.start
    return Span(start, entry.span.end)


def is_array(text) -> bool:
    """True for a single-alternative inline array (``[ ... ]``)."""
    single = _single(text)
    return single is not None and isinstance(single[1], Array)


def names(text) -> Set[str]:
    """Every name referenced in *text* (empty if it does not parse)."""
    parsed = parse(text)
    if parsed is None:
        return set()
    return {node.name for node in walk(parsed.node) if isinstance(node, (Name, GroupRef))}
