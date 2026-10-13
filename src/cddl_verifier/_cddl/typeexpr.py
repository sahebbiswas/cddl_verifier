"""Helpers for type expressions held as ``Type`` nodes (#175, #177).

The ``CDDLParser`` tables hold type expressions as nodes. Text enters the
program in two places, a rule name or expression the caller gives
(``validate(data, "root")``, ``get_type("#6.7(m)")``) and the text of a test,
and :func:`as_type` parses it there, once. Everything after that works on the
node and writes it with ``format_node`` only for a message.
"""

from dataclasses import fields, replace
from functools import lru_cache
from typing import Optional

from .ast import Name, Type
from .errors import CDDLSyntaxError
from .parser import parse_type_expr


@lru_cache(maxsize=4096)
def _parse(text: str) -> Optional[Type]:
    try:
        return parse_type_expr(text)
    except (CDDLSyntaxError, RecursionError):
        return None


def as_type(expr) -> Type:
    """The ``Type`` node for *expr*: a node, or text.

    Text that is not one type expression (a name the caller made up, a group
    fragment) becomes a single ``Name`` with that text, so it is opaque to
    every question asked about it, as it was when the questions were asked of
    the text.
    """
    if expr.__class__ is Type:
        return expr
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
