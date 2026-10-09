"""Internal CDDL syntax tree and parser (#69).

See ``docs/CDDL_AST_DESIGN.md``. This package is internal: its names can
change in any release.

    from cddl_verifier._cddl import parse_cddl, format_node
    schema = parse_cddl("person = { name: tstr, ? age: uint }")
"""

from .ast import Source, Span, entry_type, registered_label, walk
from .errors import CDDLSyntaxError
from .parser import MAX_DEPTH, parse_cddl
from .prelude import PRELUDE, parse_prelude
from .printer import format_node

__all__ = [
    "CDDLSyntaxError",
    "MAX_DEPTH",
    "PRELUDE",
    "Source",
    "Span",
    "entry_type",
    "format_node",
    "parse_cddl",
    "parse_prelude",
    "registered_label",
    "walk",
]
