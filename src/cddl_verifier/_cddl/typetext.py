"""Type text that carries the node it was made from (#175, Part 1 of #115).

The ``CDDLParser`` tables still hold type expressions as text (``'tstr .size
(1..64)'``, ``'#6.501(m)'``), because the EDN generator and the public
``extract_*`` helpers read text. :class:`TypeText` is such a text that also has
the node it was written from, after inline maps, tags and generic instances were
lifted to synthetic names. The text *is* ``format_node(node)``, so the two
cannot differ, and everything that reads the text keeps working. Consumers that
have moved to nodes read :attr:`TypeText.node` through :func:`as_type`.

:class:`TypeText` goes away when the last text consumer has moved (#177).
"""

from dataclasses import fields, replace
from functools import lru_cache
from typing import Optional

from .ast import Name, Type
from .errors import CDDLSyntaxError
from .parser import parse_type_expr
from .printer import format_node


class TypeText(str):
    """The canonical text of a ``Type`` node, and the node."""

    node: Type

    def __new__(cls, node: Type):
        text = super().__new__(cls, format_node(node))
        text.node = node
        return text

    def __reduce__(self):
        return TypeText, (self.node,)

    def __deepcopy__(self, memo):
        return self  # immutable, like the node it holds

    def __copy__(self):
        return self


@lru_cache(maxsize=4096)
def _parse(text: str) -> Optional[Type]:
    try:
        return parse_type_expr(text)
    except (CDDLSyntaxError, RecursionError):
        return None


def as_type(expr) -> Type:
    """The ``Type`` node for *expr*: a node, a :class:`TypeText`, or text.

    Text that is not one type expression (a name the caller made up, a group
    fragment) becomes a single ``Name`` with that text, so it is opaque to
    every question asked about it, as it was when the questions were asked of
    the text.
    """
    if expr.__class__ is Type:
        return expr
    if isinstance(expr, TypeText):
        return expr.node
    if isinstance(expr, str):
        text = expr.strip()
        node = _parse(text) if len(text) <= 1024 else _parse.__wrapped__(text)
        return node if node is not None else Type((Name(text),))
    return Type((expr,))


def bare_name(expr: Type) -> Optional[str]:
    """The rule name *expr* is, if it is exactly one name (no arguments, no controls)."""
    alts = expr.alternatives
    if len(alts) == 1 and isinstance(alts[0], Name) and not alts[0].args:
        return alts[0].name
    return None


def rename(node, old: str, new: str):
    """*node* with every reference to the name *old* changed to *new*."""
    if isinstance(node, Name) and node.name == old and not node.args:
        return replace(node, name=new)
    if not hasattr(node, '__dataclass_fields__'):
        return node
    changes = {}
    for f in fields(node):
        if f.name in ('span', 'source', 'comment'):
            continue
        value = getattr(node, f.name)
        if isinstance(value, tuple):
            updated = tuple(rename(item, old, new) for item in value)
            if updated != value:
                changes[f.name] = updated
        else:
            updated = rename(value, old, new)
            if updated is not value and updated != value:
                changes[f.name] = updated
    return replace(node, **changes) if changes else node
