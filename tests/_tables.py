"""Compare the ``CDDLParser`` tables as text (#177).

The tables hold every type expression as a ``Type`` node. A test that states
what a table holds writes the types as canonical text, which is how a schema
reads, and converts the table with :func:`as_text` before comparing.
"""

from cddl_verifier._cddl.ast import Type
from cddl_verifier._cddl.printer import format_node


def as_text(value):
    """*value* with every ``Type`` node in it replaced by its canonical text."""
    if isinstance(value, Type):
        return format_node(value)
    if isinstance(value, dict):
        return {key: as_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [as_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(as_text(item) for item in value)
    return value
