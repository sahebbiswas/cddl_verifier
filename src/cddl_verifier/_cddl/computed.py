"""Computed literals: ``.plus``, ``.cat`` and ``.det`` (RFC 9165 section 2).

``3 .plus 5`` is the number 8, ``"a" .cat "b"`` the string ``"ab"`` and ``.det`` is
``.cat`` with both sides dedented first. Their operands are types, so the result is
a type too: a set of values, one for each combination of a value of the target and
a value of the controller. :func:`evaluate` computes it for operands that are
literals, names of rules that are literals, ranges of numbers (for ``.plus``), choices
of those and computed literals themselves, and refuses anything else (``uint``,
``any``, a generic parameter that is still open): a tool cannot list the values.

A result is a list of :class:`Value` for single values and :class:`Interval` for a
range of numbers; :func:`contains` says whether a data item is one of them.
"""

import math
from typing import Dict, List, Optional, Tuple, Union

from .ast import (Array, BytesLit, Control, FloatLit, GroupRef, InlineGroup, IntLit, Member, Name, Paren,
                  Range, TextLit, Type)

#: The controls this module computes.
OPERATORS = frozenset({'plus', 'cat', 'det'})

#: How many values a computation may produce (``("a" / "b") .cat ("c" / "d")`` has four).
MAX_VALUES = 4096

#: How long one computed string may be, and how much text all the results of one computation may hold.
MAX_STRING = 1 << 20
MAX_TOTAL = 16 << 20

_MAX_DEPTH = 64


class ComputeError(ValueError):
    """An operand cannot be computed with, or the result is not valid."""


class Value:
    """One value: ``kind`` is ``int``, ``float``, ``text`` or ``bytes``."""

    __slots__ = ('kind', 'value')

    def __init__(self, kind: str, value):
        self.kind, self.value = kind, value

    def __eq__(self, other):
        return isinstance(other, Value) and (self.kind, self.value) == (other.kind, other.value)

    def __hash__(self):
        return hash((self.kind, self.value))

    def __repr__(self):
        return f"Value({self.kind!r}, {self.value!r})"


class Interval:
    """The numbers from ``low`` to ``high`` (``inclusive`` of the upper bound); ``kind`` is ``int`` or ``float``."""

    __slots__ = ('kind', 'low', 'high', 'inclusive')

    def __init__(self, kind: str, low, high, inclusive: bool):
        self.kind, self.low, self.high, self.inclusive = kind, low, high, inclusive

    def __repr__(self):
        return f"Interval({self.kind!r}, {self.low!r}, {self.high!r}, {self.inclusive!r})"


Item = Union[Value, Interval]


def evaluate(rules: Dict[str, object], node, _depth: int = 0) -> List[Item]:
    """The values of *node*, a type that is built from literals.

    Args:
        rules: The resolved rules by name (``RuleDef`` objects), to follow names.
        node: A ``Type`` or one of its alternatives.

    Raises:
        ComputeError: *node* is not made of literals, or a computation is invalid
            (a number added to a string, text that is not valid UTF-8).
    """
    if _depth > _MAX_DEPTH:
        raise ComputeError("the definition is nested or recursive too deeply to compute")
    if isinstance(node, Type):
        items: List[Item] = []
        for alternative in node.alternatives:
            items.extend(evaluate(rules, alternative, _depth + 1))
            _bounded(items)
        return items
    if isinstance(node, IntLit):
        return [Value('int', node.value)]
    if isinstance(node, FloatLit):
        return [Value('float', node.value)]
    if isinstance(node, TextLit):
        return [Value('text', node.value)]
    if isinstance(node, BytesLit):
        return [Value('bytes', node.value)]
    if isinstance(node, Paren):
        return evaluate(rules, node.type, _depth + 1)
    if isinstance(node, Range):
        return [_interval(rules, node, _depth)]
    if isinstance(node, Name):
        rule = rules.get(node.name)
        if rule is None or node.args or getattr(rule, 'kind', None) != 'type' or rule.params:
            raise ComputeError(f"'{node.name}' is not a literal or a rule that is made of literals")
        return evaluate(rules, rule.type(), _depth + 1)
    if isinstance(node, Control) and node.op in OPERATORS:
        left = evaluate(rules, node.target, _depth + 1)
        right = evaluate(rules, node.arg, _depth + 1)
        if len(left) * len(right) > MAX_VALUES:  # before building the product, not after
            raise ComputeError(f"the computation has more than {MAX_VALUES} results")
        if node.op == 'plus':
            return _bounded([_plus(a, b) for a in left for b in right])
        return _bounded([_concatenate(a, b, node.op == 'det') for a in left for b in right])
    raise ComputeError("only literals, names of rules that are literals, ranges and computed literals "
                       "can be computed with")


def _bounded(items: List[Item]) -> List[Item]:
    """Raise ComputeError when the results are too many or hold too much text."""
    if len(items) > MAX_VALUES:
        raise ComputeError(f"the computation has more than {MAX_VALUES} results")
    if sum(len(item.value) for item in items if isinstance(item, Value) and item.kind in ('text', 'bytes')) \
            > MAX_TOTAL:
        raise ComputeError("the results of the computation hold too much text")
    return items


def _interval(rules, node: Range, depth: int) -> Interval:
    """A range node as an Interval."""
    bounds = []
    for bound in (node.low, node.high):
        values = evaluate(rules, bound, depth + 1)
        if len(values) != 1 or not isinstance(values[0], Value) or values[0].kind not in ('int', 'float'):
            raise ComputeError("the bounds of a range must be single numbers")
        bounds.append(values[0])
    low, high = bounds
    if low.kind != high.kind:
        raise ComputeError("range bounds must both be integers or both be floats")
    return Interval(low.kind, low.value, high.value, node.inclusive)


# ----------------------------------------------------------------------- plus

def _number(item: Item) -> Item:
    """*item* if it is a number or a range of numbers, else a ComputeError."""
    if isinstance(item, Interval) or item.kind in ('int', 'float'):
        return item
    raise ComputeError(f"'.plus' needs numbers, not {_describe(item)}")


def _describe(item: Item) -> str:
    """A short name for the kind of *item*, for error messages."""
    return {'text': 'a text string', 'bytes': 'a byte string'}.get(getattr(item, 'kind', ''), 'a number')


def _sum(kind: str, left, right):
    """A sum in the type of the target (RFC 9165: a float target gives a float, an integer
    target the floor of the sum)."""
    total = left + right
    if kind == 'int':
        if isinstance(total, float):
            if math.isinf(total) or math.isnan(total):
                raise ComputeError("the sum is not an integer")
            return math.floor(total)
        return total
    return float(total)


def _plus(left: Item, right: Item) -> Item:
    """The sum of two operands of ``.plus`` (ranges shift)."""
    left, right = _number(left), _number(right)
    if isinstance(left, Value) and isinstance(right, Value):
        return Value(left.kind, _sum(left.kind, left.value, right.value))
    low_a, high_a = (left.low, left.high) if isinstance(left, Interval) else (left.value, left.value)
    low_b, high_b = (right.low, right.high) if isinstance(right, Interval) else (right.value, right.value)
    kind = left.kind
    inclusive = (left.inclusive if isinstance(left, Interval) else True) and \
                (right.inclusive if isinstance(right, Interval) else True)
    return Interval(kind, _sum(kind, low_a, low_b), _sum(kind, high_a, high_b), inclusive)


# ------------------------------------------------------------------ cat, det

def dedent(text):
    """*text* (a ``str`` or ``bytes``) dedented as RFC 9165 defines it.

    The smallest number of leading spaces on the lines that are not blank is
    removed from every line; a blank line (spaces only, or empty) loses all its
    leading spaces if it has fewer.
    """
    is_text = isinstance(text, str)
    space, newline = (' ', '\n') if is_text else (b' ', b'\n')
    blank = (' \t\r' if is_text else b' \t\r')
    lines = text.split(newline)
    indents = [len(line) - len(line.lstrip(space)) for line in lines if line.strip(blank)]
    amount = min(indents) if indents else 0
    result = []
    for line in lines:
        strip = min(amount, len(line) - len(line.lstrip(space)))
        result.append(line[strip:])
    return newline.join(result)


def _concatenate(left: Item, right: Item, dedenting: bool) -> Item:
    """Join two operands of ``.cat`` or ``.det`` in the string type of the left one."""
    for item in (left, right):
        if not isinstance(item, Value) or item.kind not in ('text', 'bytes'):
            raise ComputeError(f"'.{'det' if dedenting else 'cat'}' needs strings, not "
                               f"{'a range' if isinstance(item, Interval) else _describe(item)}")
    a, b = left.value, right.value
    if len(a) + len(b) > MAX_STRING:
        raise ComputeError(f"the result of the concatenation is longer than {MAX_STRING} characters")
    if dedenting:
        a, b = dedent(a), dedent(b)
    a_bytes = a.encode('utf-8') if left.kind == 'text' else a
    b_bytes = b.encode('utf-8') if right.kind == 'text' else b
    joined = a_bytes + b_bytes
    if left.kind == 'bytes':
        return Value('bytes', joined)
    try:
        return Value('text', joined.decode('utf-8'))
    except UnicodeDecodeError:
        raise ComputeError("the result of the concatenation is a text string that is not valid UTF-8") from None


# ----------------------------------------------------------------- membership

def contains(items: List[Item], value) -> bool:
    """Whether the data item *value* is one of *items*."""
    for item in items:
        if isinstance(item, Value):
            if _same(item, value):
                return True
        elif _in_interval(item, value):
            return True
    return False


def _same(item: Value, value) -> bool:
    """Whether *value* is exactly the literal *item* (same type, same sign of zero)."""
    if item.kind == 'int':
        return isinstance(value, int) and not isinstance(value, bool) and value == item.value
    if item.kind == 'float':
        return (isinstance(value, float) and value == item.value
                and math.copysign(1.0, value) == math.copysign(1.0, item.value))
    if item.kind == 'text':
        return isinstance(value, str) and value == item.value
    return isinstance(value, bytes) and value == item.value


def _in_interval(item: Interval, value) -> bool:
    """Whether the number *value* is inside *item*."""
    if item.kind == 'int':
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    elif not isinstance(value, float):
        return False
    if not item.low <= value:
        return False
    return value <= item.high if item.inclusive else value < item.high


def single(items: List[Item]):
    """The one value of *items* (a ``Value``), or ``None`` when there are several or it is a range."""
    return items[0] if len(items) == 1 and isinstance(items[0], Value) else None


def array_entries(rules: Dict[str, object], node, _depth: int = 0):
    """``[(type, occurrence), ...]`` for the entries of an array written out, else ``None``.

    Follows parentheses and the names of rules that are one array (for the controller of
    ``.printf`` and ``.join``). Raises :class:`ComputeError` for an array with a group
    entry or a group choice, whose entries cannot be listed.
    """
    if _depth > _MAX_DEPTH:
        return None
    if isinstance(node, Type):
        return array_entries(rules, node.alternatives[0], _depth + 1) if len(node.alternatives) == 1 else None
    if isinstance(node, Paren):
        return array_entries(rules, node.type, _depth + 1)
    if isinstance(node, Name):
        rule = rules.get(node.name)
        if rule is None or node.args or getattr(rule, 'kind', None) != 'type' or rule.params:
            return None
        return array_entries(rules, rule.type(), _depth + 1)
    if isinstance(node, Array):
        if len(node.group.choices) != 1:
            raise ComputeError("the array has group choices, so its elements cannot be listed")
        entries: list = []
        _collect(rules, node.group.choices[0].entries, entries, _depth)
        return entries
    return None


def feature(rules: Dict[str, object], node) -> Tuple[str, Optional[Type]]:
    """``(name, detail)`` for the controller of ``.feature`` (RFC 9165 section 4).

    The controller is the feature name, a text string (which may be computed, ``"a" .cat
    "b"``), or an array whose first element is that name and whose second, if any, is a
    more limited detail about the target. *detail* is the type node of the second element,
    else ``None``.

    Raises:
        ComputeError: The controller is neither, or the name is not one text string.
    """
    entries = array_entries(rules, node)
    if entries is None:
        return _feature_name(rules, node), None
    if not 1 <= len(entries) <= 2:
        raise ComputeError("an array controller is the feature name and, optionally, one detail")
    for _entry, occurrence in entries:
        if occurrence is not None:
            raise ComputeError(f"the elements of an array controller are written out, each once: "
                               f"'{occurrence.text}' cannot be followed")
    return _feature_name(rules, entries[0][0]), (entries[1][0] if len(entries) == 2 else None)


def _feature_name(rules, node) -> str:
    one = single(evaluate(rules, node))
    if one is None or one.kind != 'text':
        raise ComputeError("the feature name must be one text string")
    return one.value


def _collect(rules, group_entries, entries: list, depth: int) -> None:
    """Append the entries of a group to *entries*, flattening plain inline groups."""
    for entry in group_entries:
        if isinstance(entry, GroupRef):
            # a bare name in an array is a type unless the schema defines a group of that name
            rule = rules.get(entry.name)
            if rule is None or getattr(rule, 'kind', None) != 'type':
                raise ComputeError(f"'{entry.name}' is not a type, so the elements of the array "
                                   f"cannot be listed")
            entries.append((Type((Name(entry.name, entry.args),)), entry.occurrence))
        elif isinstance(entry, Member):
            entries.append((entry.value, entry.occurrence))
        elif isinstance(entry, InlineGroup) and entry.occurrence is None and depth < _MAX_DEPTH:
            # ( a, b ) with no occurrence is just a, b
            if len(entry.group.choices) != 1:
                raise ComputeError("the array has group choices, so its elements cannot be listed")
            _collect(rules, entry.group.choices[0].entries, entries, depth + 1)
        else:
            raise ComputeError("every element of the array must be written out (a repeated group "
                               "cannot be followed)")
