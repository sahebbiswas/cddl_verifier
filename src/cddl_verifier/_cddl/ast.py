"""Typed, immutable syntax tree for CDDL (RFC 8610, as updated by RFC 9682).

See ``docs/CDDL_AST_DESIGN.md`` §4 and §6. Every node has a ``span``.
``span`` and the ``comment`` of group entries are excluded from equality, so
two trees with the same syntax compare equal wherever they came from.

The parser only records syntax. Whether a ``Name`` is a type, a group, a
generic parameter or undefined is decided later (#70).
"""

from bisect import bisect_right
from dataclasses import dataclass, field, fields
from typing import Iterator, List, Optional, Tuple, Union


@dataclass(frozen=True)
class Span:
    """Half-open range ``[start, end)`` of code-point offsets into the source."""

    start: int
    end: int


_NO_SPAN = Span(0, 0)


def _span():
    return field(default=_NO_SPAN, compare=False, repr=False)


class Source:
    """Schema text plus a lazily built line table for line/column lookups."""

    def __init__(self, text: str, name: Optional[str] = None):
        self.text = text
        self.name = name
        self._line_starts: Optional[List[int]] = None

    def line_col(self, offset: int) -> Tuple[int, int]:
        """Return the 1-based ``(line, column)`` of *offset*.

        ``\\r\\n``, ``\\n`` and a lone ``\\r`` each end a line. A tab counts as
        one column.
        """
        if self._line_starts is None:
            starts = [0]
            text = self.text
            i, n = 0, len(text)
            while i < n:
                c = text[i]
                if c == '\n' or (c == '\r' and not (i + 1 < n and text[i + 1] == '\n')):
                    starts.append(i + 1)
                i += 1
            self._line_starts = starts
        line = bisect_right(self._line_starts, offset)
        return line, offset - self._line_starts[line - 1] + 1

    def __repr__(self) -> str:
        return f"Source(name={self.name!r}, {len(self.text)} chars)"


# --------------------------------------------------------------------- types

@dataclass(frozen=True)
class IntLit:
    value: int
    text: str
    span: Span = _span()


@dataclass(frozen=True)
class FloatLit:
    value: float
    text: str
    span: Span = _span()


@dataclass(frozen=True)
class TextLit:
    value: str
    text: str
    span: Span = _span()


@dataclass(frozen=True)
class BytesLit:
    value: bytes
    text: str
    span: Span = _span()


@dataclass(frozen=True)
class Name:
    """A reference: ``tstr``, ``$socket``, ``non-empty<{...}>``."""

    name: str
    args: Tuple["Type1", ...] = ()
    span: Span = _span()


@dataclass(frozen=True)
class Paren:
    """``( type )``."""

    type: "Type"
    span: Span = _span()


@dataclass(frozen=True)
class Map:
    """``{ group }``."""

    group: "Group"
    span: Span = _span()


@dataclass(frozen=True)
class Array:
    """``[ group ]``."""

    group: "Group"
    span: Span = _span()


@dataclass(frozen=True)
class Unwrap:
    """``~name``."""

    name: str
    args: Tuple["Type1", ...] = ()
    span: Span = _span()


@dataclass(frozen=True)
class ChoiceFrom:
    """``&( group )`` (``group`` set) or ``&name`` (``name`` set)."""

    group: Optional["Group"] = None
    name: Optional[str] = None
    args: Tuple["Type1", ...] = ()
    span: Span = _span()


#: The number after ``#N.``: an integer, a ``<type>`` (RFC 9682), or absent.
HeadNumber = Union[int, "Type", None]


@dataclass(frozen=True)
class Tag:
    """``#6.N(type)``, ``#6(type)`` or ``#6.<type>(type)``."""

    tag: HeadNumber
    type: "Type"
    span: Span = _span()


@dataclass(frozen=True)
class Major:
    """``#M`` or ``#M.N`` / ``#7.<type>``: a major type with optional info."""

    major: int
    info: HeadNumber = None
    span: Span = _span()


@dataclass(frozen=True)
class AnyItem:
    """``#``: any data item."""

    span: Span = _span()


Type2 = Union[IntLit, FloatLit, TextLit, BytesLit, Name, Paren, Map, Array,
              Unwrap, ChoiceFrom, Tag, Major, AnyItem]


@dataclass(frozen=True)
class Range:
    """``low..high`` (``inclusive``) or ``low...high``."""

    low: Type2
    high: Type2
    inclusive: bool
    span: Span = _span()


@dataclass(frozen=True)
class Control:
    """``target .op arg``; ``op`` has no leading dot."""

    target: Type2
    op: str
    arg: Type2
    span: Span = _span()


Type1 = Union[Type2, Range, Control]


@dataclass(frozen=True)
class Type:
    """``a / b / c``. Always at least one alternative, in source order."""

    alternatives: Tuple[Type1, ...]
    span: Span = _span()


# -------------------------------------------------------------------- groups

@dataclass(frozen=True)
class Occurrence:
    """``?`` ``(0, 1)``, ``*`` ``(0, None)``, ``+`` ``(1, None)``, ``n*m``."""

    min: int
    max: Optional[int]
    text: str
    span: Span = _span()


@dataclass(frozen=True)
class MemberKey:
    """``name:`` (bareword), ``1:`` / ``"x":`` (value), ``K =>`` (type)."""

    kind: str
    cut: bool = False
    name: Optional[str] = None
    value: Optional[Type2] = None
    type: Optional[Type1] = None
    span: Span = _span()


@dataclass(frozen=True)
class Member:
    occurrence: Optional[Occurrence]
    key: Optional[MemberKey]
    value: Type
    comment: Optional[str] = field(default=None, compare=False)
    span: Span = _span()


@dataclass(frozen=True)
class GroupRef:
    """A bare name in a group. #70 decides whether it names a group or a type."""

    occurrence: Optional[Occurrence]
    name: str
    args: Tuple[Type1, ...] = ()
    comment: Optional[str] = field(default=None, compare=False)
    span: Span = _span()


@dataclass(frozen=True)
class InlineGroup:
    """``( group )`` used as a group entry."""

    occurrence: Optional[Occurrence]
    group: "Group"
    comment: Optional[str] = field(default=None, compare=False)
    span: Span = _span()


GroupEntry = Union[Member, GroupRef, InlineGroup]


@dataclass(frozen=True)
class GroupChoice:
    entries: Tuple[GroupEntry, ...]
    span: Span = _span()


@dataclass(frozen=True)
class Group:
    """``a // b``. Always at least one choice; a choice may be empty."""

    choices: Tuple[GroupChoice, ...]
    span: Span = _span()


# --------------------------------------------------------------------- rules

@dataclass(frozen=True)
class TypeRule:
    """``name = type`` or ``name /= type``.

    ``maybe_group`` is set when the right-hand side is one name, or one name
    in parentheses, so #70 can reclassify the rule once that name is known.
    """

    name: str
    params: Tuple[str, ...]
    assign: str
    type: Type
    maybe_group: bool = False
    span: Span = _span()


@dataclass(frozen=True)
class GroupRule:
    """``name = group-entry`` or ``name //= group-entry``."""

    name: str
    params: Tuple[str, ...]
    assign: str
    entry: GroupEntry
    span: Span = _span()


Rule = Union[TypeRule, GroupRule]


@dataclass(frozen=True)
class Schema:
    rules: Tuple[Rule, ...]
    source: Optional[Source] = field(default=None, compare=False, repr=False)
    span: Span = _span()


Node = Union[Schema, Rule, Type, Type1, Group, GroupChoice, GroupEntry,
             MemberKey, Occurrence]


# ------------------------------------------------------------------ helpers

def walk(node) -> Iterator:
    """Yield *node* and all its descendants, depth first, in source order."""
    yield node
    for f in fields(node):
        if f.name in ('span', 'source', 'comment'):
            continue
        child = getattr(node, f.name)
        if isinstance(child, tuple):
            for item in child:
                if _is_node(item):
                    yield from walk(item)
        elif _is_node(child):
            yield from walk(child)


def _is_node(value) -> bool:
    return hasattr(value, '__dataclass_fields__') and not isinstance(value, Span)


def registered_label(member) -> Optional[Tuple[str, Type2]]:
    """Return ``(name, value)`` for a member keyed ``&(name: value) => T``.

    This is the "IANA registered parameter" form used by CoRIM and CoSWID.
    It returns ``None`` for any other entry.
    """
    if not isinstance(member, Member) or member.key is None or member.key.kind != 'type':
        return None
    key = member.key.type
    if not isinstance(key, ChoiceFrom) or key.group is None:
        return None
    choices = key.group.choices
    if len(choices) != 1 or len(choices[0].entries) != 1:
        return None
    inner = choices[0].entries[0]
    if (not isinstance(inner, Member) or inner.occurrence is not None
            or inner.key is None or inner.key.kind != 'bareword'
            or len(inner.value.alternatives) != 1):
        return None
    return inner.key.name, inner.value.alternatives[0]


def entry_type(entry) -> Optional[Type]:
    """Return the type an entry stands for, if it has no key or occurrence.

    ``GroupRef("x")`` gives ``Type([Name("x")])``. ``InlineGroup`` gives a
    ``Paren`` around its single entry's type.
    """
    if getattr(entry, 'occurrence', None) is not None:
        return None
    if isinstance(entry, Member):
        return entry.value if entry.key is None else None
    if isinstance(entry, GroupRef):
        return Type((Name(entry.name, entry.args, span=entry.span),), span=entry.span)
    if isinstance(entry, InlineGroup):
        choices = entry.group.choices
        if len(choices) != 1 or len(choices[0].entries) != 1:
            return None
        inner = entry_type(choices[0].entries[0])
        if inner is None:
            return None
        return Type((Paren(inner, span=entry.span),), span=entry.span)
    return None
