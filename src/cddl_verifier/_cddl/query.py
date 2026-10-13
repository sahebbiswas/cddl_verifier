"""Answer questions about type expressions held as nodes (#111, #175, #177).

The validator and EDN generator ask a type expression what it is built on:
its alternatives, its controls, whether it is a tag, a literal, a range, an
inline array. Every question is answered here from the node, never from text.
A function takes a ``Type`` (or any type node, which is a ``Type`` of one
alternative) and hands back nodes: a ``Type`` per alternative, per tag
content, per parenthesized alternative, per ``.cbor`` operand. :func:`head`
and ``ControlOp.arg_text`` give the canonical text of a node, for messages.

Type text is parsed once, where it enters the program (``typeexpr.as_type``),
not here: a function given a string raises ``TypeError``.
"""

from typing import List, Optional, Set, Tuple

from .ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control, FloatLit, GroupRef, IntLit, Member,
                  Major, Name, Paren, Range, Tag, TextLit, Type, walk)
from .printer import format_node


def _type(node) -> Type:
    """*node* as a ``Type``: a type node of one alternative is its own ``Type``."""
    if node.__class__ is Type or isinstance(node, Type):
        return node
    if isinstance(node, str):
        raise TypeError("query takes a type node, not text: parse it with typeexpr.as_type")
    return Type((node,))


def _single(expr):
    """The one alternative of *expr*, or ``None`` for a choice."""
    if isinstance(expr, str):
        raise TypeError("query takes a type node, not text: parse it with typeexpr.as_type")
    if isinstance(expr, Type):
        alternatives = expr.alternatives
        return alternatives[0] if len(alternatives) == 1 else None
    return expr


def alternatives(expr) -> list:
    """The top-level type choices in *expr*, one ``Type`` each.

    A type with one alternative is its own only alternative.
    """
    node = _type(expr)
    if len(node.alternatives) < 2:
        return [node]
    return [alt if isinstance(alt, Type) else Type((alt,)) for alt in node.alternatives]


def _unwrap_controls(node):
    """``(base, [(op, arg), ...])`` for a control chain, in source order."""
    ops = []
    while isinstance(node, Control):
        ops.append((node.op, node.arg))
        node = node.target
    ops.reverse()
    return node, ops


def head(expr) -> str:
    """The name or text of the type a single-alternative expression is built on.

    ``tstr .size 2`` gives ``tstr``, ``coswid.tag-id`` gives itself (a name
    may contain dots), ``#6.1(x)`` gives its canonical text. A choice gives
    the canonical text of the whole.
    """
    node = _single(expr)
    if node is None:
        return format_node(expr)
    base, _ = _unwrap_controls(node)
    if isinstance(base, Name):
        return base.name
    return format_node(base)


def base(expr):
    """The node a single-alternative expression is built on, controls looked through.

    ``tstr .size 2`` gives the ``Name`` ``tstr``. ``None`` for a choice.
    """
    node = _single(expr)
    return None if node is None else _unwrap_controls(node)[0]


class ControlOp:
    """One control operator applied to a type: ``.size 3`` is ``op='size'``, ``arg`` the ``3`` node."""

    __slots__ = ('op', 'arg')

    def __init__(self, op: str, arg):
        self.op = op
        self.arg = arg

    @property
    def arg_text(self) -> str:
        """The canonical text of the argument."""
        return format_node(self.arg)


def controls(expr) -> List[ControlOp]:
    """The control operators applied to a single-alternative expression."""
    node = _single(expr)
    if node is None:
        return []
    _, ops = _unwrap_controls(node)
    return [ControlOp(op, arg) for op, arg in ops]


class ParenParts:
    """A parenthesized type: the alternatives inside, and the controls applied to it."""

    __slots__ = ('inner', 'controls')

    def __init__(self, inner: list, controls: List[ControlOp]):
        self.inner = inner         # ``Type`` nodes
        self.controls = controls


def paren(expr) -> Optional[ParenParts]:
    """The parts of a parenthesized type (``(T)``, ``(a / b) .size 1``), else ``None``.

    ``(uint / nil) .size 1`` gives the inner alternatives ``uint`` and ``nil``
    and the ``.size`` control. Controls inside the parentheses stay with their
    alternative: ``(tstr .size 3) .regexp "a+"`` gives ``tstr .size 3``.
    """
    node = _single(expr)
    if node is None:
        return None
    inner_base, ops = _unwrap_controls(node)
    if not isinstance(inner_base, Paren):
        return None
    inner = [_type(alt) for alt in inner_base.type.alternatives]
    return ParenParts(inner, [ControlOp(op, arg) for op, arg in ops])


def without_controls(expr):
    """A single-alternative expression with its own controls removed.

    ``tstr .size 3`` gives ``tstr``, ``foo<uint>`` gives itself. A range
    (``1..5``), which cannot take a control, or a choice, gives ``None``.
    """
    node = _single(expr)
    if node is None:
        return None
    stripped, _ = _unwrap_controls(node)
    return None if isinstance(stripped, Range) else _type(stripped)


def choice_from(expr) -> Optional[ChoiceFrom]:
    """The node for ``&( group )`` or ``&name``, else ``None``."""
    node = _single(expr)
    return node if isinstance(node, ChoiceFrom) else None


def tag(expr) -> Optional[Tuple[int, Type]]:
    """``(tag, inner)`` for ``#6.N(inner)``, else ``None``."""
    node = _single(expr)
    if not isinstance(node, Tag) or not isinstance(node.tag, int):
        return None
    return node.tag, _type(node.type)


class ItemRef:
    """A reference to data items by their encoding: ``#``, ``#M``, ``#M.N``, ``#6(t)``.

    *node* is the ``AnyItem``, ``Major`` or ``Tag`` node. For a tag, *tag_type*
    is the ``<type>`` in ``#6.<type>(t)`` (``None`` for ``#6(t)``, any tag) and
    *inner* the ``t``, both ``Type`` nodes. For ``#7.<type>``, *tag_type* is
    the ``<type>``.
    """

    __slots__ = ('node', 'tag_type', 'inner')

    def __init__(self, node, tag_type=None, inner=None):
        self.node = node
        self.tag_type = tag_type
        self.inner = inner


def item_ref(expr) -> Optional[ItemRef]:
    """The item reference *expr* is, else ``None``.

    A tag with a number (``#6.32(tstr)``) is not one: :func:`tag` gives that.
    """
    node = _single(expr)
    if isinstance(node, Major):
        info = None if node.info is None or isinstance(node.info, int) else _type(node.info)
        return ItemRef(node, info)
    if isinstance(node, AnyItem):
        return ItemRef(node)
    if isinstance(node, Tag) and not isinstance(node.tag, int):
        tag_type = None if node.tag is None else _type(node.tag)
        return ItemRef(node, tag_type, _type(node.type))
    return None


def _embedded_control(expr, control: str) -> Optional[Tuple[str, Type]]:
    node = _single(expr)
    if node is None:
        return None
    stripped, ops = _unwrap_controls(node)
    if not isinstance(stripped, Name) or stripped.name not in ('bstr', 'bytes'):
        return None
    for op, arg in ops:
        if op == control:
            return stripped.name, _type(arg)
    return None


def cbor_control(expr) -> Optional[Tuple[str, Type]]:
    """``(base, inner)`` for ``bstr .cbor inner`` / ``bytes .cbor inner``.

    Chained controls are looked through, so ``bstr .cbor uint .size 1`` gives
    ``('bstr', uint)``: the embedded item must still be checked.
    """
    return _embedded_control(expr, 'cbor')


def cborseq_control(expr) -> Optional[Tuple[str, Type]]:
    """``(base, inner)`` for ``bstr .cborseq [* inner]`` / ``bytes .cborseq ...``.

    The byte string holds a CBOR sequence (RFC 8742); *inner* is the array type
    its items are matched against. Chained controls are looked through.
    """
    return _embedded_control(expr, 'cborseq')


def literal(expr):
    """The literal a single-alternative expression is built on, else ``None``.

    Controls are looked through: ``"t" .regexp "[a-z]+"`` gives ``"t"``.
    """
    node = _single(expr)
    if node is None:
        return None
    stripped, _ = _unwrap_controls(node)
    return stripped if isinstance(stripped, (IntLit, FloatLit, TextLit, BytesLit)) else None


def range_of(expr) -> Optional[Range]:
    """The ``Range`` node when *expr* is exactly a range (``0..10``, ``lo ... hi``)."""
    node = _single(expr)
    while isinstance(node, Paren) and len(node.type.alternatives) == 1:
        node = node.type.alternatives[0]
    return node if isinstance(node, Range) else None


def inline_array(expr) -> Optional[Tuple[str, Type]]:
    """``(occurrence, element)`` for ``[ + T ]`` / ``[ * T ]`` / ``[ T ]``.

    Only an array with one entry and no member key qualifies. The occurrence
    is ``'+'``, ``'*'`` or ``''``; the element is a ``Type``.
    """
    node = _single(expr)
    if not isinstance(node, Array):
        return None
    entries = [e for choice in node.group.choices for e in choice.entries]
    if len(entries) != 1:
        return None
    entry = entries[0]
    if isinstance(entry, GroupRef):
        element = Name(entry.name, entry.args)
    elif isinstance(entry, Member) and entry.key is None:
        element = entry.value
    else:
        return None
    occurrence = entry.occurrence.text if entry.occurrence is not None \
        and entry.occurrence.text in ('+', '*') else ''
    return occurrence, _type(element)


def is_array(expr) -> bool:
    """True for a single-alternative inline array (``[ ... ]``)."""
    return isinstance(_single(expr), Array)


def names(expr) -> Set[str]:
    """Every name referenced in *expr*."""
    return {node.name for node in walk(_type(expr)) if isinstance(node, (Name, GroupRef))}
