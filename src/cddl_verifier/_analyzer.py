#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator

Analyzes CBOR data against CDDL schemas and generates annotated EDN output.
Internal implementation module of the ``cddl_verifier`` package. Use the
public ``cddl_verifier`` API (``validate``, ``Validator``) instead.
"""

import argparse
import contextlib
import copy
from dataclasses import replace
import functools
import logging
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ._cddl import parse_cddl, parse_prelude, query, tagcontent, xsd_regex
from ._cddl.typeexpr import as_type, bare_name, rename
from ._cddl.ast import (BytesLit, Control, FloatLit, GroupRef, InlineGroup, IntLit, Major, Member,
                        Name, Paren, Range, Tag, TextLit, Type, Unwrap, walk)
from ._cddl.printer import format_node
from ._cddl.legacy import LegacyTables
from ._cddl.items import float_width_fits, major_error, tag_parts
from ._cddl.modules import ModuleLoader, link, unloaded_warnings
from ._cddl.resolve import resolve
from ._version import __version__

from ._cbor import CBOR, CBORDecodeError, CBORKey
from ._cbor_values import UNDEFINED, CBORSimple, CBORTag, tag_tuple_error
from ._diagnostics import (MAX_ALTERNATIVES, MAX_HEX_BYTES, Diagnostic, _Finding, _Index, classify_message, decode_diagnostic,
                           diagnostic_of, expected_and_found, render_json, render_text,
                           schema_diagnostic)


_FLOAT_WIDTHS = {'float16': 25, 'float32': 26, 'float64': 27}


def _tuple_hint(value: Any) -> str:
    """What to add to "the data is not tagged" when *value* is a tuple shaped like the old tag."""
    return (" (a (tag, value) tuple is not a tag: use CBORTag)"
            if tag_tuple_error(value) is not None else "")


# Compatibility wrapper for existing code that uses SimpleCBORDecoder
class SimpleCBORDecoder:
    """Compatibility wrapper around unified CBOR class."""
    def __init__(self, data: bytes):
        self.data = data

    def decode(self, breadcrumb: str = "") -> Any:
        """Decode CBOR data using unified CBOR class."""
        return CBOR.loads(self.data, keep_encoding=True)

# ANSI color codes for terminal output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Logging level colors
    DEBUG = '\033[36m'      # Cyan
    INFO = '\033[32m'       # Green
    WARNING = '\033[33m'    # Yellow
    ERROR = '\033[31m'      # Red
    
    # Semantic colors
    CBOR = '\033[35m'       # Magenta for CBOR hex
    CDDL = '\033[34m'       # Blue for CDDL types
    MATCH = '\033[32m'      # Green for matches
    MISMATCH = '\033[31m'   # Red for mismatches

class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support."""
    
    FORMATS = {
        logging.DEBUG: f'{Colors.DEBUG}[DEBUG]{Colors.RESET} %(message)s',
        logging.INFO: f'{Colors.INFO}[INFO]{Colors.RESET} %(message)s',
        logging.WARNING: f'{Colors.WARNING}[WARNING]{Colors.RESET} %(message)s',
        logging.ERROR: f'{Colors.ERROR}[ERROR]{Colors.RESET} %(message)s',
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, '[%(levelname)s] %(message)s')
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# Configure logging. As a library the package emits nothing by default; the
# CLI (main) attaches a coloured stderr handler.
logger = logging.getLogger('cddl_verifier')
logger.addHandler(logging.NullHandler())


class _StderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr``."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def _configure_cli_logging(verbose: bool) -> None:
    """Attach the CLI's coloured stderr handler (once) and set the level."""
    if not any(isinstance(h, _StderrHandler) for h in logger.handlers):
        cli_handler = _StderrHandler()
        cli_handler.setFormatter(ColoredFormatter())
        logger.addHandler(cli_handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def _reset_cli_logging() -> None:
    """Undo :func:`_configure_cli_logging` so library use stays silent."""
    for h in [h for h in logger.handlers if isinstance(h, _StderrHandler)]:
        logger.removeHandler(h)
    logger.setLevel(logging.NOTSET)


# CBOR Major Type Constants (RFC 8949)
MAJOR_TYPE_UINT = 0      # Unsigned integer
MAJOR_TYPE_NINT = 1      # Negative integer
MAJOR_TYPE_BSTR = 2      # Byte string
MAJOR_TYPE_TSTR = 3      # Text string
MAJOR_TYPE_ARRAY = 4     # Array
MAJOR_TYPE_MAP = 5       # Map
MAJOR_TYPE_TAG = 6       # Tagged item
MAJOR_TYPE_SIMPLE = 7    # Simple values (bool, null, float)

# Simple Value Constants
SIMPLE_FALSE = 20
SIMPLE_TRUE = 21
SIMPLE_NULL = 22


@functools.lru_cache(maxsize=1)
def _prelude_tables() -> LegacyTables:
    """The tables built from the RFC 8610 prelude (shared: copy before changing)."""
    prelude = parse_prelude()
    return LegacyTables(prelude, resolve(prelude))


class CDDLParser:
    """Parse a CDDL schema and expose its type structure for validation and EDN generation.

    The parser handles a practical subset of RFC 8610 (CDDL) that covers the
    constructs used in CoRIM, CoSWID, and similar IETF attestation schemas.

    Supported constructs
    --------------------
    * Named map types:     ``person = { &(name:0)=>tstr, &(age:1)=>uint }``
    * Named array types:   ``tags = [ + tstr ]``  (with ``+``, ``*`` occurrence)
    * Type aliases:        ``name = tstr``
    * CBOR tag notation:   ``wrapped = #6.501(inner-map)``
    * ``.cbor`` control:   ``payload = bytes .cbor inner-type``
    * ``.size`` constraints: ``id = bstr .size 16``  /  ``label = tstr .size (1..64)``
    * Type choices:        ``$kind /= option-a``
    * Socket extensions:   ``$$ext //= extra-field``
    * IANA registered params: ``&( keyname : keyindex ) => type``
    * Optional fields:     ``? &( field : 0 ) => type``
    * Multi-line fields and continuation lines

    Limitations
    -----------
    * The whole RFC 8610 grammar (as updated by RFC 9682) is parsed, and
      text that is not valid CDDL raises ``CDDLSyntaxError``. Validation
      covers a practical subset of what the grammar can express.
    * ``.regexp`` patterns are XSD regular expressions, as RFC 8610 says, and
      match the whole text (``xsd_regex``, #182); a construct XSD lacks is a
      schema error. ``.bits`` allows only the bit positions of its argument.
    * Ranges (``0..10``, ``0.0...1.0``) and the comparison controls
      (``.ge``, ``.gt``, ``.le``, ``.lt``) are enforced on integers and
      floats (#71). ``.eq`` and ``.ne`` compare with one value and ``.and``
      and ``.within`` need both operands to match (#181).

    Attributes
    ----------
    types : dict
        Parsed type definitions keyed by type name.
    type_aliases : dict
        Simple name → target-name alias table, including built-in primitives.
    type_choices : dict
        Maps ``$choice-name`` to the list of its alternatives.
    registered_params : dict
        Maps integer keyindex to human-readable keyname.
    ast : cddl_verifier._cddl.ast.Schema
        The typed syntax tree. The tables above are built from it
        (``_cddl/legacy.py``); the validator reads only the tables until #111.
    """
    
    def __init__(self, cddl_content: str, source_name: Optional[str] = None,
                 loader: Optional[ModuleLoader] = None):
        """Initialise the parser and immediately parse *cddl_content*.

        Args:
            cddl_content: Raw CDDL schema text.  May be empty (``""``), in
                which case only the built-in primitive aliases are available.
            source_name: File name used in syntax error messages.
            loader: Finds the modules the schema imports (``;# import X as P``);
                without one, imported modules are not loaded (#121).

        Raises:
            CDDLSyntaxError: (a ``SchemaError``) when *cddl_content* is not
                valid CDDL.

        After construction the parsed data is available through the instance
        attributes described in the class docstring.
        """
        self.content = cddl_content
        self.source_name = source_name
        self.loader = loader
        self.warnings: List[str] = []  # imports whose module was not found (#121)
        self.types: Dict[str, Dict] = {}
        self.groups: Dict[str, List] = {}  # Store group definitions
        self.type_choices: Dict[str, List[Type]] = {}  # type choice alternatives, as nodes
        self.socket_extensions: Dict[str, List] = {}  # Store socket extension points
        self.registered_params: Dict[int, str] = {}  # Maps keyindex to keyname
        self.type_aliases: Dict[str, Type] = {}  # what each alias rule stands for, as a node
        self.first_definition: Optional[str] = None  # First name defined in source order
        self.synthetic_types: set = set()  # names given to inline maps (r@a)
        self.ast = None
        self._choice_checker = None  # a CBORAnalyzer, made on first use (#74)
        self.resolved = None  # ResolvedSchema (#70)
        self.parse()

        # Add built-in CDDL primitive types
        self._add_builtin_types()
        
        # Note: CBOR tag definitions like tagged-unsigned-corim-map = #6.501(...)
        # are parsed automatically as type aliases
    
    def _add_builtin_types(self):
        """Add CDDL built-in primitive types."""
        # Map CDDL standard names to our internal type names
        builtin_primitives = {
            'text': 'tstr',
            'bytes': 'bstr',
            'int': 'int',
            'uint': 'uint',
            'nint': 'nint',  # major type 1 only (#99)
            'bool': 'bool',
            # Literal values (prelude 'true = #7.21'), not 'bool' (#73)
            'true': 'true',
            'false': 'false',
            'nil': 'nil',
            'null': 'nil',
            'undefined': '#7.23',
            'float': 'float',
            # float widths follow the encoding (#171), see _check_primitive_type
            'float16': 'float16',
            'float32': 'float32',
            'float64': 'float64',
            'any': 'any',
            # Bignums decode to an int outside the 64-bit range, or to
            # a CBORTag of bytes inside it; 'biguint' / 'bignint' check both (#99).
            'biguint': 'biguint',
            'bignint': 'bignint',
        }
        
        def defined(name):
            return (name in self.types or name in self.type_choices
                    or name in self.type_aliases)

        for builtin_name, internal_type in builtin_primitives.items():
            if not defined(builtin_name):  # the schema's own rule wins
                self.type_aliases[builtin_name] = as_type(internal_type)
                logger.debug(f"Added built-in type: {builtin_name} -> {internal_type}")

        # The other prelude rules (RFC 8610 Appendix D) as the tables build
        # them from the prelude text (#130): 'uri' is '#6.32(tstr)', 'number'
        # is 'int / float', 'decfrac' is a tag around an array type.
        prelude = _prelude_tables()
        for name, alias in prelude.type_aliases.items():
            # 'tstr = #3', 'float16 = #7.25': primitives the validator knows by name
            bare_major_type = query.item_ref(alias) is not None
            if name not in builtin_primitives and not bare_major_type and not defined(name):
                self.type_aliases[name] = alias
        # The array inside 'decfrac' / 'bigfloat' gets a table entry
        # ('decfrac@tag'), added only when the schema uses the rule.
        # A bare name in an array ('[* decfrac]') parses as a GroupRef.
        used = {node.name for node in walk(self.ast) if isinstance(node, (Name, GroupRef))}
        for name, type_def in prelude.types.items():
            owner = name.split('@', 1)[0]
            prelude_alias = prelude.type_aliases.get(owner)
            if owner not in used or self.type_aliases.get(owner) != prelude_alias:
                continue
            # A schema may have its own rule with that name ('@' is allowed
            # in CDDL names): pick another one rather than replace it.
            table_name = name
            while defined(table_name):
                table_name += '@prelude'
            if table_name != name:
                self.type_aliases[owner] = rename(prelude_alias, name, table_name)
            self.types[table_name] = copy.deepcopy(type_def)
            self.synthetic_types.add(table_name)
    
    def parse(self):
        """Parse the schema into :attr:`ast` and build the lookup tables from it.

        Raises:
            CDDLSyntaxError: (a ``SchemaError``) when the text is not valid
                CDDL, with the line and column of the problem.
            CDDLSemanticError: (a ``SchemaError``) when it parses but does
                not make sense, such as an undefined name (#70).
        """
        linked = link(parse_cddl(self.content, source_name=self.source_name), self.loader)
        self.ast = linked.schema
        self._origins = linked.origins
        self.resolved = resolve(self.ast, linked.imports)
        self.warnings = unloaded_warnings(linked, self.resolved.external)
        tables = LegacyTables(self.ast, self.resolved)
        self.types = tables.types
        self.groups = tables.groups
        self.type_choices = tables.type_choices
        self.socket_extensions = tables.socket_extensions
        self.registered_params = tables.registered_params
        self.type_aliases = tables.type_aliases
        self.first_definition = tables.first_definition
        self.synthetic_types = tables.synthetic_types
        self._tables = tables

    def group_sequence(self, key: tuple) -> Optional[list]:
        """Entry sequence of the group *key* (``(name, args)``) an array entry refers to (#138)."""
        tables = getattr(self, '_tables', None)
        return None if tables is None else tables.group_sequence(key)

    def rule_location(self, name: str) -> Optional[str]:
        """``file:line:column`` where the schema defines rule *name*, if it does.

        The file is the schema's name (``<schema>`` for text given directly).
        Rules from the prelude, and generated names, have no location.
        """
        ast = self.ast
        if ast is None or ast.source is None:
            return None
        for rule in ast.rules:
            if rule.name == name:
                source = self._origins.get(name, ast.source)
                line, column = source.line_col(rule.span.start)
                return f"{source.name or '<schema>'}:{line}:{column}"
        return None

    def alias_node(self, name: str) -> Optional[Type]:
        """The type the alias *name* stands for, as a node, or ``None`` if it is no alias (#175)."""
        return self.type_aliases.get(name)

    def resolve_alias(self, expr, max_depth: int = 10) -> Type:
        """Follow the alias chain of *expr* and return the terminal type as a node.

        *expr* is a ``Type`` or a rule name. Only an expression that is
        exactly one name is followed, at most *max_depth* hops, so a circular
        definition ends; a name that is its own alias (a built-in primitive)
        stops the walk. A name that is no alias is its own terminal type.

        Example::

            cddl = CDDLParser("a = b\nb = tstr")
            cddl.resolve_alias("a")  # → the node for tstr
        """
        node = as_type(expr)
        name = bare_name(node)
        depth = 0
        while name is not None and name in self.type_aliases and depth < max_depth:
            following = self.type_aliases[name]
            if following == node:
                break
            node, name = following, bare_name(following)
            depth += 1
        return node

    def _size_bound(self, node, seen=None) -> Optional[Tuple[Optional[int], Optional[int]]]:
        """``(min, max)`` byte counts for a ``.size`` argument node.

        The argument is a non-negative integer, a range (``1..64``,
        ``1...64``), either in parentheses, or a name for one of those.
        Returns ``None`` when it is none of these.
        """
        seen = set() if seen is None else seen
        if isinstance(node, Paren) and len(node.type.alternatives) == 1:
            return self._size_bound(node.type.alternatives[0], seen)
        if isinstance(node, IntLit):
            return (node.value, node.value) if node.value >= 0 else None
        if isinstance(node, Range):
            lo, hi = self._size_int(node.low, seen), self._size_int(node.high, seen)
            if lo is None or hi is None:
                return None
            return lo, hi if node.inclusive else hi - 1
        if isinstance(node, Name) and node.name not in seen:
            seen.add(node.name)
            alias = self.alias_node(node.name)
            if alias is not None and len(alias.alternatives) == 1:
                return self._size_bound(alias.alternatives[0], seen)
        return None

    def _size_int(self, node, seen) -> Optional[int]:
        bound = self._size_bound(node, seen)
        if bound is None or bound[0] != bound[1]:
            return None
        return bound[0]

    def extract_size_constraint(self, expr) -> Optional[dict]:
        """Extract the ``.size`` control (RFC 8610 §3.8.1) from a type node.

        The argument may be a non-negative integer (decimal, ``0x`` or ``0b``),
        a range ``(M..N)`` or ``(M...N)`` (upper bound exclusive), or a named
        constant defined elsewhere in the schema (``max-len = 64``).

        Returns ``None`` when there is no ``.size`` control, otherwise a dict
        with ``'exact'``, ``'min'`` and ``'max'`` (each an int or ``None``).
        An unusable argument yields ``{'error': message}`` instead, so that
        validation reports it rather than silently skipping the check.

        Examples:
        - 'bytes .size 16' -> {'exact': 16, 'min': None, 'max': None}
        - 'text .size (8..64)' -> {'exact': None, 'min': 8, 'max': 64}
        - 'text .size (8...64)' -> {'exact': None, 'min': 8, 'max': 63}
        """
        size = next((c for c in query.controls(expr) if c.op == 'size'), None)
        if size is None:
            return None
        bound = self._size_bound(size.arg)
        if bound is None:
            message = (f"Invalid .size argument '{size.arg_text}': expected a non-negative "
                       f"integer, a range (M..N) / (M...N), or a constant naming one")
            dotted = [n for n in query.names(size.arg)
                      if '..' in n and n not in self.type_aliases]
            if dotted:
                lo, _, hi = dotted[0].partition('..')
                message += (f" ('{dotted[0]}' is one name, since names may contain dots; "
                            f"write '{lo} .. {hi.lstrip('.')}' for a range)")
            return {'error': message}
        lo, hi = bound
        if lo > hi:
            return {'error': f"Invalid .size argument '{size.arg_text}': empty range"}
        if lo == hi and not self._is_range(size.arg):
            return {'exact': lo, 'min': None, 'max': None}
        return {'exact': None, 'min': lo, 'max': hi}

    def _is_range(self, node, seen=None) -> bool:
        """True if a ``.size`` argument is (or names) a range, not one number."""
        seen = set() if seen is None else seen
        if isinstance(node, Paren) and len(node.type.alternatives) == 1:
            return self._is_range(node.type.alternatives[0], seen)
        if isinstance(node, Range):
            return True
        if isinstance(node, Name) and node.name not in seen:
            seen.add(node.name)
            alias = self.alias_node(node.name)
            if alias is not None and len(alias.alternatives) == 1:
                return self._is_range(alias.alternatives[0], seen)
        return False

    def _terminal_name(self, name: str) -> str:
        """The name an alias chain starting at the name *name* ends at (the type's text if it is no name)."""
        resolved = self.resolve_alias(_name_type(name))
        return bare_name(resolved) or format_node(resolved)

    def size_violation(self, value: Any, base_type: str, constraint: Optional[dict]) -> Optional[str]:
        """Check *value* against a ``.size`` constraint (RFC 8610 §3.8.1).

        - ``tstr``: length of the UTF-8 encoding in bytes.
        - ``bstr``: length in bytes.
        - ``uint``: the value must fit in an allowed number of bytes, i.e.
          ``value < 256 ** n`` for some allowed size ``n``. ``uint .size 3``
          is ``0..16777215``; for a range only the upper bound restricts.

        Returns an error message, or ``None`` when the value conforms (or
        there is no constraint). The caller has already checked the type.
        """
        if not constraint:
            return None
        if 'error' in constraint:
            return constraint['error']
        resolved = self.resolve_alias(_name_type(base_type))  # text -> tstr, bytes -> bstr
        alternatives = query.alternatives(resolved)
        if len(alternatives) == 1:
            base_type = bare_name(resolved) or format_node(resolved)
        else:
            base_type = format_node(resolved)
            # 'm .size 2' with 'm = bstr / tstr': measure as the alternative
            # whose type the value has (the caller has checked that it fits).
            wanted = ('tstr' if isinstance(value, str) else 'bstr' if isinstance(value, bytes)
                      else 'uint' if isinstance(value, int) and not isinstance(value, bool) else None)
            if wanted in {self._terminal_name(query.head(a)) for a in alternatives}:
                base_type = wanted
        exact, lo, hi = constraint.get('exact'), constraint.get('min'), constraint.get('max')
        if base_type == 'uint':
            limit = exact if exact is not None else hi
            if limit is not None and value.bit_length() > 8 * limit:
                return f"{value} does not fit in {limit} byte(s) (.size {limit})"
            return None
        if base_type == 'tstr':
            length, unit = len(value.encode('utf-8')), 'UTF-8 bytes'
        elif base_type == 'bstr':
            length, unit = len(value), 'bytes'
        else:
            return f".size is not defined for '{base_type}' (only tstr, bstr, uint)"
        if exact is not None and length != exact:
            return f"expected exactly {exact} {unit}, got {length}"
        if lo is not None and length < lo:
            return f"expected at least {lo} {unit}, got {length}"
        if hi is not None and length > hi:
            return f"expected at most {hi} {unit}, got {length}"
        return None

    def extract_value_range(self, expr) -> Optional[dict]:
        """Extract numeric value-range predicates from a type node.

        Supports ``.ge``, ``.gt``, ``.le``, ``.lt`` (RFC 8610 §3.8.1) with a
        numeric literal argument. The type takes one control, so one bound is
        found; ``(uint .ge 0) .le 100`` is enforced as a rule of its own.

        Examples::

            'uint .le 150'          -> {'ge': None, 'gt': None, 'le': 150, 'lt': None}

        Returns ``None`` if no range predicates are found.
        """
        result = {'ge': None, 'gt': None, 'le': None, 'lt': None}
        found = False
        for control in query.controls(expr):
            if control.op in result and isinstance(control.arg, (IntLit, FloatLit)):
                result[control.op] = control.arg.value
                found = True
        return result if found else None

    def extract_bits(self, expr) -> Optional[int]:
        """The bit positions allowed by a ``.bits`` control, as a mask; ``None`` without one.

        Example::

            'uint .bits &(a: 0, b: 2)' -> 0b101
        """
        for control in query.controls(expr):
            if control.op == 'bits':
                return self.resolved.bits(control.arg) if self.resolved is not None else None
        return None

    @staticmethod
    def bits_violation(value, mask: Optional[int]) -> Optional[str]:
        """The error for a ``uint`` or ``bstr`` with a bit outside *mask*, or ``None``.

        A ``bstr`` is read as an integer with its first byte least significant, so
        bit 8 is the lowest bit of the second byte (RFC 8610 section 3.8).
        """
        if mask is None:
            return None
        number = int.from_bytes(value, 'little') if isinstance(value, bytes) else value
        stray = number & ~mask
        if not stray:
            return None
        bit = (stray & -stray).bit_length() - 1
        allowed = [str(i) for i in range(mask.bit_length()) if mask >> i & 1]
        shown = ', '.join(allowed[:16]) + (', ...' if len(allowed) > 16 else '') if allowed else 'none'
        return f"bit {bit} is set, but .bits allows only the bits {shown}"

    @staticmethod
    def regexp_violation(pattern: str, value: str) -> Optional[str]:
        """The error for *value* against the XSD *pattern* of ``.regexp``, or ``None``.

        The pattern is read as XSD (RFC 8610 section 3.8), see ``xsd_regex``.
        """
        try:
            if xsd_regex.fullmatch(pattern, value):
                return None
        except xsd_regex.XsdRegexError as exc:
            return f"invalid .regexp pattern /{pattern}/: {exc}"
        return f"{value!r} does not match .regexp /{pattern}/"

    def extract_regexp(self, expr) -> Optional[str]:
        """The pattern of a ``.regexp`` control, with string escapes decoded.

        Example::

            'tstr .regexp "[a-z]+"' -> '[a-z]+'
        """
        for control in query.controls(expr):
            if control.op == 'regexp' and isinstance(control.arg, TextLit):
                return control.arg.value
        return None

    def choice_alternatives(self, expr) -> Optional[List[Type]]:
        """The alternatives *expr* chooses between, or ``None`` if it is not a choice.

        *expr* is the name of a type choice (``$socket``) or an inline choice
        (``m1 / m2``) as a node. The alternatives are ``Type`` nodes.
        """
        if not expr:
            return None
        node = as_type(expr)
        alternatives = self.type_choices.get(bare_name(node))
        if alternatives is not None:
            return alternatives
        parts = query.alternatives(node)
        return parts if len(parts) > 1 else None

    def choice_matches(self, choice, cbor_data: Any) -> List[Type]:
        """The alternatives of type choice *choice* that *cbor_data* matches.

        Each alternative is checked completely (tag numbers, fields, controls),
        so an alternative matches only if the data is valid for it. The result
        is in schema order: empty when nothing matches, one alternative when
        the choice is decided, several when the data fits more than one
        alternative (#74).
        """
        alternatives = self.choice_alternatives(choice)
        if not alternatives:
            return []
        checker = self._choice_checker
        if checker is None:
            checker = self._choice_checker = CBORAnalyzer(self)
        return [alt for alt in alternatives if not checker._check_value(cbor_data, alt)]

    def resolve_type_choice_for_data(self, choice, cbor_data: Any,
                                     validator=None) -> Optional[Type]:
        """The alternative of type choice *choice* that *cbor_data* matches.

        When several alternatives match, the first in schema order is
        returned (see :meth:`choice_matches` for all of them). When none
        does, the result is ``None``: no alternative is assumed.

        Args:
            choice: The type choice: its name, or an inline choice as a node.
            cbor_data: The decoded CBOR item (a tag is ``(number, value)``).
            validator: Unused; kept for callers written before choices were
                resolved by matching alone.
        """
        matches = self.choice_matches(choice, cbor_data)
        if not matches:
            logger.debug(f"  No alternative of {_label(choice)} matches")
            return None
        if len(matches) > 1:
            logger.debug(f"  {len(matches)} alternatives of {_label(choice)} match "
                         f"({', '.join(format_node(m) for m in matches)}); using the first")
        return matches[0]

    def get_type(self, type_name, cbor_data: Any = None) -> Optional[Dict]:
        """Get type definition by name, resolving aliases and type choices if needed.

        Args:
            type_name: Name of the type to look up, or a type expression
                (``#6.501(m)``, ``bstr .cbor m``) as text or as a ``Type`` node
            cbor_data: Optional CBOR data to help resolve type choices

        Returns:
            Type definition dict or None if not found
        """
        if not type_name:
            return None

        node = as_type(type_name)
        name = bare_name(node)
        label = _label(type_name)
        logger.debug(f"Getting type: {label}")

        # First try direct lookup
        if name in self.types:
            logger.debug(f"  Found directly in types")
            return self.types[name]

        # Try resolving alias
        resolved = self.resolve_alias(node)
        resolved_name = bare_name(resolved)
        if resolved != node:
            logger.debug(f"  Resolved alias: {label} -> {format_node(resolved)}")

            # Check if resolved name is in types
            if resolved_name in self.types:
                logger.debug(f"  Found resolved type in types")
                return self.types[resolved_name]

            # Check if it's a type choice
            if resolved_name in self.type_choices:
                logger.debug(f"  Resolved to type choice: {resolved_name}")
                # Try to resolve the type choice
                if cbor_data is not None:
                    selected = self.resolve_type_choice_for_data(resolved_name, cbor_data)
                    if selected:
                        logger.debug(f"  Resolved type choice to: {format_node(selected)}")
                        # Recursively get the selected type
                        return self.get_type(selected, cbor_data)
                # A choice has no single definition: without data to choose by,
                # or when no alternative matches, there is none (#74)
                return None

        # Check if the original name is a type choice
        if name in self.type_choices:
            logger.debug(f"  Type is a type choice")
            if cbor_data is not None:
                selected = self.resolve_type_choice_for_data(name, cbor_data)
                if selected:
                    return self.get_type(selected, cbor_data)
            return None

        # Try extracting from CBOR tag notation (directly, or at the end of an alias chain)
        tag_info = query.tag(node) or query.tag(resolved)
        if tag_info:
            tag_num, inner_type = tag_info
            logger.debug(f"  Extracted from tag notation: inner type = {format_node(inner_type)}")
            return self._with_tag(self.get_type(inner_type, self._untag(cbor_data, tag_num)), tag_num)

        # Try extracting .cbor control operator
        cbor_control = query.cbor_control(node)
        if cbor_control:
            base_type, inner_type = cbor_control
            logger.debug(f"  Extracted .cbor control: {base_type} containing {format_node(inner_type)}")
            # Return a synthetic type definition for bytes
            return {
                'type': 'bstr',
                'fields': {},
                'cbor_inner_type': inner_type  # Type node of the embedded item
            }

        # If the name (or its resolved alias) is a known CDDL primitive there is
        # no structured type definition — that is correct and expected, not an error.
        _PRIMITIVES = {'uint', 'int', 'bool', 'nil', 'null', 'float', 'tstr', 'bstr', 'any'}
        if query.head(resolved) in _PRIMITIVES:
            logger.debug(f"  Resolved to primitive: {format_node(resolved)}")
            return None

        logger.debug(f"  Type not found: {label}")
        return None
    
    @staticmethod
    def _with_tag(type_def: Optional[Dict], tag_num: int) -> Optional[Dict]:
        """Return a copy of *type_def* that records the CBOR tag it must carry.

        ``cbor_tags`` lists the expected tags outermost first, so
        ``#6.1(#6.2(m))`` gives ``(1, 2)``. The stored definition is not
        modified, because untagged references share it.
        """
        if type_def is None:
            return None
        tagged = dict(type_def)
        tagged['cbor_tags'] = (tag_num,) + tuple(type_def.get('cbor_tags', ()))
        return tagged

    @staticmethod
    def _untag(cbor_data: Any, tag_num: int) -> Any:
        """Strip tag *tag_num* from decoded data if present (for choice resolution)."""
        if isinstance(cbor_data, CBORTag) and cbor_data.tag == tag_num:
            return cbor_data.value
        return cbor_data

    def get_field_name(self, type_name: str, key: Any) -> Optional[str]:
        """Get field name for a given key in a type."""
        type_def = self.get_type(type_name)
        if not type_def:
            return None
        
        field_info = type_def['fields'].get(key)
        if field_info:
            return field_info['name']
        return None
    
    def get_type_choices(self, choice_name: str) -> Optional[List[Type]]:
        """Get all type alternatives for a type choice."""
        return self.type_choices.get(choice_name)
    
    def get_group(self, group_name: str) -> Optional[List[str]]:
        """Get group definition by name."""
        return self.groups.get(group_name)
    
    def get_socket_extensions(self, socket_name: str) -> Optional[List[str]]:
        """Get all extensions for a socket."""
        return self.socket_extensions.get(socket_name)


def _label(expr) -> str:
    """*expr*, a rule name or a type node, as text for a message."""
    return expr if isinstance(expr, str) else format_node(expr)


def _text_of(expr) -> Optional[str]:
    """The canonical text of the type node *expr* (``None`` for no type), for a message."""
    return None if expr is None else format_node(expr)


def _is_dollar_type(expr) -> bool:
    """True for a type whose text starts with a ``$name`` (a socket or type choice), controls included."""
    base = query.base(query.alternatives(expr)[0])
    return isinstance(base, Name) and base.name.startswith('$')


def _name_type(name: str) -> Type:
    """The type expression that is just the name *name*."""
    return Type((Name(name),))


#: The type of a member whose type the tables do not record.
_UNKNOWN_TYPE = Type((Name('unknown'),))

#: Marks a breadcrumb component (a rule name) that is not a step in the data.
_NO_STEP = object()


class _CheckError(str):
    """An error found by :meth:`CBORAnalyzer._check_value`, with what is known about it (#153).

    It is a ``str``, so code that only formats or compares the message keeps
    working. The other attributes let a caller report it as a finding of its
    own, at the right item and with its own expected and actual, instead of
    wrapping the text in a message of its own.

    Attributes:
        expected, actual: What the schema asks for and what the data has.
        category: A category from ``_diagnostics.CATEGORIES``; guessed from the
            message when ``None``.
        path: Steps from the value that was checked to the item at fault
            (the key or index of each step); empty for the value itself.
        schema_path, rule: The rules entered below the one that was checked,
            and the innermost one; set for errors found by checking a named type.
        span: Byte span of the item in the input, when it is known
            (``located``); otherwise it is found from the path when reported.
        alternatives: For a value that matches none of a choice's
            alternatives: ``(alternative, error)`` for each one that was tried,
            or, for an error found by a nested run, their finished diagnostics.
    """

    expected: Optional[str]
    actual: Optional[str]
    category: Optional[str]
    path: Tuple[Any, ...]
    schema_path: Tuple[str, ...]
    rule: Optional[str]
    span: Optional[Tuple[int, int]]
    located: bool
    alternatives: Tuple[Any, ...]

    def __new__(cls, message: str, *, expected: Optional[str] = None, actual: Optional[str] = None,
                category: Optional[str] = None, path: Tuple[Any, ...] = (),
                schema_path: Tuple[str, ...] = (), rule: Optional[str] = None,
                span: Optional[Tuple[int, int]] = None, located: bool = False,
                alternatives: Tuple[Any, ...] = ()):
        obj = super().__new__(cls, message)
        obj.expected, obj.actual, obj.category = expected, actual, category
        obj.path, obj.schema_path, obj.rule = tuple(path), tuple(schema_path), rule
        obj.span, obj.located, obj.alternatives = span, located, tuple(alternatives)
        return obj

    def reworded(self, message: str) -> '_CheckError':
        """The same finding under a new message (a caller's wording of it)."""
        return _CheckError(message, expected=self.expected, actual=self.actual,
                           category=self.category, path=self.path, schema_path=self.schema_path,
                           rule=self.rule, span=self.span, located=self.located,
                           alternatives=self.alternatives)


class _EmbeddedFrame(NamedTuple):
    """A byte string whose embedded CBOR is being checked (#154)."""
    depth: int  # steps in the data path up to the byte string
    base: int  # offset in the input of the embedded bytes
    locations: Any  # CBORLocations of the embedded bytes; None if unknown
    span: Optional[Tuple[int, int]]  # the byte string itself, in the input

def _cbor_type_name(value: Any) -> str:
    """Name *value* by its CBOR type (``uint``, ``tstr``, ``map``, ``tag 501``...)."""
    if isinstance(value, CBORKey):
        value = value.value
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if value is UNDEFINED:
        return 'undefined'
    if isinstance(value, CBORSimple):
        return repr(value)
    if isinstance(value, int):
        return 'uint' if value >= 0 else 'nint'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, str):
        return 'tstr'
    if isinstance(value, bytes):
        return 'bstr'
    if isinstance(value, dict):
        return 'map'
    if CBORAnalyzer._is_tagged(value):
        return f'tag {value.tag}'
    if isinstance(value, (list, tuple)):
        return 'array'
    return type(value).__name__


def _mismatch_category(base_type: str, value: Any) -> str:
    """``value`` when *value* has the CBOR type *base_type* asks for but fails a
    control on it (a range, ``.regexp``), else ``type``."""
    if base_type in ('uint', 'int'):
        fits = (isinstance(value, int) and not isinstance(value, bool)
                and -2**64 <= value < 2**64 and not (base_type == 'uint' and value < 0))
        return 'value' if fits else 'type'
    if base_type == 'tstr':
        return 'value' if isinstance(value, str) else 'type'
    return 'type'


def _describe_value(value: Any) -> str:
    """The CBOR type of *value*, plus the value itself for numbers and booleans."""
    name = _cbor_type_name(value)
    if isinstance(value, (bool, int, float)) and not isinstance(value, CBORKey):
        return f"{name} {value!r}"
    return name


class CBORAnalyzer:
    """Validate decoded CBOR data against a parsed CDDL schema.

    Uses a :class:`CDDLParser` instance for all schema lookups.  Call
    :meth:`validate` with decoded Python data and the root CDDL type name;
    inspect :meth:`get_errors` for a list of human-readable error messages
    when validation fails.

    Type checking enforced
    ----------------------
    * ``tstr`` / ``bstr`` — Python ``str`` / ``bytes`` (with optional
      ``.size`` constraints enforced)
    * ``uint`` — non-negative Python ``int`` (``bool`` rejected)
    * ``int``  — any Python ``int`` (``bool`` rejected)
    * ``bool`` — Python ``bool`` only (``int`` rejected)
    * ``null`` / ``nil`` — Python ``None`` only
    * ``float`` — Python ``float`` (``int`` rejected)
    * Required field presence and optional field absence
    * Unknown map keys (not in schema) → validation error
    * Array ``+`` occurrence → at-least-one element required
    * Array element primitive types (for ``[ + type ]`` / ``[ * type ]``)
    """

    def __init__(self, cddl_parser: CDDLParser, unique_choices: bool = False):
        """Initialise the analyzer with a parsed CDDL schema.

        Args:
            cddl_parser: A :class:`CDDLParser` instance holding the schema.
            unique_choices: Report data that matches more than one structured
                alternative (a map, array or tag) of a choice, instead of
                accepting it (#74).
        """
        self.cddl = cddl_parser
        self.unique_choices = unique_choices
        self.validation_errors: List[str] = []
        self.breadcrumb: List[str] = []  # Track CDDL path for logging
        self.cbor_bytes: Optional[bytes] = None  # Raw CBOR bytes for offset tracking
        self.offset_map: Dict[int, int] = {}  # Map data id() to byte offset
        # Structured diagnostics (#75): where in the data each breadcrumb
        # component sits, the rules being checked, and the item offsets
        self._data_path: List[Any] = []
        self._rules: List[str] = []
        self._locations = None  # CBORLocations, decoded from cbor_bytes on first use
        self._locations_tried = False
        # One frame per byte string whose CBOR is being checked, innermost last (#154)
        self._embedded_frames: List[_EmbeddedFrame] = []
    
    def _push_breadcrumb(self, component: str, step: Any = _NO_STEP):
        """Add a component to the CDDL breadcrumb.

        *step* is the map key or array index of the data item the component
        stands for; a rule name has none.
        """
        self.breadcrumb.append(component)
        self._data_path.append(step)
    
    def _pop_breadcrumb(self):
        """Remove the last component from the breadcrumb."""
        if self.breadcrumb:
            self.breadcrumb.pop()
            if self._data_path:
                self._data_path.pop()

    def _current_path(self) -> Tuple[Any, ...]:
        """Map keys and array indexes from the root to the item being checked."""
        return tuple(step for step in self._data_path if step is not _NO_STEP)

    def _item_span(self, path: Tuple[Any, ...], key: Any = _NO_STEP,
                   fallback: bool = True) -> Optional[Tuple[int, int]]:
        """Byte span of the item at *path* (or of its key *key*) in the CBOR input.

        Inside embedded CBOR an item the index has no position for is placed
        at its byte string, unless *fallback* is false: then it has no span.
        """
        if self._embedded_frames:
            return self._embedded_item_span(path, key, fallback)
        if not self.cbor_bytes:
            return None
        if self._locations is None and not self._locations_tried:
            self._locations_tried = True
            try:
                self._locations = CBOR.loads_with_locations(self.cbor_bytes)[1]
            except (CBORDecodeError, TypeError):
                self._locations = None
        if self._locations is None:
            return None
        if key is not _NO_STEP:
            return self._locations.key_span(path, key)
        return self._locations.span(path)

    def _embedded_item_span(self, path: Tuple[Any, ...], key: Any,
                            fallback: bool = True) -> Optional[Tuple[int, int]]:
        """Span, in the input, of an item inside embedded CBOR.

        The embedded bytes are a slice of the input, so the item's position
        inside them plus where they start is its offset in the input. An item
        the index has no position for (or bytes that are not one slice, such
        as a chunked byte string) is reported at the byte string.
        """
        frame = self._embedded_frames[-1]
        if frame.locations is not None:
            below = path[frame.depth:]
            span = (frame.locations.key_span(below, key) if key is not _NO_STEP
                    else frame.locations.span(below))
            if span is not None:
                return frame.base + span[0], frame.base + span[1]
        return frame.span if fallback else None

    @contextlib.contextmanager
    def _inside_embedded_cbor(self, payload: bytes, sequence: bool = False):
        """Report offsets inside the byte string *payload* while checking the CBOR in it.

        *sequence* is for ``.cborseq``: the items are addressed as an array.
        """
        path = self._current_path()
        span = self._item_span(path)  # where to point when the bytes can't be located
        own = self._item_span(path, fallback=False)  # the byte string's own span, if indexed
        base = locations = None
        if own is not None and self.cbor_bytes:
            start = own[1] - len(payload)  # the content is the end of the byte string item
            if start >= own[0] and self.cbor_bytes[start:own[1]] == payload:
                try:
                    locations = (CBOR.loads_sequence_with_locations(payload)[1] if sequence
                                 else CBOR.loads_with_locations(payload)[1])
                    base = start
                except (CBORDecodeError, TypeError):
                    locations = None
        self._embedded_frames.append(
            _EmbeddedFrame(len(path), base or 0, locations, span))
        try:
            yield
        finally:
            self._embedded_frames.pop()

    def _report(self, message: str, category: Optional[str] = None, *,
                expected: Optional[str] = None, actual: Optional[str] = None,
                rule: Optional[str] = None, at_parent: bool = False,
                extra_path: Tuple[Any, ...] = (), key: Any = _NO_STEP,
                finding: Optional[_CheckError] = None) -> None:
        """Record a validation error as a message with its structured diagnostic.

        The message is appended to :attr:`validation_errors` unchanged; the
        diagnostic holds the data path, the schema path and rule, the byte
        offset and bytes of the item, and what was expected and found.

        Args:
            message:   The error text.
            category:  A category from :data:`_diagnostics.CATEGORIES`;
                       guessed from the message when omitted.
            expected:  What the schema asks for; read from an "expected X,
                       got Y" message when omitted.
            actual:    What the data has.
            rule:      The rule being checked; defaults to the innermost one.
            at_parent: Point at the container of the current item (a member
                       that is missing has no item of its own).
            extra_path: Steps below the current item.
            key:       Point at the member with this key of the map at the current
                       item: the path ends at it and the offset is that of its key.
            finding:   The error from :meth:`_check_value` that *message* words:
                       the diagnostic takes its path below the current item,
                       its category, expected and actual from it, unless given.
        """
        path = self._current_path()
        if at_parent and path:
            path = path[:-1]
        path = path + tuple(extra_path)
        diagnostic = self._diagnostic(message, path, category, expected, actual, rule, key, finding)
        self.validation_errors.append(_Finding(message, diagnostic))

    def _diagnostic(self, message: str, path: Tuple[Any, ...], category: Optional[str],
                    expected: Optional[str], actual: Optional[str], rule: Optional[str],
                    key: Any, finding: Optional[_CheckError],
                    schema_path: Optional[Tuple[str, ...]] = None) -> Diagnostic:
        """The diagnostic for *message* at *path*, below the schema path (the current one by default)."""
        schema_path = tuple(self.breadcrumb) if schema_path is None else schema_path
        alternatives = ()
        if finding is not None:
            path = path + finding.path
            schema_path = schema_path + finding.schema_path
            # the alternatives were checked on the same value: they continue from here
            alternatives = tuple(
                item if isinstance(item, Diagnostic)  # already complete (from a nested run)
                else self._diagnostic(f"{item[0]}: {item[1]}", path, None, None, None, None,
                                      _NO_STEP, item[1], schema_path)
                for item in finding.alternatives[:MAX_ALTERNATIVES])
            category = category or finding.category
            rule = rule or finding.rule
            if expected is None and actual is None:
                expected, actual = finding.expected, finding.actual
        if category is None:
            category = classify_message(message)
        if expected is None and actual is None:
            expected, actual = expected_and_found(message)
        if finding is not None and finding.located:
            span = finding.span  # found when it was checked, inside embedded CBOR or not
        else:
            span = self._item_span(path, key)
        if key is not _NO_STEP:
            path = path + (key,)  # the member's own path; the offset is that of its key
        offset = end = hex_text = None
        truncated = False
        if span is not None:
            offset, end = span
            if self.cbor_bytes:
                excerpt = self.cbor_bytes[offset:min(end, offset + MAX_HEX_BYTES)]
                hex_text = excerpt.hex(' ')
                truncated = end - offset > MAX_HEX_BYTES
        rule_name = rule if rule is not None else (self._rules[-1] if self._rules else None)
        return Diagnostic(
            message=message, code="validation", severity="error", offset=offset,
            category=category, path=path, schema_path=schema_path,
            rule=rule_name,
            schema_location=self.cddl.rule_location(rule_name) if rule_name else None,
            expected=expected, actual=actual, end_offset=end, hex=hex_text,
            hex_truncated=truncated, alternatives=alternatives)
    
    def _get_breadcrumb(self) -> str:
        """Get the current CDDL path as a string."""
        if not self.breadcrumb:
            return "/"
        return "/" + "/".join(self.breadcrumb)
    
    def _get_cbor_context(self, value: Any) -> str:
        """Get CBOR byte context for a value if available."""
        if not self.cbor_bytes:
            return ""
        
        # Try to find this value's offset in our map
        value_id = id(value)
        if value_id in self.offset_map:
            offset = self.offset_map[value_id]
            if offset < len(self.cbor_bytes):
                # Show a few bytes around this offset
                start = max(0, offset)
                end = min(len(self.cbor_bytes), offset + 4)
                hex_bytes = self.cbor_bytes[start:end].hex(' ')
                return f" {Colors.CBOR}[@{offset:04x}:{hex_bytes}]{Colors.RESET}"
        
        return ""
    
    def _build_offset_map(self, cbor_bytes: bytes, data: Any, offset: int = 0):
        """Build a map of Python object IDs to CBOR byte offsets.
        
        This is a simplified approach - walks the data structure and estimates offsets.
        """
        try:
            if offset >= len(cbor_bytes):
                return offset
            
            initial_byte = cbor_bytes[offset]
            major_type = initial_byte >> 5
            additional_info = initial_byte & 0x1F
            
            # Store this value's offset
            self.offset_map[id(data)] = offset
            
            current_offset = offset + 1
            
            # For maps and arrays, recursively process children
            if major_type == MAJOR_TYPE_MAP and isinstance(data, dict):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                elif additional_info == 25:
                    if current_offset + 1 < len(cbor_bytes):
                        _length = int.from_bytes(cbor_bytes[current_offset:current_offset+2], 'big')
                    current_offset += 2
                
                # Process each key-value pair
                for key, value in data.items():
                    # Process key
                    current_offset = self._skip_cbor_item(cbor_bytes, current_offset)
                    # Process value
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, value, current_offset)
                
                return current_offset
            
            elif major_type == MAJOR_TYPE_ARRAY and isinstance(data, (list, tuple)):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                
                for item in data:
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, item, current_offset)
                
                return current_offset
            
            else:
                # For primitives, skip to next item
                return self._skip_cbor_item(cbor_bytes, offset)
        
        except Exception as e:
            logger.debug(f"Error building offset map: {e}")
            return offset
    
    def _skip_cbor_item(self, cbor_bytes: bytes, offset: int) -> int:
        """Skip a CBOR item and return the offset of the next item."""
        if offset >= len(cbor_bytes):
            return offset
        
        initial_byte = cbor_bytes[offset]
        major_type = initial_byte >> 5
        additional_info = initial_byte & 0x1F
        
        offset += 1
        
        # Decode length/value based on additional info
        if additional_info < 24:
            value_length = 0
        elif additional_info == 24:
            value_length = 1
        elif additional_info == 25:
            value_length = 2
        elif additional_info == 26:
            value_length = 4
        elif additional_info == 27:
            value_length = 8
        else:
            value_length = 0
        
        offset += value_length
        
        # For strings and byte strings, skip the actual data
        if major_type in [2, 3]:  # byte string or text string
            if additional_info < 24:
                data_length = additional_info
            elif additional_info == 24 and offset - 1 < len(cbor_bytes):
                data_length = cbor_bytes[offset - 1]
            elif additional_info == 25 and offset - 2 < len(cbor_bytes):
                data_length = int.from_bytes(cbor_bytes[offset-2:offset], 'big')
            else:
                data_length = 0
            
            offset += data_length
        
        return offset
    
    _ROOT_PRIMITIVES = frozenset(
        {'uint', 'int', 'nint', 'biguint', 'bignint', 'bool', 'nil', 'null', 'float',
         'tstr', 'bstr', 'any', 'float16', 'float32', 'float64'})
    #: Primitives the map-field and array-element code has no branch for, so
    #: they are checked by :meth:`_check_value` (#99).
    _WHOLE_PRIMITIVES = frozenset({'nint', 'biguint', 'bignint', 'float16', 'float32', 'float64'})

    def _validate_root_expression(self, data: Any, type_name: str) -> Optional[bool]:
        """Check what the structured-type path cannot: root tags and primitives.

        Follows the alias chain of *type_name*. A CBOR tag on the way
        (``root = #6.501(inner)``) must be present on *data* with the same tag
        number. A chain that ends in a plain primitive (``n = uint``) is
        validated here.

        Returns:
            ``False`` (with an error recorded) on a tag mismatch or a primitive
            mismatch, ``True`` when a primitive root matches, and ``None`` when
            validation should continue on the structured-type path.
        """
        node = as_type(type_name)
        value = data
        seen = set()
        while node not in seen:
            seen.add(node)
            name = bare_name(node)
            if name in self.cddl.type_choices or self._is_empty_socket(node):
                # '$s /= uint', '$s /= tstr': checked like a socket field
                errors = self._check_value(value, node)
                for error in errors:
                    self._report(f"Value does not match type '{type_name}': {error}", finding=error)
                return not errors
            if name in self.cddl.types:
                return None
            tag_info = query.tag(node)
            if tag_info:
                expected_tag, node = tag_info
                if not isinstance(value, CBORTag):
                    self._report(
                        f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                        f"but the data is not tagged{_tuple_hint(value)}", 'tag', expected=f"tag {expected_tag}",
                        actual=_describe_value(value), rule=type_name)
                    return False
                if value.tag != expected_tag:
                    self._report(
                        f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                        f"got tag {value.tag}", 'tag', expected=f"tag {expected_tag}",
                        actual=f"tag {value.tag}", rule=type_name)
                    return False
                content_error = tagcontent.content_error(expected_tag, value.value)
                if content_error:
                    self._report(f"Type '{type_name}': {content_error}", 'tag',
                                 expected=f"valid tag {expected_tag} content",
                                 actual=_describe_value(value.value), rule=type_name)
                    return False
                value = value.value
                continue
            # Primitives (with controls), literals and inline choices are
            # checked by the same code as map fields and array elements.
            first = query.head(node)
            if (self._checked_as_whole(node)
                    or bare_name(self.cddl.resolve_alias(_name_type(first))) in self._ROOT_PRIMITIVES
                    or self._literal_errors(value, node) is not None):
                errors = self._check_value(value, node)
                for error in errors:
                    self._report(f"Value does not match type '{type_name}': {error}", finding=error)
                return not errors
            following = self.cddl.alias_node(name) if name is not None else None
            if following is None or following == node:
                return None
            node = following
        return None

    def _validate_root(self, data: Any, type_name: str) -> bool:
        """Internal dispatcher — validate *data* without resetting validation_errors.

        :meth:`validate` resets :attr:`validation_errors` and :attr:`breadcrumb` at
        entry, then delegates to this method.  Type-choice auto-resolution uses
        this helper so that any errors already on the list are not wiped.

        Args:
            data:      Decoded CBOR data.
            type_name: Resolved CDDL type name (past alias/choice lookup).

        Returns:
            ``True`` if no new errors were added.
        """
        type_def = self.cddl.get_type(type_name, data)
        if not type_def:
            self._report(f"Type '{type_name}' not found in CDDL")
            return False
        self._push_breadcrumb(type_name)
        result = self._validate_type(data, type_def, type_name)
        self._pop_breadcrumb()
        return result

    def validate(self, data: Any, type_name: str = None, cbor_bytes: bytes = None) -> bool:
        """Validate decoded CBOR *data* against the named CDDL type.

        Resets :attr:`validation_errors` at the start of each call, so the
        instance may be reused across multiple validation calls.

        Args:
            data:       Decoded CBOR data (``dict``, ``list``, scalar, or
                        a :class:`CBORTag`).
            type_name:  Root CDDL type name.  If ``None`` the call returns
                        ``True`` immediately (no-op).
            cbor_bytes: Optional raw CBOR bytes used only for offset
                        annotations in debug logging; not required for
                        validation.

        Returns:
            ``True`` if the data conforms to the schema, ``False`` otherwise.
            Call :meth:`get_errors` to retrieve the list of error messages.

        Raises:
            Nothing — all errors are recorded internally.

        Example::

            cddl = CDDLParser(schema_text)
            analyzer = CBORAnalyzer(cddl)
            if not analyzer.validate(decoded_data, "my-type"):
                for err in analyzer.get_errors():
                    print(err)
        """
        self.validation_errors = []
        self.breadcrumb = []  # Reset breadcrumb
        self._data_path = []
        self._rules = [type_name] if type_name else []
        self.cbor_bytes = cbor_bytes  # Store for offset tracking
        self._locations = None
        self._locations_tried = False
        self._embedded_frames = []
        
        logger.info("=" * 60)
        logger.info("VALIDATION STARTED")
        logger.info("=" * 60)
        
        # Show CBOR hex if available
        if cbor_bytes and logger.isEnabledFor(logging.DEBUG):
            hex_preview = cbor_bytes[:min(16, len(cbor_bytes))].hex(' ')
            if len(cbor_bytes) > 16:
                hex_preview += f" ... ({len(cbor_bytes)} bytes total)"
            logger.debug(f"{Colors.CBOR}CBOR bytes:{Colors.RESET} {hex_preview}")
            
            # Parse CBOR structure for offset mapping
            self._build_offset_map(cbor_bytes, data)
        
        if type_name:
            logger.info(f"Root type: {Colors.CDDL}'{type_name}'{Colors.RESET}")
            logger.debug(f"CBOR data type: {type(data).__name__}")
            if isinstance(data, dict):
                logger.debug(f"CBOR data keys: {list(data.keys())}")
            
            root_result = self._validate_root_expression(data, type_name)
            if root_result is not None:
                return root_result

            type_def = self.cddl.get_type(type_name, cbor_data=data)
            if not type_def:
                logger.warning(f"Type '{type_name}' not found, attempting resolution...")
                
                # Try to provide helpful error message
                resolved_node = self.cddl.resolve_alias(type_name)
                resolved = format_node(resolved_node)
                if resolved_node != as_type(type_name):
                    logger.info(f"Type '{type_name}' is an alias for '{resolved}'")
                    # It's an alias
                    choices = self.cddl.type_choices.get(bare_name(resolved_node))
                    if choices:
                        logger.info(f"'{resolved}' is a type choice with alternatives: "
                                    f"{[format_node(c) for c in choices]}")
                        logger.info("Attempting to auto-resolve type choice...")
                        
                        # Try to resolve automatically with validation
                        selected = self.cddl.resolve_type_choice_for_data(resolved, data, validator=self)
                        if selected:
                            logger.info(f"{Colors.MATCH}Auto-selected: {selected}{Colors.RESET}")
                            return self._validate_root(data, selected)
                        
                        for error in self._check_value(data, resolved):
                            self._report(f"Value does not match type '{type_name}': {error}", finding=error)
                    else:
                        logger.warning(f"'{resolved}' is not a concrete type definition")
                        self._report(
                            f"Type '{type_name}' resolves to '{resolved}' which is not a concrete type definition"
                        )
                else:
                    logger.error(f"Type '{type_name}' not found in CDDL schema")
                    self._report(f"Type '{type_name}' not found in CDDL")
                return False
            
            logger.info(f"Type definition found: {type_name} ({type_def['type']})")
            logger.debug(f"Type fields: {list(type_def['fields'].keys())}")
            
            # Set initial breadcrumb
            self._push_breadcrumb(type_name)
            result = self._validate_type(data, type_def, type_name)
            self._pop_breadcrumb()
            
            if result:
                logger.info("=" * 60)
                logger.info("VALIDATION SUCCESSFUL")
                logger.info("=" * 60)
            else:
                logger.error("=" * 60)
                logger.error("VALIDATION FAILED")
                logger.error("=" * 60)
                for error in self.validation_errors:
                    logger.error(f"  - {error}")
            
            return result
        
        logger.info("No type specified, skipping validation")
        return True
    
    @staticmethod
    def _is_tagged(data: Any) -> bool:
        """True for a decoded CBOR tag, which is a :class:`CBORTag`."""
        return isinstance(data, CBORTag)

    def _validate_type(self, data: Any, type_def: Dict, type_name: str, cbor_offset: int = None) -> bool:
        """Validate data against a specific type definition (see :meth:`_validate_type_checked`)."""
        self._rules.append(type_name)
        try:
            return self._validate_type_checked(data, type_def, type_name, cbor_offset)
        finally:
            self._rules.pop()

    def _validate_type_checked(self, data: Any, type_def: Dict, type_name: str,
                               cbor_offset: int = None) -> bool:
        """Validate data against a specific type definition.
        
        Args:
            data: The CBOR data to validate
            type_def: The CDDL type definition
            type_name: Name of the type
            cbor_offset: Optional CBOR byte offset for this data
        """
        breadcrumb = self._get_breadcrumb()
        offset_str = f"{Colors.CBOR}[@{cbor_offset:04x}]{Colors.RESET} " if cbor_offset is not None else ""
        
        # Tags: the data must carry exactly the tags the rule declares
        # (``r = #6.501(m)``), outermost first. Any other tag is rejected.
        for expected_tag in type_def.get('cbor_tags', ()):
            if not self._is_tagged(data):
                error_msg = (f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                             f"but the data is not tagged{_tuple_hint(data)}")
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"tag {expected_tag}",
                             actual=_describe_value(data))
                return False
            if data.tag != expected_tag:
                error_msg = (f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                             f"got tag {data.tag}")
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"tag {expected_tag}",
                             actual=f"tag {data.tag}")
                return False
            content_error = tagcontent.content_error(expected_tag, data.value)
            if content_error:
                error_msg = f"Type '{type_name}': {content_error}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"valid tag {expected_tag} content",
                             actual=_describe_value(data.value))
                return False
            data = data.value
            logger.debug(f"{Colors.CBOR}[{breadcrumb}] Unwrapped expected CBOR tag {expected_tag}{Colors.RESET}")
        if self._is_tagged(data):
            error_msg = (f"Type '{type_name}' expects an untagged {type_def.get('type', 'value')}, "
                         f"but the data has CBOR tag {data.tag}")
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg, 'tag', expected=f"untagged {type_def.get('type', 'value')}",
                         actual=f"tag {data.tag}")
            return False
        
        logger.debug(f"{offset_str}{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating type '{type_name}'")
        logger.debug(f"  Expected: {type_def['type']}, Got: {type(data).__name__}")

        # '.and' / '.within' around a structure (``non-empty<M>``): the value
        # must match the other operand too (#120)
        for constraint in type_def.get('constraints', ()):
            for error in self._check_value(data, constraint):
                error_msg = f"Type '{type_name}' does not satisfy its control operand '{format_node(constraint)}': {error}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, finding=error)
        
        # Check if this is a .cbor control type (bytes containing CBOR)
        if type_def.get('cbor_inner_type'):
            inner_node = type_def['cbor_inner_type']
            inner_type = format_node(inner_node)
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Type has .cbor control: contains {inner_type}")
            
            if not isinstance(data, bytes):
                error_msg = f"Expected bytes (for .cbor control) for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg)
                return False
            
            # Decode the nested CBOR
            try:
                logger.debug(f"{Colors.CBOR}[{breadcrumb}]{Colors.RESET} Decoding nested CBOR ({len(data)} bytes)")
                nested_decoder = SimpleCBORDecoder(data)
                nested_data = nested_decoder.decode(f"{breadcrumb}:cbor")
                logger.debug(f"{Colors.MATCH}[{breadcrumb}]{Colors.RESET} Decoded nested CBOR successfully")
                
                # Validate the decoded data against the inner type
                nested_type_def = self.cddl.get_type(inner_node, nested_data)
                if nested_type_def:
                    logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating nested CBOR against: {inner_type}")
                    self._push_breadcrumb("cbor")
                    with self._inside_embedded_cbor(data):
                        self._validate_type(nested_data, nested_type_def, inner_type)
                    self._pop_breadcrumb()
                else:
                    logger.warning(f"{Colors.WARNING}[{breadcrumb}]{Colors.RESET} Could not find type definition for: {inner_type}")
                    
                return len(self.validation_errors) == 0
                
            except Exception as e:
                error_msg = f"Failed to decode nested CBOR in type '{type_name}': {e}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg)
                return False
        
        if type_def['type'] == 'map':
            if not isinstance(data, dict):
                error_msg = f"Expected map for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}] TYPE MISMATCH:{Colors.RESET} {error_msg}")
                self._report(error_msg, 'type', expected='map', actual=_describe_value(data))
                return False

            if type_def.get('unsupported'):
                error_msg = f"Cannot check type '{type_name}': {type_def['unsupported']}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg)
                return False

            alternatives = type_def.get('alternatives')
            if alternatives:
                # Group choices, optional groups: the map must match one of the
                # alternatives (#72). If none does, report the closest one.
                closest = None
                for alternative in alternatives:
                    errors = self._sandboxed_validate(data, alternative, type_name)
                    if not errors:
                        return True
                    if closest is None or len(errors) < closest[0]:
                        closest = (len(errors), alternative)
                return self._validate_type_checked(data, closest[1], type_name, cbor_offset)

            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Checking {len(type_def['fields'])} field definitions")
            
            # Check required fields
            for key, field_info in type_def['fields'].items():
                field_name = field_info['name']
                is_optional = field_info.get('optional', False)
                field_node = field_info.get('type') or _UNKNOWN_TYPE
                field_type = format_node(field_node)
                
                # Push field to breadcrumb
                self._push_breadcrumb(field_name, step=key)
                field_breadcrumb = self._get_breadcrumb()
                
                if key in data:
                    value = data[key]
                    value_repr = self._format_value_for_log(value)
                    cbor_ctx = self._get_cbor_context(value)
                    logger.debug(f"{Colors.MATCH}[{field_breadcrumb}] [OK]{Colors.RESET} Field present: " +
                               f"key={key}, type={field_type}, value={value_repr}{cbor_ctx}")
                    
                    # Basic type checking for primitives
                    type_mismatch = False
                    mismatch_detail = None  # (expected, actual) when a control, not the type, failed
                    
                    # Check for .cbor control operator (bytes containing CBOR data)
                    # An inline choice ('bstr .cbor m / tstr') is checked as a whole below
                    cbor_control = query.cbor_control(field_node)
                    if cbor_control:
                        base_type, inner_node = cbor_control
                        inner_type = format_node(inner_node)
                        logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Field has .cbor control: {base_type} .cbor {inner_type}")
                        
                        # Verify it's bytes
                        if not isinstance(value, bytes):
                            type_mismatch = True
                            logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Expected bytes (for .cbor), got {type(value).__name__}")
                            self._report(f"Field '{field_name}' should be bytes containing CBOR data")
                        else:
                            # Decode the nested CBOR and validate it
                            try:
                                logger.debug(f"{Colors.CBOR}[{field_breadcrumb}]{Colors.RESET} Decoding nested CBOR ({len(value)} bytes)")
                                nested_decoder = SimpleCBORDecoder(value)
                                nested_data = nested_decoder.decode(f"{field_breadcrumb}:cbor")
                                logger.debug(f"{Colors.MATCH}[{field_breadcrumb}]{Colors.RESET} Decoded nested CBOR: {type(nested_data).__name__}")
                                
                                # Validate the decoded data against the inner type
                                nested_type_def = self.cddl.get_type(inner_node, nested_data)
                                if nested_type_def:
                                    logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Validating nested CBOR against: {inner_type}")
                                    with self._inside_embedded_cbor(value):
                                        self._validate_type(nested_data, nested_type_def, inner_type)
                                else:
                                    # A primitive or other expression ('bstr .cbor uint')
                                    for error in self._check_embedded(value, nested_data, inner_node):
                                        self._report(
                                            f"Embedded CBOR in field '{field_name}' of '{type_name}' "
                                            f"does not match '{inner_type}': {error}",
                                            'nested', finding=error)
                            except Exception as e:
                                logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Failed to decode nested CBOR: {e}")
                                self._report(f"Failed to decode nested CBOR in field '{field_name}': {e}")
                    
                    # Tagged primitive (``x: #6.1(uint)``): check the tag, then the value inside
                    written_node = field_node
                    field_node, value, tag_error = self._peel_tagged_primitive(field_node, value)
                    # the type as text, for messages and for rule names
                    if field_node is not written_node:
                        field_type = format_node(field_node)
                    if tag_error:
                        self._report(f"Field '{field_name}' in '{type_name}' {tag_error}")
                        self._pop_breadcrumb()
                        continue

                    # field_type may contain user alias + annotation (e.g. 'my-text .size 16')
                    # Resolve alias first, then extract base type.
                    _resolved = self.cddl.resolve_alias(field_node)

                    # Inline type choice ('c = uint / tstr', 'x: m / n'): any alternative may match
                    # (or a parenthesized type, whose controls apply to each alternative)
                    if self._checked_as_whole(_resolved) or isinstance(query.base(_resolved), Unwrap):  # '~m' (#106)
                        for choice_error in self._check_value(value, _resolved):
                            self._report(
                                f"Field '{field_name}' in '{type_name}' {choice_error}",
                                finding=choice_error)
                        self._pop_breadcrumb()
                        continue
                    _base_type = query.head(_resolved) if _resolved else ''
                    # Normalize CDDL built-in aliases to their canonical base types
                    _alias_map = {
                        'text': 'tstr', 'bytes': 'bstr',
                        'nil': 'null'
                    }
                    _base_type = _alias_map.get(_base_type, _base_type)

                    if _base_type in ('uint', 'int'):
                        # bool is a subclass of int in Python; reject it because CBOR
                        # booleans (major type 7) and integers (major type 0/1) are
                        # distinct wire types.  Also reject negative values for uint.
                        if not isinstance(value, int) or isinstance(value, bool):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected {_base_type}, got {type(value).__name__}")
                        elif _base_type == 'uint' and value < 0:
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected uint (>=0), got {value}")
                        elif not -2**64 <= value < 2**64:
                            # Outside the 64-bit range: a bignum (tag 2/3), not uint/int
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: {value} is a bignum, outside the {_base_type} range")
                        else:
                            # Check .ge / .gt / .le / .lt value-range predicates
                            vrange = self.cddl.extract_value_range(_resolved)
                            if vrange:
                                if vrange['ge'] is not None and value < vrange['ge']:
                                    type_mismatch = True
                                    mismatch_detail = (f"at least {vrange['ge']}", str(value))
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} < .ge {vrange['ge']}")
                                elif vrange['gt'] is not None and value <= vrange['gt']:
                                    type_mismatch = True
                                    mismatch_detail = (f"more than {vrange['gt']}", str(value))
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} not > .gt {vrange['gt']}")
                                elif vrange['le'] is not None and value > vrange['le']:
                                    type_mismatch = True
                                    mismatch_detail = (f"at most {vrange['le']}", str(value))
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} > .le {vrange['le']}")
                                elif vrange['lt'] is not None and value >= vrange['lt']:
                                    type_mismatch = True
                                    mismatch_detail = (f"less than {vrange['lt']}", str(value))
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} not < .lt {vrange['lt']}")
                    elif _base_type == 'bool':
                        if not isinstance(value, bool):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected bool, got {type(value).__name__}")
                    elif _base_type in ('nil', 'null'):
                        if value is not None:
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected null/nil, got {type(value).__name__}")
                    elif _base_type == 'float':
                        # Accept float only; reject plain int — CBOR integers and floats
                        # are different major types on the wire.
                        if not isinstance(value, float):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected float, got {type(value).__name__}")
                    elif bare_name(field_node) == 'tstr' or _base_type == 'tstr':
                        if not isinstance(value, str):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected tstr, got {type(value).__name__}")
                        else:
                            # Check .regexp constraint
                            pattern = self.cddl.extract_regexp(_resolved)
                            if pattern and not type_mismatch:
                                regexp_error = self.cddl.regexp_violation(pattern, value)
                                if regexp_error:
                                    type_mismatch = True
                                    if not regexp_error.startswith('invalid'):
                                        mismatch_detail = (f"text matching .regexp /{pattern}/",
                                                           self._format_value_for_log(value))
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Regexp mismatch for field '{field_name}': {regexp_error}")
                                    if regexp_error.startswith('invalid'):
                                        self._report(f"{regexp_error} (field '{field_name}')")
                    elif bare_name(field_node) == 'bstr' or _base_type == 'bstr':
                        if not isinstance(value, bytes):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected bstr, got {type(value).__name__}")

                    elif not _is_dollar_type(field_node):
                        # It's a structured type - check basic structure
                        nested_type_def = self.cddl.get_type(field_node)
                        # Tagged rules are checked (tag, then structure) by _validate_type below
                        if nested_type_def and not nested_type_def.get('cbor_tags'):
                            if nested_type_def['type'] == 'map' and not isinstance(value, dict):
                                type_mismatch = True
                                logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected map, got {type(value).__name__}")
                            elif nested_type_def['type'] == 'array' and not isinstance(value, (list, tuple)):
                                type_mismatch = True
                                logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected array, got {type(value).__name__}")
                    
                    if type_mismatch:
                        expected_text, actual_text = mismatch_detail or (field_type, _describe_value(value))
                        self._report(f"Type mismatch for field '{field_name}' in '{type_name}'",
                                     _mismatch_category(_base_type, value),
                                     expected=expected_text, actual=actual_text)
                    elif _base_type in self._ROOT_PRIMITIVES and _base_type != 'any':
                        size_error = self.cddl.size_violation(
                            value, _base_type, self.cddl.extract_size_constraint(_resolved))
                        if size_error:
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} .size: {size_error}")
                            self._report(
                                f"Field '{field_name}' in '{type_name}' violates .size: {size_error}")
                    
                    # Recursively validate nested structures
                    if bare_name(field_node) not in ('tstr', 'uint', 'int', 'bstr', 'bool', 'float', 'any'):
                        # Check if field_type is an inline array definition: [ + type ] or [ * type ]
                        # Other inline arrays ('[ a, * b ]') are named types of
                        # their own (#133) and are checked as such.
                        array_match = query.inline_array(field_node) or (
                            ('', None) if query.is_array(field_node) else None)
                        if array_match:
                            # It's an inline array definition
                            # quantifier is '+', '*' or ''; element_type is None
                            # for an array with several entries
                            quantifier, element_type = array_match
                            
                            logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Field is inline array: [{quantifier} {format_node(element_type) if element_type else element_type}]")
                            
                            if not isinstance(value, (list, tuple)):
                                logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Expected array, got {type(value).__name__}")
                                self._report(f"Expected array for field '{field_name}', got {type(value).__name__}")
                            else:
                                # Validate array constraints
                                if quantifier == '+' and len(value) == 0:
                                    logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Array must have at least one element")
                                    self._report(f"Array field '{field_name}' must not be empty")
                                
                                # Validate each array element
                                logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Validating {len(value)} array elements")
                                for i, item in enumerate(value):
                                    self._push_breadcrumb(f"[{i}]", step=_Index(i))
                                    item_breadcrumb = self._get_breadcrumb()
                                    item_repr = self._format_value_for_log(item)
                                    logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")
                                    if element_type is None:
                                        self._pop_breadcrumb()
                                        continue

                                    # Check if element type is a type choice
                                    if _is_dollar_type(element_type) and not self._checked_as_whole(element_type):
                                        element_text = format_node(element_type)
                                        logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element type is a choice: {element_text}")
                                        selected_type = self.cddl.resolve_type_choice_for_data(element_text, item, validator=self)
                                        if selected_type:
                                            logger.debug(f"{Colors.MATCH}[{item_breadcrumb}]{Colors.RESET} Resolved choice to: {selected_type}")
                                            nested_type_def = self.cddl.get_type(selected_type)
                                            if nested_type_def:
                                                self._validate_type(item, nested_type_def, selected_type)
                                        else:
                                            logger.warning(f"{Colors.WARNING}[{item_breadcrumb}]{Colors.RESET} Could not resolve type choice: {element_text}")
                                    else:
                                        # Regular element type: primitive (with .size) or named type
                                        resolved_elem = self.cddl.resolve_alias(element_type)
                                        if self._checked_as_whole(resolved_elem) or self.cddl.extract_regexp(resolved_elem) is not None:
                                            for choice_error in self._check_value(item, resolved_elem):
                                                self._report(
                                                    f"Element [{i}] of field '{field_name}' {choice_error}",
                                                    finding=choice_error)
                                            self._pop_breadcrumb()
                                            continue
                                        if query.cbor_control(resolved_elem):
                                            for cbor_error in self._check_value(item, resolved_elem):
                                                self._report(
                                                    f"Element [{i}] of field '{field_name}' {cbor_error}",
                                                    finding=cbor_error)
                                            self._pop_breadcrumb()
                                            continue
                                        base_elem = query.head(resolved_elem) if resolved_elem else ''
                                        elem_valid = self._check_primitive_type(item, base_elem)
                                        if elem_valid is False:
                                            self._report(
                                                f"Element [{i}] of field '{field_name}' has wrong type: "
                                                f"expected {base_elem}, got {type(item).__name__}")
                                        elif elem_valid is True:
                                            size_error = self.cddl.size_violation(
                                                item, base_elem, self.cddl.extract_size_constraint(resolved_elem))
                                            if size_error:
                                                self._report(
                                                    f"Element [{i}] of field '{field_name}' violates .size: {size_error}")
                                        else:
                                            nested_type_def = self.cddl.get_type(element_type)
                                            if nested_type_def:
                                                self._validate_type(item, nested_type_def, format_node(element_type))
                                    
                                    self._pop_breadcrumb()
                        
                        # Check if field_type is a type choice
                        elif _is_dollar_type(field_node):
                            # It's a type choice - need to resolve it
                            choice_name = field_type
                            logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Field type is a choice: {choice_name}")
                            
                            # Resolve the choice
                            selected_type = self.cddl.resolve_type_choice_for_data(choice_name, value, validator=self)
                            if selected_type:
                                logger.debug(f"{Colors.MATCH}[{field_breadcrumb}]{Colors.RESET} Resolved choice to: {selected_type}")
                                nested_type_def = self.cddl.get_type(selected_type)
                                if nested_type_def:
                                    logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Recursing into nested type: {selected_type}")
                                    self._validate_type(value, nested_type_def, selected_type)
                            else:
                                logger.warning(f"{Colors.WARNING}[{field_breadcrumb}]{Colors.RESET} Could not resolve type choice: {choice_name}")
                        else:
                            # Regular type - try to get nested type definition
                            nested_type_def = self.cddl.get_type(field_node)
                            # 'bstr .cbor m' was checked above; checking again repeats its errors
                            if nested_type_def and not cbor_control:
                                logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Recursing into nested type: {field_type}")
                                self._validate_type(value, nested_type_def, field_type)
                    
                elif not is_optional:
                    error_msg = f"Missing required field '{field_name}' (key {key}) in type '{type_name}'"
                    logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}] [X] MISSING:{Colors.RESET} {error_msg}")
                    self._report(error_msg, 'missing', at_parent=True,
                                 expected=f"member '{field_name}' (key {key})", actual='absent')
                else:
                    logger.debug(f"{Colors.DEBUG}[{field_breadcrumb}] [.]{Colors.RESET} Optional field not present")
                
                self._pop_breadcrumb()
            
            # Report extra fields
            defined_keys = set(type_def['fields'].keys())
            actual_keys = set(data.keys())
            extra_keys = actual_keys - defined_keys
            # Keys allowed by computed-key members ('* label => value', #104)
            computed_keys = type_def.get('computed_keys', ())
            computed_counts = {}
            for key in sorted(extra_keys, key=repr):
                key_value = key.value if isinstance(key, CBORKey) else key
                candidates = []
                for index, computed in enumerate(computed_keys):
                    if self._check_value(key_value, computed['key'], strict=True):
                        continue
                    candidates.append(index)
                    if computed.get('cut'):
                        break  # 'K ^=> V': later members are not tried for this key
                if not candidates:
                    continue
                # The member the entry belongs to: one whose value matches
                # and that has room left, else any whose value matches.
                matching = [i for i in candidates
                            if not self._check_value(data[key], computed_keys[i]['type'])]
                with_room = [i for i in matching
                             if computed_keys[i]['max'] is None
                             or computed_counts.get(i, 0) < computed_keys[i]['max']]
                chosen = (with_room or matching or candidates)[0]
                computed_counts[chosen] = computed_counts.get(chosen, 0) + 1
                extra_keys.discard(key)
                if chosen not in matching:
                    self._push_breadcrumb(repr(key), step=key)  # the value is at the key
                    for error in self._check_value(data[key], computed_keys[chosen]['type']):
                        self._report(
                            f"Value for key {key!r} in type '{type_name}' "
                            f"does not match '{format_node(computed_keys[chosen]['type'])}': {error}",
                            finding=error)
                    self._pop_breadcrumb()
            # How many entries each computed-key member took ('+ K => V', '? K => V',
            # 'K => V' takes exactly one, #120, #151)
            for index, computed in enumerate(computed_keys):
                count = computed_counts.get(index, 0)
                low, high = computed['min'], computed['max']
                if count < low or (high is not None and count > high):
                    if high is None:
                        wanted = f"at least {low}"
                    elif low == high:
                        wanted = f"exactly {low}"
                    elif low == 0:
                        wanted = f"at most {high}"
                    else:
                        wanted = f"{low} to {high}"
                    error_msg = (f"Type '{type_name}' needs {wanted} "
                                 f"{'entry' if (high or low) == 1 and (high == 1 or low == 1) else 'entries'} "
                                 f"with key '{format_node(computed['key'])}', found {count}")
                    logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                    self._report(error_msg)
            # One diagnostic per unknown member, in the order they appear in the map (#155)
            for key in [k for k in data if k in extra_keys]:
                error_msg = f"Unknown field {key!r} not in schema for type '{type_name}'"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'unknown', key=key,
                             expected='only the members the schema defines',
                             actual=f"unknown key {key!r}")
        
        elif type_def['type'] == 'array':
            if not isinstance(data, (list, tuple)):
                error_msg = f"Expected array for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}] TYPE MISMATCH:{Colors.RESET} {error_msg}")
                self._report(error_msg, 'type', expected='array', actual=_describe_value(data))
                return False
            
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Array has {len(data)} elements")

            sequence = type_def.get('sequence')
            if sequence is not None:
                # Optional or repeated entries before others, group names and
                # group choices: assign the elements to the entries (#135)
                error_msg = self._match_array(data, sequence, type_name)
                if error_msg:
                    logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                    self._report(error_msg)
                return len(self.validation_errors) == 0

            # Enforce the number of elements the entries' occurrences allow
            # ('[int, tstr]' takes exactly two). Without known bounds, only
            # a lone '+' entry is checked.
            length = type_def.get('length')
            if length is not None:
                low, high = length
                error_msg = None
                if len(data) < low:
                    error_msg = (f"Array type '{type_name}' is missing element [{len(data)}]: "
                                 f"expected at least {low} element(s), got {len(data)}")
                elif high is not None and len(data) > high:
                    error_msg = (f"Array type '{type_name}' has unexpected element [{high}]: "
                                 f"expected at most {high} element(s), got {len(data)}")
                if error_msg:
                    logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                    self._report(error_msg)
            elif type_def.get('occurrence', '') == '+' and len(data) == 0:
                error_msg = f"Array type '{type_name}' requires at least one element (+ occurrence)"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg)

            # Validate element types
            element_types = type_def.get('element_types', {})
            # Elements past the positional ones take the repeating entry's
            # type ('[ int, * tstr ]': index 1). A one-entry array ('[ + tstr ]')
            # repeats entry 0. Which entry an element matches when entries are
            # optional is not worked out yet (#72).
            repeat = type_def.get('repeat')
            if repeat is None and len(element_types) == 1:
                repeat = next(iter(element_types))
            repeating_type = element_types.get(repeat) if repeat is not None else None

            for i, item in enumerate(data):
                self._push_breadcrumb(f"[{i}]", step=_Index(i))
                item_breadcrumb = self._get_breadcrumb()
                item_repr = self._format_value_for_log(item)
                logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")

                # Check element type: per-index pattern takes precedence over repeating
                elem_type = element_types.get(i) if i in element_types else repeating_type
                if elem_type:
                    elem_node, item, tag_error = self._peel_tagged_primitive(elem_type, item)
                    if tag_error:
                        self._report(f"Array element [{i}] of '{type_name}' {tag_error}")
                        self._pop_breadcrumb()
                        continue
                    # Resolve user alias first
                    resolved_elem = self.cddl.resolve_alias(elem_node)
                    if self._checked_as_whole(resolved_elem) or self.cddl.extract_regexp(resolved_elem) is not None:
                        for choice_error in self._check_value(item, resolved_elem):
                            self._report(
                                f"Array element [{i}] of '{type_name}' {choice_error}",
                                finding=choice_error)
                        self._pop_breadcrumb()
                        continue
                    if query.cbor_control(resolved_elem):
                        # 'bstr .cbor T': decode the bytes and check the item inside
                        for cbor_error in self._check_value(item, resolved_elem):
                            self._report(
                                f"Array element [{i}] of '{type_name}' {cbor_error}",
                                finding=cbor_error)
                        self._pop_breadcrumb()
                        continue
                    # Extract base type and any .size constraint
                    base_elem_type = query.head(resolved_elem)
                    size_constraint = self.cddl.extract_size_constraint(resolved_elem)

                    # Check primitive type first
                    elem_valid = self._check_primitive_type(item, base_elem_type)
                    if elem_valid is False:
                        error_msg = (
                            f"Array element [{i}] of '{type_name}' has wrong type: "
                            f"expected {base_elem_type}, got {type(item).__name__}"
                        )
                        logger.error(f"{Colors.MISMATCH}[{item_breadcrumb}]{Colors.RESET} {error_msg}")
                        self._report(error_msg)
                    # Enforce size constraint if primitive check passed
                    elif elem_valid is True and size_constraint:
                        size_error = self.cddl.size_violation(item, base_elem_type, size_constraint)
                        if size_error:
                            error_msg = f"Array element [{i}] of '{type_name}' violates .size: {size_error}"
                            logger.error(f"{Colors.MISMATCH}[{item_breadcrumb}]{Colors.RESET} {error_msg}")
                            self._report(error_msg)
                    # elem_valid is None → structured type (map, array, choice)
                    elif elem_valid is None:
                        # Look up the rule by name first: a tagged rule resolves
                        # to '#6.n(...)', whose first token is not a type name.
                        elem_name = format_node(elem_node)
                        nested_type_def = self.cddl.get_type(elem_node, item)
                        if not nested_type_def:
                            elem_name = base_elem_type
                            nested_type_def = self.cddl.get_type(base_elem_type, item)
                        if nested_type_def:
                            logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Recursing into nested type: {elem_name}")
                            self._validate_type(item, nested_type_def, elem_name)
                        else:
                            logger.warning(f"{Colors.WARNING}[{item_breadcrumb}]{Colors.RESET} Unknown element type: {format_node(elem_node)}")

                self._pop_breadcrumb()
        
        return len(self.validation_errors) == 0
    
    def _peel_tagged_primitive(self, expr, value: Any) -> Tuple[Type, Any, Optional[str]]:
        """Strip the tags of a tagged primitive type such as ``#6.7(uint)``.

        Tagged structured types are checked by :meth:`_validate_type`; this
        covers tags around primitives, which have no type definition.

        Returns ``(inner_type, inner_value, error)``, the type as a node. When
        *expr* is not a tagged primitive the type is returned as it was (the
        same object) with ``error`` ``None``. On a missing or wrong tag
        ``error`` describes it.
        """
        node = as_type(expr) if expr else expr
        if not node or self.cddl.get_type(node) is not None:
            return node, value, None
        while True:
            tag_info = query.tag(self.cddl.resolve_alias(node))
            if not tag_info:
                return node, value, None
            expected_tag, inner = tag_info
            if not self._is_tagged(value):
                return node, value, f"requires CBOR tag {expected_tag}, but the data is not tagged{_tuple_hint(value)}"
            if value.tag != expected_tag:
                return node, value, f"requires CBOR tag {expected_tag}, got tag {value.tag}"
            content_error = tagcontent.content_error(expected_tag, value.value)
            if content_error:
                return node, value, content_error
            node, value = inner, value.value

    def _bound_literal(self, bound) -> Optional[Any]:
        """The ``IntLit``/``FloatLit`` a range bound stands for, following names."""
        lit = bound if isinstance(bound, (IntLit, FloatLit)) else (
            self.cddl.resolved.literal(bound) if self.cddl.resolved is not None else None)
        return lit if isinstance(lit, (IntLit, FloatLit)) else None

    def _range_bounds(self, node: Range) -> Optional[Tuple[Any, Any]]:
        """The numeric bounds of a range node, following names to their literals."""
        low, high = self._bound_literal(node.low), self._bound_literal(node.high)
        if low is None or high is None:
            return None
        return low.value, high.value

    def _range_errors(self, value: Any, expr, strict: bool = False) -> Optional[List[str]]:
        """Check *value* against a range type (``0..10``, ``0.0...1.0``, ``lo .. hi``, #71).

        An integer range matches CBOR integers (major types 0 and 1, so not a
        bignum, a float or a bool) between its bounds; a float range matches
        floats. ``..`` includes the upper bound and ``...`` excludes it.
        Returns ``None`` when *expr* is not a range.
        """
        node = query.range_of(expr)
        if node is None:
            return None
        bounds = self._range_bounds(node)
        # with one bound known ('$low .. 10'), the kind of number still is
        known = self._bound_literal(node.low) or self._bound_literal(node.high)
        if known is None:
            # bounds that are sockets or generic parameters: nothing to compare with
            return [f"cannot check range '{format_node(expr)}'"] if strict else []
        if isinstance(known, IntLit):
            ok_type = (isinstance(value, int) and not isinstance(value, bool)
                       and -(1 << 64) <= value < (1 << 64))
            kind = 'an integer'
        else:
            ok_type = isinstance(value, float)
            kind = 'a float'
        shown = self._format_value_for_log(value)

        def outside(text: str) -> _CheckError:
            return _CheckError(f"{text} is outside {format_node(expr)}", category='value',
                               expected=f"a value in {format_node(expr)}", actual=text)

        if not ok_type:
            return [f"expected {kind} in {format_node(expr)}, got {shown}"]
        if bounds is None:
            if strict:
                return [f"cannot check range '{format_node(expr)}'"]
            # '$low .. 10' with an unknown '$low': the known bound still applies
            low, high = self._bound_literal(node.low), self._bound_literal(node.high)
            if low is not None and not value >= low.value:  # 'not >=' also rejects NaN
                return [outside(shown)]
            if high is not None and not (value <= high.value if node.inclusive
                                         else value < high.value):
                return [outside(shown)]
            return []
        low, high = bounds
        if not (low <= value and (value <= high if node.inclusive else value < high)):
            return [outside(shown)]
        return []

    def _literal_errors(self, value: Any, expr) -> Optional[List[str]]:
        """Check *value* against a literal type (``1``, ``1.5``, ``"x"``, ``h'01'``, ``true``).

        Returns ``None`` when *expr* is not a literal.
        """
        lit = query.literal(expr)
        if isinstance(lit, IntLit):
            ok = isinstance(value, int) and not isinstance(value, bool) and value == lit.value
        elif isinstance(lit, FloatLit):
            # 0.0 and -0.0 are different CBOR values
            ok = (isinstance(value, float) and value == lit.value
                  and math.copysign(1.0, value) == math.copysign(1.0, lit.value))
        elif isinstance(lit, TextLit):
            ok = isinstance(value, str) and value == lit.value
        elif isinstance(lit, BytesLit):
            ok = isinstance(value, bytes) and value == lit.value
        elif self._is_literal(expr):
            # 'true' / 'false', unless the schema redefines them
            ok = value is (bare_name(as_type(expr)) == 'true')
        else:
            return None
        if not ok:
            return [f"expected {format_node(expr)}, got {self._format_value_for_log(value)}"]
        if lit is None or not query.controls(expr):
            return []
        # '"t" .regexp "[a-c]+"', '1 .ge 0': the controls apply to the literal too
        base = {TextLit: 'tstr', BytesLit: 'bstr', FloatLit: 'float'}.get(
            type(lit), 'uint' if isinstance(lit, IntLit) and lit.value >= 0 else 'int')
        return self._primitive_errors(value, base, expr)

    def _primitive_errors(self, value: Any, base: str, expr) -> List[str]:
        """Check *value* against primitive *base* and the controls in *expr*."""
        if base == 'any':
            return []
        if not self._check_primitive_type(value, base):
            got = value if isinstance(value, (int, float)) and not isinstance(value, bool) \
                else type(value).__name__
            if value is UNDEFINED or isinstance(value, CBORSimple):
                got = repr(value)
            elif isinstance(value, float) and base in _FLOAT_WIDTHS:
                written = {25: 'float16', 26: 'float32', 27: 'float64'}.get(getattr(value, 'info', None))
                got = f"{written} {value}" if written else value
            return [f"expected {base}, got {got}"]
        errors = []
        size_error = self.cddl.size_violation(value, base, self.cddl.extract_size_constraint(expr))
        if size_error:
            size_error = size_error if size_error.startswith('.size') else f".size {size_error}"
            errors.append(_CheckError(size_error, category='value'))
        if base in ('uint', 'int', 'nint', 'float', 'float16', 'float32', 'float64'):
            vrange = self.cddl.extract_value_range(expr)
            if vrange:
                for op, test in (('ge', lambda v, b: v >= b), ('gt', lambda v, b: v > b),
                                 ('le', lambda v, b: v <= b), ('lt', lambda v, b: v < b)):
                    if vrange[op] is not None and not test(value, vrange[op]):
                        wanted = {'ge': 'at least', 'gt': 'more than',
                                  'le': 'at most', 'lt': 'less than'}[op]
                        errors.append(_CheckError(
                            f"{value} violates .{op} {vrange[op]}", category='value',
                            expected=f"{wanted} {vrange[op]}", actual=str(value)))
        if isinstance(value, (int, bytes)) and not isinstance(value, bool):
            bits_error = self.cddl.bits_violation(value, self.cddl.extract_bits(expr))
            if bits_error:
                errors.append(_CheckError(bits_error, category='value',
                                          expected="only the bits .bits allows",
                                          actual=self._format_value_for_log(value)))
        pattern = self.cddl.extract_regexp(expr)
        if pattern is not None and isinstance(value, str):
            regexp_error = self.cddl.regexp_violation(pattern, value)
            if regexp_error:
                if regexp_error.startswith('invalid'):  # the pattern, not the value, is wrong
                    errors.append(_CheckError(regexp_error, category='schema'))
                else:
                    errors.append(_CheckError(
                        regexp_error, category='value', expected=f"text matching .regexp /{pattern}/",
                        actual=self._format_value_for_log(value)))
        return errors

    def _match_array(self, data: Sequence, sequence: List, type_name: str) -> Optional[str]:
        """Match the elements of *data* to the entry *sequence* of an array.

        Each entry takes between its ``min`` and ``max`` elements in order,
        and a nested group takes one of its choices (RFC 8610 §3.4). Every
        assignment is followed at once, as the set of positions it can reach
        after each entry, so each element is checked against each entry type
        at most once. Returns ``None`` when one assignment takes every
        element, else an error naming the first element none gets past.
        """
        count = len(data)
        fits: Dict[Tuple[int, Type], bool] = {}
        tried: Dict[int, List[Tuple[Type, str]]] = {}
        furthest = [0]
        # Ends found so far for a group reference being followed, by (reference,
        # positions), and those of them that were entered again meanwhile.
        approximate: Dict[tuple, set] = {}
        reentered: set = set()

        # The functions below are generators that hand the group they need
        # next to ``solve`` instead of calling it, so a group that refers to
        # itself is followed one element at a time on a stack of its own and
        # not on the Python call stack: the length of the array is not limited
        # by the recursion limit (#138, #162).
        def unit_ends(entry, starts):
            if 'group' in entry:
                return (yield group_ends(entry['group'], starts))
            if 'ref' in entry:
                # A group that refers to itself: follow it from these positions.
                # Reaching it again from the same positions, before any element
                # is taken (``g = (? g, int)``), gives what is known so far, and
                # the outer visit is repeated until nothing new is found, so the
                # matches that take elements after the inner one are kept and
                # a recursion that takes none ends (a least fixed point).
                visit = (entry['ref'], frozenset(starts))
                choices = self.cddl.group_sequence(entry['ref'])
                if choices is None:
                    return set()
                if visit in approximate:
                    reentered.add(visit)
                    return set(approximate[visit])
                approximate[visit] = set()
                try:
                    while True:
                        ends = yield group_ends(choices, starts)
                        if visit not in reentered or ends <= approximate[visit]:
                            return ends
                        approximate[visit] = ends
                        reentered.discard(visit)
                finally:
                    del approximate[visit]
                    reentered.discard(visit)
            node = as_type(entry['type'])
            ends = set()
            for pos in starts:
                if pos >= count:
                    continue
                key = (pos, node)
                if key not in fits:
                    errors = self._check_value(data[pos], node)
                    fits[key] = not errors
                    if errors:
                        tried.setdefault(pos, []).append((node, errors[0]))
                if fits[key]:
                    ends.add(pos + 1)
                    furthest[0] = max(furthest[0], pos + 1)
            return ends

        def entry_ends(entry, starts):
            low, high = entry['min'], entry['max']
            ends = set(starts) if low == 0 else set()
            seen = set(ends)
            frontier, done = set(starts), 0
            while frontier and (high is None or done < high):
                done += 1
                reached = yield unit_ends(entry, frontier)
                if done >= low:
                    # a position reached again, after more repeats, adds nothing
                    reached -= seen
                    seen |= reached
                    ends |= reached
                frontier = reached
            return ends

        def group_ends(choices, starts):
            ends = set()
            for entries in choices:
                reached = set(starts)
                for entry in entries:
                    reached = yield entry_ends(entry, reached)
                    if not reached:
                        break
                ends |= reached
            return ends

        def solve(task):
            """Run *task*, a generator that yields the generators it needs."""
            stack, value = [task], None
            while stack:
                try:
                    needed = stack[-1].send(value)
                except StopIteration as done:
                    stack.pop()
                    value = done.value
                else:
                    stack.append(needed)
                    value = None
            return value

        if count in solve(group_ends(sequence, {0})):
            return None
        index = furthest[0]
        if index >= count:
            return (f"Array type '{type_name}' is missing element [{count}]: "
                    f"the array ends before its required entries")
        if index in tried:
            reasons = '; '.join(f"{format_node(node)}: {error}" for node, error in tried[index][:3])
            return f"Array element [{index}] of '{type_name}' matches no entry ({reasons})"
        return f"Array type '{type_name}' has unexpected element [{index}]"

    def _sandboxed_validate(self, value: Any, type_def: Dict, type_name: str) -> List[_CheckError]:
        """Run :meth:`_validate_type` and return its errors without keeping them.

        The errors keep what the run found out about each: the path below the
        current item, the rules entered, expected and actual, and where it sits.
        """
        saved_errors, saved_breadcrumb = self.validation_errors, list(self.breadcrumb)
        saved_path, saved_rules = list(self._data_path), list(self._rules)
        prefix, depth = self._current_path(), len(saved_breadcrumb)
        self.validation_errors = []
        try:
            self._validate_type(value, type_def, type_name)
            found = self.validation_errors
        finally:
            self.validation_errors, self.breadcrumb = saved_errors, saved_breadcrumb
            self._data_path, self._rules = saved_path, saved_rules
        return [self._relative_error(error, prefix, depth) for error in found]

    @staticmethod
    def _relative_error(error: str, prefix: Tuple[Any, ...], depth: int) -> _CheckError:
        """*error*, a recorded finding, as an error below the item at *prefix*."""
        diag = getattr(error, 'diagnostic', None)
        if diag is None:
            return _CheckError(str(error))
        below = diag.path[len(prefix):] if diag.path[:len(prefix)] == prefix else diag.path
        span = None if diag.offset is None else (diag.offset, diag.end_offset)
        return _CheckError(str(error), expected=diag.expected, actual=diag.actual,
                           category=diag.category, path=below,
                           schema_path=diag.schema_path[depth:], rule=diag.rule,
                           span=span, located=True, alternatives=diag.alternatives)

    def _is_structured(self, alt) -> bool:
        """Whether choice alternative *alt* is a map, array or tag, by name or in full."""
        resolved = self.cddl.resolve_alias(alt)
        item = query.item_ref(resolved)
        name = bare_name(resolved)
        return ((name is not None and name in self.cddl.types)
                or query.tag(resolved) is not None
                or query.is_array(resolved)
                or (item is not None and (isinstance(item.node, Tag)
                                          or getattr(item.node, 'major', 0) in (4, 5, 6))))

    def _alternative_text(self, alt) -> str:
        """*alt* as written, or its kind (``map``, ``array``) for a structure written inline."""
        text = format_node(alt)
        name = bare_name(as_type(alt))
        if '@' in text and name is not None and name in self.cddl.types:
            return self.cddl.types[name].get('type') or text  # a lifted 'r@0@2'
        return text

    def _no_alternative(self, value: Any, alternatives, failures) -> _CheckError:
        """The error for a value that matches none of *alternatives*.

        *failures* are ``(alternative, error)`` pairs. The message names every
        alternative's reason; the finding expects the choice and has the value.
        """
        shown = ' / '.join(format_node(alt) for alt in alternatives)
        reasons = '; '.join(f"{format_node(alt)}: {error}" for alt, error in failures)
        return _CheckError(f"matches none of {shown} ({reasons})",
                           expected=' / '.join(self._alternative_text(alt) for alt in alternatives),
                           actual=_describe_value(value),
                           alternatives=tuple((self._alternative_text(alt), error if isinstance(error, _CheckError)
                                               else _CheckError(error)) for alt, error in failures))

    def _check_value(self, value: Any, expr, _depth: int = 0,
                     strict: bool = False) -> List[_CheckError]:
        """Validate *value* against a CDDL type expression; return the errors.

        See :meth:`_check_errors`. Each error is a :class:`_CheckError`: its
        message, and what is known about where it is and what it expected.
        """
        return [error if isinstance(error, _CheckError) else _CheckError(error)
                for error in self._check_errors(value, expr, _depth, strict)]

    def _check_errors(self, value: Any, expr, _depth: int = 0,
                      strict: bool = False) -> List[str]:
        """Validate *value* against a CDDL type expression; return the errors.

        *expr* is a ``Type`` node (text is parsed, for the callers that have
        not moved to nodes yet, #176).

        Handles type choices (inline ``a / b`` and ``$socket`` choices), tags,
        literals, primitives with ``.size``/``.regexp``/``.ge``-style controls
        and named rules. Recorded validation state is not changed, so the
        caller decides how to report the result. Constructs this cannot check
        (inline arrays and maps, ranges, generics) are accepted, as elsewhere,
        unless *strict* is set: then they count as not matching. Computed map
        keys use *strict*, so a key type that cannot be checked does not
        allow every extra key.
        """
        expr = as_type(expr)
        if _depth > 32:
            return [f"cannot check '{format_node(expr)}'"] if strict else []  # alias cycle
        alternatives = query.alternatives(expr)
        name = bare_name(expr)
        if len(alternatives) == 1 and name is not None and name in self.cddl.type_choices:
            # '$socket', even with one alternative
            alternatives = [as_type(alt) for alt in self.cddl.type_choices[name]]
            if len(alternatives) == 1:
                return self._check_value(value, alternatives[0], _depth + 1, strict)
        if len(alternatives) > 1:
            failures = []
            matched = []
            for alt in alternatives:
                alt_errors = self._check_value(value, alt, _depth + 1, strict)
                if not alt_errors:
                    if not self.unique_choices:
                        return []
                    matched.append(alt)
                else:
                    failures.append((alt, alt_errors[0]))
            if matched:
                # Overlapping primitives ('uint / int') are ordinary; two
                # structures that both fit are an ambiguous reading (#74).
                structured = [alt for alt in matched if self._is_structured(alt)]
                if len(structured) > 1:
                    shown = format_node(expr)
                    return [_CheckError(
                        f"matches more than one alternative of "
                        f"{shown if len(shown) < 60 else 'the choice'}: "
                        f"{', '.join(format_node(alt) for alt in structured)}",
                        category='type', expected="exactly one alternative",
                        actual=f"matches {', '.join(format_node(alt) for alt in structured)}")]
                return []
            return [self._no_alternative(value, alternatives, failures)]

        split = self._split_set_controls(expr)
        if split is not None:  # 'T .eq 5', 'T .ne 5', 'T .and U', 'T .within U'
            target, operands = split
            errors = self._check_value(value, target, _depth + 1, strict)
            for op, arg in operands:
                if errors:
                    break
                errors = self._operand_errors(value, op, arg, _depth, strict)
            return errors

        paren = self._paren_parts(expr)
        if paren is not None:
            return self._paren_errors(value, expr, paren, _depth, strict)

        if query.choice_from(expr) is not None:  # '&(a: 0, b: 1)' is the choice '0 / 1'
            values = self._choice_from_values(expr)
            if values is None:
                return [f"cannot check {format_node(expr)}: its group could not be expanded"]
            if not values:
                return [f"{format_node(expr)} has no values to choose from"]
            return self._check_value(value, Type(tuple(values)), _depth + 1, strict)

        if self._is_empty_socket(expr):
            return [f"type socket {format_node(expr)} has no alternatives, so no value matches it"]

        if isinstance(query.base(expr), Unwrap):
            # '~t' with a tag is the type inside it and is resolved when the
            # tables are built (#158). What is left unwraps a map or array,
            # which gives a group, not a type, so no value matches it. It must
            # not make a whole choice accept everything (#106).
            return [f"cannot check '{format_node(expr)}': unwrapping a map or array gives a group, "
                    f"which is not a type, so no value matches it"]

        tag_info = query.tag(expr)
        if tag_info:
            expected_tag, inner = tag_info
            if not self._is_tagged(value):
                return [_CheckError(
                    f"requires CBOR tag {expected_tag}, but the data is not tagged{_tuple_hint(value)}",
                    category='tag', expected=f"tag {expected_tag}", actual=_describe_value(value))]
            if value.tag != expected_tag:
                return [_CheckError(f"requires CBOR tag {expected_tag}, got tag {value.tag}",
                                    category='tag', expected=f"tag {expected_tag}",
                                    actual=f"tag {value.tag}")]
            content_error = tagcontent.content_error(expected_tag, value.value)
            if content_error:
                return [_CheckError(content_error, category='tag',
                                    expected=f"valid tag {expected_tag} content",
                                    actual=_describe_value(value.value))]
            return self._check_value(value.value, inner, _depth + 1, strict)

        item = query.item_ref(expr)
        if item is not None:  # '#', '#1', '#7.25', '#6.<uint>(tstr)' (#167)
            return self._item_errors(value, item, _depth, strict)

        range_errors = self._range_errors(value, expr, strict)
        if range_errors is not None:
            return range_errors

        literal = self._literal_errors(value, expr)
        if literal is not None:
            return literal

        embedded = query.embedded_controls(expr)
        if embedded:  # 'bstr .cbor T', 'bstr .cborseq [* T]': decode the bytes, check what is inside
            return self._embedded_errors(value, expr, embedded, _depth, strict)

        if name is not None and name in self.cddl.types:
            return self._sandboxed_validate(value, self.cddl.types[name], name)

        head = query.head(expr)
        alias = self.cddl.alias_node(name) if name is not None else None
        if alias is not None and alias != expr:
            return self._check_value(value, alias, _depth + 1, strict)  # follow one alias step

        base = bare_name(self.cddl.resolve_alias(_name_type(head)))
        if base in self._ROOT_PRIMITIVES:
            return self._primitive_errors(value, base, expr)

        shown = format_node(expr)
        type_def = self.cddl.get_type(shown, value)
        if type_def:
            return self._sandboxed_validate(value, type_def, shown)
        if strict:
            return [f"cannot check '{shown}'"]
        logger.debug(f"Cannot check type expression '{shown}'; accepting value")
        return []

    def _embedded_errors(self, value: Any, expr, controls, _depth: int, strict: bool) -> List[str]:
        """Check a ``bstr`` with ``.cbor`` / ``.cborseq`` controls: every one must hold.

        ``.cbor`` decodes one item and checks it against its argument;
        ``.cborseq`` decodes a CBOR sequence and checks its items, as an array,
        against its argument. The bytes' own controls (``.size``) apply too.
        """
        def label(op, inner):
            shown = format_node(inner)
            return 'items' if op == 'cborseq' and '@' in shown else shown  # a lifted structure

        decoded = []
        for op, _base, inner in controls:
            if not isinstance(value, bytes):
                return [f"expected bstr (.{op} {label(op, inner)}), got {type(value).__name__}"]
            try:
                if op == 'cbor':
                    decoded.append(SimpleCBORDecoder(value).decode("cbor"))
                else:
                    decoded.append(CBOR.loads_sequence(value, keep_encoding=True))
            except Exception as exc:
                what = 'embedded CBOR' if op == 'cbor' else 'embedded CBOR sequence'
                return [f"{what} (.{op} {label(op, inner)}) does not decode: {exc}"]
        # 'bstr .cbor uint .size 1': the bytes' own controls apply too
        errors = self._primitive_errors(value, 'bstr', expr)
        for (op, _base, inner), nested in zip(controls, decoded):
            errors = errors or self._check_embedded(value, nested, inner, op == 'cborseq',
                                                    _depth + 1, strict)
        return errors

    def _check_embedded(self, payload: bytes, nested: Any, inner, sequence: bool = False,
                        _depth: int = 0, strict: bool = False) -> List[_CheckError]:
        """Check *nested*, decoded from the byte string *payload*, against *inner*.

        The errors know where in the input they are: inside the byte string, at
        the item that is at fault (#154). An error about the embedded item as a
        whole is at that item, which is all of *payload* for ``.cbor``.
        """
        with self._inside_embedded_cbor(payload, sequence):
            errors = self._check_value(nested, inner, _depth, strict)
            whole = self._embedded_item_span(self._current_path(), _NO_STEP) if errors else None
        return [self._located_at(error, whole) for error in errors]

    @classmethod
    def _located_at(cls, error: _CheckError, span: Optional[Tuple[int, int]]) -> _CheckError:
        """*error* with *span* for itself and each alternative that has no position yet.

        What is already located (found by a nested validation run) keeps its
        span. This is for errors about the embedded item as a whole, found
        while the embedded frame is open: they are reported after it closes.
        """
        if not error.located:
            error = error.reworded(str(error))
            error.span, error.located = span, True
        error.alternatives = tuple(
            item if isinstance(item, Diagnostic) else (item[0], cls._located_at(item[1], span))
            for item in error.alternatives)
        return error

    #: Controls that compare the value with their argument instead of
    #: describing its shape: equal to (``.eq``), different from (``.ne``), and
    #: matching the argument type too (``.and``, ``.within``).
    _SET_CONTROLS = frozenset({'eq', 'ne', 'and', 'within'})

    @classmethod
    def _split_set_controls(cls, expr) -> Optional[Tuple[Type, List[Tuple[str, Any]]]]:
        """``(target, [(op, arg), ...])`` for a type with ``.eq``, ``.ne``, ``.and`` or ``.within``.

        *target* is the type without those controls (its other controls, such as
        ``.size``, stay); the pairs are in source order. ``None`` when the type
        has none of them.
        """
        expr = as_type(expr)
        if len(expr.alternatives) != 1 or not isinstance(expr.alternatives[0], Control):
            return None
        operands: List[Tuple[str, Any]] = []

        def strip(node):
            if not isinstance(node, Control):
                return node
            inner = strip(node.target)
            if node.op in cls._SET_CONTROLS:
                operands.append((node.op, node.arg))
                return inner
            return inner if inner is node.target else replace(node, target=inner)

        target = strip(expr.alternatives[0])
        if not operands:
            return None
        return (target if isinstance(target, Type) else Type((target,))), operands

    def _operand_errors(self, value: Any, op: str, arg, _depth: int, strict: bool) -> List[str]:
        """Check *value* against the argument of ``.eq``, ``.ne``, ``.and`` or ``.within``."""
        operand = arg if isinstance(arg, Type) else Type((arg,))
        errors = self._check_value(value, operand, _depth + 1, strict)
        shown = format_node(operand)
        if op == 'eq':
            return [f"expected a value equal to {shown} (.eq), got {self._format_value_for_log(value)}"] \
                if errors else []
        if op == 'ne':
            return [] if errors else [
                f"expected a value other than {shown} (.ne), got {self._format_value_for_log(value)}"]
        if errors:
            return [self._reworded(errors[0], f"does not satisfy its control operand '{shown}' (.{op}): {errors[0]}")]
        return []

    def _item_errors(self, value: Any, item: 'query.ItemRef', _depth: int,
                     strict: bool) -> List[str]:
        """Check *value* against ``#``, ``#M``, ``#M.N``, ``#6(t)`` or ``#6.<type>(t)``."""
        node = item.node
        if isinstance(node, Major):
            if item.tag_type is None:
                error = major_error(value, node.major, node.info)
                return [error] if error else []
            if node.major == 6:  # '#6.<type>': the tag number is any number the type allows
                parts = tag_parts(value)
                if parts is None:
                    return [f"expected a tag (#6), got {self._describe_item(value)}"]
                errors = self._check_value(parts[0], item.tag_type, _depth + 1, True)
                return [self._reworded(errors[0], f"tag number {parts[0]} does not match "
                                       f"{format_node(item.tag_type)}: {errors[0]}")] if errors else []
            # '#7.<type>': the additional information is any number the type allows
            for info in range(256):
                if not self._check_value(info, item.tag_type, _depth + 1, True) \
                        and not major_error(value, node.major, info):
                    return []
            return [f"expected #{node.major}.<{format_node(item.tag_type)}>, "
                    f"got {self._describe_item(value)}"]
        if not isinstance(node, Tag):
            return []  # '#': any data item
        parts = tag_parts(value)
        if parts is None:
            return [f"expected a tag (#6), got {self._describe_item(value)}"]
        if item.tag_type is not None:
            errors = self._check_value(parts[0], item.tag_type, _depth + 1, True)
            if errors:
                return [self._reworded(errors[0], f"tag number {parts[0]} does not match "
                                       f"{format_node(item.tag_type)}: {errors[0]}")]
        return self._check_value(parts[1], item.inner, _depth + 1, strict)

    @staticmethod
    def _reworded(error: str, message: str) -> _CheckError:
        """*error* under *message*: the same finding, a caller's wording of it."""
        return error.reworded(message) if isinstance(error, _CheckError) else _CheckError(message)

    @staticmethod
    def _describe_item(value: Any) -> str:
        return type(value).__name__ if not isinstance(value, (int, float, str, bool)) \
            else repr(value)

    def _paren_errors(self, value: Any, expr: Type, paren: 'query.ParenParts',
                      _depth: int, strict: bool) -> List[str]:
        """Check *value* against a parenthesized type (``(T)``, ``(a / b) .ctl x``).

        The value must match one alternative inside the parentheses and,
        for that alternative, satisfy every control outside them:
        ``(uint / tstr) .size 1`` is ``uint .size 1 / tstr .size 1``. A
        control cannot be applied to a range alternative (``(1..5) .size 1``),
        so there only the range is checked.
        """
        failures = []
        for alt in paren.inner:
            errors = self._check_value(value, alt, _depth + 1, strict)
            base = query.without_controls(alt)
            if base is None and query.range_of(alt) is not None:
                # '(0..10) .ne 5': the controls apply to the number in the range
                node = query.range_of(alt)
                # either known bound gives the kind ('($low .. 10) .le 5')
                known = self._bound_literal(node.low) or self._bound_literal(node.high)
                base = None if known is None else _name_type(
                    'int' if isinstance(known, IntLit) else 'float')
            if not errors and base is not None:
                for control in paren.controls:
                    # 'uint .size 1' for the alternative 'uint'
                    controlled = Type((Control(base.alternatives[0], control.op, control.arg),))
                    errors = self._check_value(value, controlled, _depth + 1, strict)
                    if errors:
                        break
            if not errors:
                return []
            failures.append((alt, errors[0]))
        if len(failures) == 1:
            return [failures[0][1]]
        return [self._no_alternative(value, [expr], failures)]

    def _paren_parts(self, expr) -> Optional['query.ParenParts']:
        """*expr* as a parenthesized type, if it is one.

        A named choice under a control counts too: with ``m = uint / nil``,
        ``m .size 1`` is ``(uint / nil) .size 1``. So does a choice from a
        group: ``&(a: 0, b: 1) .le 0`` is ``(0 / 1) .le 0``.
        """
        expr = as_type(expr)
        paren = query.paren(expr)
        if paren is not None:
            return paren
        controls = query.controls(expr)
        if not controls:
            return None
        base = query.without_controls(expr)
        if base is not None and query.choice_from(base) is not None:
            # '&colors .size 1': the controls apply to each value of the group
            values = self._choice_from_values(base)
            return query.ParenParts([Type((v,)) for v in values] if values else [base], controls)
        if base is not None and self._is_empty_socket(base):
            return query.ParenParts([base], controls)  # '$e .size 1': still matches nothing
        resolved = self.cddl.resolve_alias(_name_type(query.head(expr)))
        name = bare_name(resolved)
        choices = self.cddl.type_choices.get(name) if name is not None else None
        alternatives = ([as_type(alt) for alt in choices] if choices
                        else query.alternatives(resolved))
        if len(alternatives) < 2 and not (name is not None and name in self.cddl.type_choices):
            return None
        return query.ParenParts(list(alternatives), controls)

    def _choice_from_values(self, expr) -> Optional[list]:
        """The values ``&( group )`` or ``&name`` chooses from, as type nodes.

        These are the types of the group's entries, keys dropped:
        ``&(a: 0, ? b: 1)`` gives the nodes ``0`` and ``1``. Entries that are
        groups (``&(base, c: 2)``) contribute their own entries. ``None`` when
        *expr* is not a choice from a group, or when the group cannot be
        expanded; :meth:`_check_value` reports the latter rather than
        accepting the value.
        """
        node = query.choice_from(expr)
        if node is None:
            return None
        group = node.group
        if group is None:
            resolved = self.cddl.resolved
            group = resolved.instantiate(node.name, node.args) if resolved is not None else None
            if group is None or not hasattr(group, 'choices'):
                return None
        values: list = []
        if not self._collect_group_values(group, values, set(), depth=0):
            return None
        return values

    def _collect_group_values(self, group, values: list, seen: set, depth: int) -> bool:
        """Add the entry types of *group* to *values*; False if one cannot be read.

        A group already being expanded (``g = (a: 1, ? g)``) adds nothing new
        and is skipped.
        """
        if depth > 64:
            return False
        for choice in group.choices:
            for entry in choice.entries:
                if isinstance(entry, Member):
                    values.extend(entry.value.alternatives)
                elif isinstance(entry, InlineGroup):
                    if not self._collect_group_values(entry.group, values, seen, depth + 1):
                        return False
                elif isinstance(entry, GroupRef):
                    key = (entry.name, entry.args)
                    if key in seen:
                        continue
                    seen.add(key)
                    resolved = self.cddl.resolved
                    inner = (resolved.instantiate(entry.name, entry.args)
                             if resolved is not None else None)
                    if inner is None or not hasattr(inner, 'choices') \
                            or not self._collect_group_values(inner, values, seen, depth + 1):
                        return False
                else:
                    return False
        return True

    @staticmethod
    def _is_socket(expr) -> bool:
        """True for a bare type socket name (``$name``)."""
        name = bare_name(as_type(expr))
        return name is not None and name.startswith('$') and not name.startswith('$$')

    def _is_empty_socket(self, expr) -> bool:
        """True for a type socket (``$name``) that has no alternatives."""
        if not self._is_socket(expr):
            return False
        name = bare_name(as_type(expr))
        return (name not in self.cddl.type_choices and name not in self.cddl.types
                and name not in self.cddl.type_aliases)

    def _is_literal(self, expr) -> bool:
        """True for a literal type (``1``, ``"x"``, ``h'01'``, ``true``), with or
        without controls (``"t" .regexp "t+"``)."""
        expr = as_type(expr)
        if query.literal(expr) is not None:
            return True
        name = bare_name(expr)
        if name not in ('true', 'false') or name in self.cddl.types \
                or name in self.cddl.type_choices:
            return False
        alias = self.cddl.alias_node(name)
        return alias is None or alias == expr  # not redefined

    def _checked_as_whole(self, expr) -> bool:
        """True for a type that only :meth:`_check_value` can check: an inline
        choice (``uint / tstr``), a type socket (``$name``), a choice from a
        group (``&(a: 0, b: 1)``), a parenthesized type (``(uint) .size 1``)
        or a literal (``1``, ``"x"``, #73) or an item reference (``#1``, #167).

        *expr* may be ``None`` or empty: no type.
        """
        if not expr:
            return False
        expr = as_type(expr)
        name = bare_name(expr)
        return (len(expr.alternatives) > 1
                or self._split_set_controls(expr) is not None
                or self._is_literal(expr)
                or query.item_ref(expr) is not None
                or bare_name(self.cddl.resolve_alias(_name_type(query.head(expr)))) in self._WHOLE_PRIMITIVES
                or (name is not None and name in self.cddl.type_choices)
                or self._is_socket(expr)
                or query.choice_from(expr) is not None
                or query.range_of(expr) is not None
                or self._is_compared_number(expr)
                or any(control.op == 'bits' for control in query.controls(expr))
                or query.cborseq_control(expr) is not None
                or len(query.embedded_controls(expr)) > 1
                or self._paren_parts(expr) is not None)

    _NUMBER_TYPES = frozenset({'uint', 'int', 'float', 'float16', 'float32', 'float64',
                               'float16-32', 'float32-64'})

    def _is_compared_number(self, expr) -> bool:
        """True for a number type with ``.ge``/``.gt``/``.le``/``.lt`` (``float .le 1.0``).

        :meth:`_check_value` checks those controls; the map-field and
        array-element paths would check only the type (#71).
        """
        base = bare_name(self.cddl.resolve_alias(_name_type(query.head(expr))))
        return base in self._NUMBER_TYPES and self.cddl.extract_value_range(expr) is not None

    def _check_primitive_type(self, value, type_name, _depth: int = 0):
        """Check whether value matches the named CDDL primitive type.

        Returns True if compatible, False if definitely incompatible, and None
        if type_name is not a recognised primitive (caller should recurse).

        Args:
            value:     The Python value decoded from CBOR.
            type_name: A CDDL primitive name such as 'uint', 'tstr', 'bool',
                       'bstr', 'float', 'int', 'nil', or 'null', or a type
                       node (an alias to a choice is checked alternative by
                       alternative).
        """
        resolved = self.cddl.resolve_alias(type_name)
        alternatives = query.alternatives(resolved)
        if len(alternatives) > 1 and _depth < 16:
            # A choice ('m = bstr / [* int]'): compatible if any alternative is,
            # incompatible only if every alternative is a mismatching primitive.
            results = [self._check_primitive_type(value, alt, _depth + 1) for alt in alternatives]
            if True in results:
                return True
            return False if all(r is False for r in results) else None
        # Strip any CDDL annotations (.size, etc.) to get just the base type
        t = query.head(resolved)
        if t == 'uint':
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            return 0 <= value < 2**64
        if t == 'int':
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            # Values beyond 64 bits can only come from bignum tags 2/3
            return -2**64 <= value < 2**64
        if t == 'nint':
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            return -2**64 <= value < 0
        if t in ('biguint', 'bignint'):
            # '#6.2(bstr)' / '#6.3(bstr)': in-range bignums stay a CBORTag of bytes;
            # the decoder turns out-of-range ones into ints
            tag_num = 2 if t == 'biguint' else 3
            if self._is_tagged(value):
                return value.tag == tag_num and isinstance(value.value, bytes)
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            return value >= 2**64 if t == 'biguint' else value < -2**64
        if t == 'bool':
            return isinstance(value, bool)
        if t in ('nil', 'null'):
            return value is None
        if t == 'float':
            return isinstance(value, float)
        if t in ('float16', 'float32', 'float64'):
            # the width it was written in, or one it fits without loss (#171)
            return isinstance(value, float) and float_width_fits(value, _FLOAT_WIDTHS[t])
        if t == 'tstr':
            return isinstance(value, str)
        if t == 'bstr':
            return isinstance(value, bytes)
        if t == 'any':
            return True
        return None

    def _format_value_for_log(self, value, max_len=50):
        """Format a value for logging, with truncation."""
        if isinstance(value, bytes):
            hex_str = value[:4].hex(' ')
            if len(value) > 4:
                return f"h'{hex_str}...' ({len(value)} bytes)"
            return f"h'{hex_str}'"
        elif isinstance(value, str):
            if len(value) > max_len:
                return f'"{value[:max_len]}..."'
            return f'"{value}"'
        elif isinstance(value, (list, tuple)):
            return f"[{len(value)} items]"
        elif isinstance(value, dict):
            return f"{{{len(value)} fields}}"
        else:
            return str(value)
    def get_errors(self) -> List[str]:
        """Return the list of validation error messages from the last :meth:`validate` call.

        Returns an empty list when the last call succeeded.  The list is
        reset at the start of every :meth:`validate` call.
        """
        return self.validation_errors

    def get_diagnostics(self) -> Tuple[Diagnostic, ...]:
        """Return the structured diagnostics of the last :meth:`validate` call.

        One per message of :meth:`get_errors`, in the same order. Empty when
        the last call succeeded.
        """
        return tuple(diagnostic_of(error) for error in self.validation_errors)


class EDNGenerator:
    """Generate annotated EDN (Extended Diagnostic Notation) from decoded CBOR data.

    EDN is the human-readable form of CBOR defined in RFC 8949 §8.  This
    generator enriches plain EDN with field-name comments derived from the
    CDDL schema.

    EDN format options
    ------------------
    ``'keyindex'`` (default)
        Keys are shown as integers with the field name as a comment::

            / name / 0: "Alice",

    ``'keyname'``
        Keys are replaced by their CDDL names as quoted strings::

            "name": "Alice",

    ``'both'``
        Both the integer key and the name are shown::

            0 / name /: "Alice",
    """

    def __init__(self, cddl_parser: CDDLParser, edn_format: str = 'keyindex'):
        """Initialise the generator.

        Args:
            cddl_parser: Parsed CDDL schema for field-name lookup.
            edn_format:  Output format — ``'keyindex'``, ``'keyname'``, or
                         ``'both'``.  Defaults to ``'keyindex'``.
        """
        self.cddl = cddl_parser
        self.edn_format = edn_format
        self.indent_level = 0
        self.indent_str = "  "
    
    def _indent_content(self, content: str) -> str:
        """Indent each line of content by one level."""
        lines = content.split('\n')
        indent = self.indent_str
        return '\n'.join(indent + line if line.strip() else line for line in lines)
    
    def _named(self, type_node: Optional[Type]) -> bool:
        """True if *type_node* is a rule the user wrote, not a synthetic name
        given to an inline map (``r@a``), so it is worth annotating."""
        if type_node is None:
            return False
        synthetic = self.cddl.synthetic_types
        return not synthetic or not (synthetic & query.names(type_node))

    @staticmethod
    def _edn_str(s: str) -> str:
        """Return *s* as a properly escaped EDN text string literal."""
        escaped = (s
                   .replace('\\', '\\\\')
                   .replace('"', '\\"')
                   .replace('\n', '\\n')
                   .replace('\r', '\\r')
                   .replace('\t', '\\t'))
        return '"' + escaped + '"'

    def generate(self, data: Any, type_name: str = None, annotate: bool = True) -> str:
        """Generate an annotated EDN string for *data*.

        Args:
            data:       Decoded CBOR data — ``dict``, ``list``, scalar, bytes,
                        or a :class:`CBORTag` for tagged items.
            type_name:  Root CDDL type name used to look up field annotations.
                        Pass ``None`` to generate plain unannotated EDN.
            annotate:   When ``False``, suppress all field-name comments.
                        Defaults to ``True``.

        Returns:
            A multi-line EDN string with field annotations.

        Example::

            cddl = CDDLParser(schema_text)
            gen  = EDNGenerator(cddl, edn_format='keyindex')
            print(gen.generate({0: "Alice", 1: 30}, "person"))
            # / person / {
            #   / name / 0: "Alice",
            #   / age  / 1: 30,
            # }
        """
        self.indent_level = 0
        try:
            return self._generate_value(data, as_type(type_name) if type_name else None, annotate)
        finally:
            self.indent_level = 0  # always reset, even when an exception propagates
    
    def _generate_value(self, value: Any, type_name: Optional[Type] = None,
                        annotate: bool = True) -> str:
        """Generate EDN for a value of type *type_name* (a ``Type`` node, or ``None``)."""
        logger.debug(f"EDN: _generate_value called with type_name='{_text_of(type_name)}'")
        
        # Resolve type choice to actual matched type
        if type_name is not None and self.cddl.choice_alternatives(type_name) is not None:
            # A choice ('$socket', 'm1 / m2'): the alternative the data matches,
            # or no type when it matches none (#74)
            resolved = self.cddl.resolve_type_choice_for_data(type_name, value)
            logger.debug(f"EDN: Resolved type choice {_text_of(type_name)} -> {_text_of(resolved)}")
            type_name = resolved
        elif type_name is not None:
            # Check if this type is an alias and resolve to final type
            resolved = self.cddl.resolve_alias(type_name)
            logger.debug(f"EDN: Checked alias for '{_text_of(type_name)}', got '{_text_of(resolved)}'")
            if resolved != type_name and bare_name(resolved) in self.cddl.type_choices:
                # It's an alias to a choice - resolve the choice
                final_resolved = self.cddl.resolve_type_choice_for_data(resolved, value)
                if final_resolved:
                    logger.debug(f"EDN: Resolved alias+choice {_text_of(type_name)} -> "
                                 f"{_text_of(resolved)} -> {_text_of(final_resolved)}")
                    type_name = final_resolved
        
        # Handle CBOR tags
        if isinstance(value, CBORTag):
            tag_num = value.tag
            inner_value = value.value
            
            # Determine the actual tag type name and inner type name
            # If type_name is a tagged type definition, extract the inner type
            tag_type_name = type_name
            inner_type_name = type_name
            
            if type_name is not None:
                # Try to extract inner type from tag notation in aliases
                # e.g., tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
                alias_value = self.cddl.type_aliases.get(bare_name(type_name))
                if alias_value is not None:
                    tag_info = query.tag(alias_value)
                    if tag_info:
                        extracted_tag_num, extracted_inner = tag_info
                        if extracted_tag_num == tag_num:
                            # This is the right tag type
                            inner_type_name = extracted_inner
                            logger.debug(f"EDN: Tag {tag_num}: outer type='{_text_of(tag_type_name)}', "
                                         f"inner type='{_text_of(inner_type_name)}'")
                            
                            # Further resolve the inner type if it's an alias
                            resolved_inner = self.cddl.resolve_alias(inner_type_name)
                            if resolved_inner != inner_type_name:
                                logger.debug(f"EDN: Resolved inner type alias {_text_of(inner_type_name)} -> "
                                             f"{_text_of(resolved_inner)}")
                                inner_type_name = resolved_inner
            
            # Check if this type has .cbor control (nested CBOR in bytes)
            if type_name is not None:
                type_def = self.cddl.get_type(type_name, inner_value)
                if type_def and type_def.get('cbor_inner_type'):
                    # Decode nested CBOR and generate EDN for it
                    inner_type = type_def['cbor_inner_type']
                    if isinstance(inner_value, bytes):
                        try:
                            logger.debug(f"EDN: Decoding nested CBOR ({len(inner_value)} bytes) for type {_text_of(inner_type)}")
                            nested_decoder = SimpleCBORDecoder(inner_value)
                            nested_data = nested_decoder.decode()
                            logger.debug(f"EDN: Successfully decoded nested CBOR")
                            
                            # Save current indent level - this is where the tag sits in parent
                            saved_indent = self.indent_level
                            
                            # Generate EDN for the decoded nested data (starts at indent 0)
                            self.indent_level = 0
                            nested_edn = self._generate_value(nested_data, inner_type, annotate)
                            
                            # Restore indent level
                            self.indent_level = saved_indent
                            
                            # Now indent the nested content to fit within bytes(...)
                            # Tag opening is at saved_indent (added by parent)
                            # bytes( should be at saved_indent + 1
                            # content should be at saved_indent + 2
                            # ) for bytes at saved_indent + 1
                            # ) for tag at saved_indent
                            
                            tag_indent = self.indent_str * self.indent_level
                            bytes_indent = self.indent_str * (self.indent_level + 1)
                            content_indent = self.indent_str * (self.indent_level + 2)
                            
                            # Indent each line of nested content
                            nested_lines = nested_edn.split('\n')
                            indented_nested = []
                            for line in nested_lines:
                                if line.strip():
                                    indented_nested.append(content_indent + line)
                                else:
                                    indented_nested.append(line)
                            
                            # Build bytes<N>(...) wrapper; N = raw byte-string length
                            _raw_len = len(inner_value)
                            bytes_wrapped = [bytes_indent + f"bytes<{_raw_len}>("]
                            bytes_wrapped.extend(indented_nested)
                            bytes_wrapped.append(bytes_indent + ")")
                            bytes_content = '\n'.join(bytes_wrapped)
                            
                            # Tag annotation and opening - NO prefix (parent adds it to first line)
                            # But all subsequent lines have full indentation
                            if annotate and self._named(tag_type_name):
                                # Add type annotation comment showing the tagged type
                                tag_comment = f"/ {_text_of(tag_type_name)} / "
                                result = f"{tag_comment}{tag_num}(\n{bytes_content}\n{tag_indent})"
                                return result
                            else:
                                result = f"{tag_num}(\n{bytes_content}\n{tag_indent})"
                                return result
                        except Exception as e:
                            logger.warning(f"EDN: Failed to decode nested CBOR: {e}")
                            # Fall back to hex representation
                            return self._generate_bytes(inner_value)
            
            # For other tagged data (non-.cbor), wrap and continue
            # Save and reset indent for inner content generation
            saved_indent = self.indent_level
            self.indent_level = 0
            inner_edn = self._generate_value(inner_value, inner_type_name, annotate)
            self.indent_level = saved_indent
            
            # Wrap in tag notation with absolute indentation
            # Tag opening at saved_indent (parent adds to first line)
            # Content at saved_indent + 1
            # Closing at saved_indent
            tag_indent = self.indent_str * self.indent_level
            content_indent = self.indent_str * (self.indent_level + 1)
            
            if '\n' in inner_edn:
                # Multi-line content - indent each line with absolute indentation
                lines = inner_edn.split('\n')
                indented_lines = []
                for line in lines:
                    if line.strip():
                        indented_lines.append(content_indent + line)
                    else:
                        indented_lines.append(line)
                
                # Build result - first line has no prefix (parent adds it)
                # All subsequent lines have full absolute indentation
                if annotate and self._named(tag_type_name):
                    tag_annotation = f"/ {_text_of(tag_type_name)} / "
                    result = f"{tag_annotation}{tag_num}(\n"
                else:
                    result = f"{tag_num}(\n"
                
                result += '\n'.join(indented_lines)
                result += f"\n{tag_indent})"
                return result
            else:
                # Single-line content
                if annotate and self._named(tag_type_name):
                    tag_annotation = f"/ {_text_of(tag_type_name)} / "
                    return f"{tag_annotation}{tag_num}({inner_edn})"
                else:
                    return f"{tag_num}({inner_edn})"
        
        if isinstance(value, dict):
            return self._generate_map(value, type_name, annotate)
        elif isinstance(value, (list, tuple)):
            logger.debug(f"EDN: Generating array with type_name='{type_name}'")
            return self._generate_array(value, type_name, annotate)
        elif isinstance(value, bytes):
            return self._generate_bytes(value)
        elif isinstance(value, str):
            return self._edn_str(value)
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif value is None:
            return "null"
        else:
            return str(value)
    
    def _edn_key(self, key: Any) -> str:
        """EDN for a map key: an array, map or tag key is written on one line."""
        if isinstance(key, CBORKey):
            key = key.value
        if isinstance(key, (str, int, float, bool)) or key is None:
            return self._generate_value(key)
        return ' '.join(line.strip() for line in self._generate_value(key, None, False).splitlines())

    def _generate_map(self, data: Dict, type_name: Optional[Type] = None, annotate: bool = True) -> str:
        """Generate EDN for a map/object."""
        if not data:
            return "{}"
        
        type_def = self.cddl.get_type(type_name) if type_name is not None else None
        
        # Add type name header if we have one and annotations are enabled
        # Format: / type / {
        type_header = ""
        if annotate and self._named(type_name) and bare_name(type_name) not in ('map', 'dict'):
            type_header = f"/ {_text_of(type_name)} / "
        
        lines = [type_header + "{"]
        self.indent_level += 1
        
        for i, (key, value) in enumerate(data.items()):
            indent = self.indent_str * self.indent_level
            
            # Get field info from CDDL if available
            field_name = None
            field_type = None
            is_registered = False
            if type_def and annotate:
                field_info = type_def['fields'].get(key)
                if field_info:
                    field_name = field_info['name']
                    field_type = field_info.get('type')
                    is_registered = field_info.get('registered', False)
            
            # Determine key string and annotation based on edn_format
            if self.edn_format == 'keyname' and is_registered and field_name:
                # Format: "name": value
                key_str = self._edn_str(field_name)
                annotation = ""
            elif self.edn_format == 'both' and is_registered and field_name:
                # Format: 0 / name /: value
                key_str = self._edn_key(key)
                key_str = f'{key_str} / {field_name} /'
                annotation = ""
            else:  # keyindex format (default)
                # Format: / name / 0: value
                key_str = self._edn_key(key)
                
                # Add annotation prefix
                annotation = ""
                if is_registered and field_name and annotate:
                    annotation = f"/ {field_name} / "
                elif field_name and field_name != str(key) and not is_registered and annotate:
                    # Regular field with comment name different from key
                    annotation = f"/ {field_name} / "
            
            # Recursively generate value with type information for nested structures
            value_str = self._generate_value(value, field_type, annotate)
            
            comma = "," if i < len(data) - 1 else ""
            lines.append(f"{indent}{annotation}{key_str}: {value_str}{comma}")
        
        self.indent_level -= 1
        lines.append(self.indent_str * self.indent_level + "}")
        
        return "\n".join(lines)
    
    def _generate_array(self, data: List, type_name: Optional[Type] = None, annotate: bool = True) -> str:
        """Generate EDN for an array."""
        if not data:
            return "[]"
        
        # Check if type_name is an inline array definition: [ + type ] or [ * type ]
        element_type = None
        element_types_by_index = {}  # For structured arrays like [type1, type2]
        repeat_index = None  # '[ int, * tstr ]': later elements repeat this entry's type
        
        if type_name is not None:
            type_text = _text_of(type_name)
            # Try inline array: [ + type ] or [ * type ]
            array_match = query.inline_array(type_name)
            if array_match:
                element_type = array_match[1]
                logger.debug(f"EDN: Inline array type detected, element type: {_text_of(element_type)}")
            else:
                # Try to get array type definition for structured arrays
                # e.g., reference-triple-record = [environment-map, [ + measurement-map ]]
                logger.debug(f"EDN: Checking if '{type_text}' is a structured array type")
                type_def = self.cddl.get_type(type_name)
                if type_def and type_def.get('type') == 'array':
                    # Check if it has element_types (structured array)
                    if 'element_types' in type_def and type_def['element_types']:
                        element_types_by_index = type_def.get('element_types', {})
                        repeat_index = type_def.get('repeat')
                        if repeat_index is None and type_def.get('occurrence') in ('*', '+'):
                            repeat_index = 0  # '[* T]'
                        logger.debug(f"EDN: Structured array type '{type_text}' with element types: "
                                     f"{ {k: format_node(v) for k, v in element_types_by_index.items()} }")
                    else:
                        logger.debug(f"EDN: Array type '{type_text}' has no element_types")
                else:
                    logger.debug(f"EDN: Type '{type_text}' is not an array type (type={type_def.get('type') if type_def else 'not found'})")
        
        # Add type name header if we have a structured array type and annotations are enabled
        # Format: / type / [
        type_header = ""
        if annotate and self._named(type_name) and not query.is_array(type_name):
            # It's a named array type (not inline array syntax)
            type_header = f"/ {_text_of(type_name)} / "
        
        lines = [type_header + "["]
        self.indent_level += 1
        
        for i, value in enumerate(data):
            indent = self.indent_str * self.indent_level
            
            # Determine the type for this specific element
            resolved_element_type = None
            
            # First check if we have a type for this specific index (structured arrays)
            if element_types_by_index and i in element_types_by_index:
                resolved_element_type = element_types_by_index[i]
                logger.debug(f"EDN: Array element [{i}] has indexed type: {_text_of(resolved_element_type)}")
            elif repeat_index is not None and i > repeat_index:
                resolved_element_type = element_types_by_index.get(repeat_index)
            elif element_type:
                # For uniform arrays, all elements have the same type
                resolved_element_type = element_type
                logger.debug(f"EDN: Array element [{i}] using uniform type: {_text_of(resolved_element_type)}")
            
            # For inline arrays with type choices, resolve the type for each element
            if resolved_element_type is not None and _is_dollar_type(resolved_element_type):
                # It's a type choice - need to resolve it
                # A tag stays a CBORTag for type resolution (tag matching)
                resolution_data = value
                
                # Try to resolve the type choice based on the actual data
                resolved = self.cddl.resolve_type_choice_for_data(resolved_element_type, resolution_data)
                if resolved:
                    resolved_element_type = resolved
                    logger.debug(f"EDN: Resolved array element [{i}] type choice to: {_text_of(resolved)}")
            elif resolved_element_type is not None:
                # Check if this type is itself a structured array
                elem_type_def = self.cddl.get_type(resolved_element_type)
                if elem_type_def and elem_type_def.get('type') == 'array' and isinstance(value, (list, tuple)):
                    # It's a structured array - pass the type so it can use element_types
                    logger.debug(f"EDN: Array element [{i}] type '{_text_of(resolved_element_type)}' is a structured array")
                    # The type name stays as-is, _generate_value will handle it
                else:
                    # Not a type choice, but might be an alias - try to resolve
                    resolved_alias = self.cddl.resolve_alias(resolved_element_type)
                    if resolved_alias != resolved_element_type:
                        logger.debug(f"EDN: Resolved array element [{i}] type alias "
                                     f"{_text_of(resolved_element_type)} -> {_text_of(resolved_alias)}")
                        resolved_element_type = resolved_alias
                        # A generic instance the tables kept inline (one that is
                        # not a map or array, #70) is annotated as its generic rule.
                        generic = query.head(resolved_alias)
                        if generic != format_node(resolved_alias) and self.cddl.get_type(generic):
                            resolved_element_type = _name_type(generic)
            
            value_str = self._generate_value(value, resolved_element_type, annotate)
            comma = "," if i < len(data) - 1 else ""
            lines.append(f"{indent}{value_str}{comma}")
        
        self.indent_level -= 1
        lines.append(self.indent_str * self.indent_level + "]")
        
        return "\n".join(lines)
    
    def _generate_bytes(self, data: bytes) -> str:
        """Generate EDN for byte string."""
        # Convert to hex string with h' prefix
        hex_str = data.hex()
        return f"h'{hex_str}'"


def load_cddl(filepath: Path, loader: Optional[ModuleLoader] = None) -> CDDLParser:
    """Load a CDDL schema file from *filepath* and return a :class:`CDDLParser`.

    Args:
        filepath: Path to the ``.cddl`` file.
        loader: Finds the modules the schema imports. By default modules are
            looked up next to the schema file.

    Returns:
        A fully parsed :class:`CDDLParser` instance ready for validation or
        EDN generation.

    Exits:
        Calls ``sys.exit(1)`` on any I/O or parse error (CLI helper).
    """
    try:
        content = filepath.read_text(encoding='utf-8')
        if loader is None:
            loader = ModuleLoader(path=[filepath.parent])
        return CDDLParser(content, source_name=str(filepath), loader=loader)
    except Exception as e:
        print(f"Error loading CDDL file: {e}")
        sys.exit(1)


def load_cbor(filepath: Path) -> Any:
    """Load and decode a CBOR binary file from *filepath*.

    Tries ``cbor2.load`` first; falls back to the bundled
    :class:`SimpleCBORDecoder` if ``cbor2`` is not installed.

    Args:
        filepath: Path to the ``.cbor`` binary file.

    Returns:
        The decoded Python object (``dict``, ``list``, scalar, etc.).

    Exits:
        Calls ``sys.exit(1)`` on any I/O or decode error (CLI helper).
    """
    try:
        # Try to use cbor2 if available
        try:
            import cbor2
            with open(filepath, 'rb') as f:
                return cbor2.load(f)
        except ImportError:
            # Fall back to simple decoder
            with open(filepath, 'rb') as f:
                data = f.read()
            decoder = SimpleCBORDecoder(data)
            return decoder.decode()
    except Exception as e:
        print(f"Error loading CBOR file: {e}")
        sys.exit(1)


def main(argv=None):
    """Entry point of the ``cddl-verify`` command (see ``--help``)."""
    try:
        _run_cli(argv)
    finally:
        _reset_cli_logging()


def _exit_json(diagnostics, *, valid, root_type=None, status=1):
    """Print the diagnostics document, the only thing on stdout, and exit."""
    sys.stdout.write(render_json(diagnostics, valid=valid, root_type=root_type) + "\n")
    sys.stdout.flush()
    sys.exit(status)


def _schema_problems(cddl_file, modules, module_path, error):
    """Every problem in the schema that could not be loaded, as diagnostics (#145)."""
    from ._api import schema_diagnostics  # _api imports this module
    try:
        found = schema_diagnostics(cddl_file, modules=modules, module_path=module_path)
    except Exception:  # noqa: BLE001 - fall back to the error that stopped the load
        found = ()
    return found or (schema_diagnostic(error),)


def _run_cli(argv=None):
    parser = argparse.ArgumentParser(
        prog='cddl-verify',
        description='Analyze CBOR against CDDL and generate annotated EDN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Decode CBOR and display annotated EDN (stdout)
  %(prog)s schema.cddl data.cbor

  # Validate CBOR against a named type and display annotated EDN
  %(prog)s schema.cddl data.cbor --type corim-map

  # Write annotated EDN to a file
  %(prog)s schema.cddl data.cbor --type corim-map --output data.edn

  # Suppress field-name annotations
  %(prog)s schema.cddl data.cbor --no-annotate

  # Use human-readable key names instead of integer indices
  %(prog)s schema.cddl data.cbor --edn-format keyname

  # Machine-readable diagnostics: stdout is one JSON document, nothing else
  %(prog)s schema.cddl data.cbor --type corim-map --diagnostics json

  # ...and the EDN to a file when the data is valid
  %(prog)s schema.cddl data.cbor --type corim-map --diagnostics json -o data.edn
        """
    )
    
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('cddl_file', type=Path, help='Path to CDDL schema file')
    parser.add_argument('cbor_file', type=Path, help='Path to CBOR data file')
    parser.add_argument('-o', '--output', type=Path, help='Output EDN file (default: stdout)')
    parser.add_argument('-t', '--type', help='Root type name from CDDL for validation')
    # Annotations are on by default.  Use --no-annotate to suppress field-name comments.
    # The old -a/--annotate flag was removed: `action='store_true', default=True` meant
    # passing -a never changed anything — the flag was always True either way.
    parser.set_defaults(annotate=True)
    parser.add_argument('--no-annotate', action='store_false', dest='annotate',
                        help='Suppress field-name comments in EDN output')
    parser.add_argument('--edn-format', choices=['keyindex', 'keyname', 'both'], default='keyindex',
                        help='EDN key format: keyindex (0: val / name /), keyname ("name": val), both (0 / name /: val)')
    parser.add_argument('-M', '--module-path', action='append', default=[], metavar='DIR',
                        type=Path,
                        help='Directory searched for imported CDDL modules (<name>.cddl); '
                             'repeatable. The schema file\'s own directory is searched last')
    parser.add_argument('--module', action='append', default=[], metavar='NAME=FILE',
                        help='CDDL file for the imported module NAME (;# import NAME as P); '
                             'repeatable')
    parser.add_argument('--unique-choices', action='store_true',
                        help='Fail when data matches more than one map, array or tag '
                             'alternative of a choice')
    parser.add_argument('--diagnostics', choices=['text', 'json'], default='text',
                        help='How problems are reported: text (messages on stderr, the default) '
                             'or json (one JSON document on stdout, progress and logs on stderr; '
                             'EDN is written only with -o)')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging of validation and type resolution')
    parser.add_argument('--show-types', action='store_true', 
                        help='Show parsed CDDL types and exit')
    
    args = parser.parse_args(argv)
    
    # Set logging level
    _configure_cli_logging(args.verbose)
    if args.verbose:
        logger.debug("Verbose logging enabled")
    
    json_mode = args.diagnostics == 'json'
    if json_mode and args.show_types:
        parser.error("--show-types cannot be combined with --diagnostics json")

    # Check if files exist
    if not args.cddl_file.exists():
        if json_mode:
            _exit_json([Diagnostic(f"CDDL file not found: {args.cddl_file}", code="schema",
                                   category="schema")], valid=False)
        print(f"Error: CDDL file not found: {args.cddl_file}")
        sys.exit(1)
    
    if not args.cbor_file.exists():
        if json_mode:
            _exit_json([Diagnostic(f"CBOR file not found: {args.cbor_file}", code="decode",
                                   category="decode")], valid=False)
        print(f"Error: CBOR file not found: {args.cbor_file}")
        sys.exit(1)
    
    # Load CDDL schema
    print(f"Loading CDDL schema: {args.cddl_file}", file=sys.stderr)
    modules = {}
    for item in args.module:
        name, sep, file = item.partition('=')
        if not sep or not name or not file:
            parser.error(f"--module expects NAME=FILE, not {item!r}")
        modules[name] = Path(file)
    loader = ModuleLoader(modules, [*args.module_path, args.cddl_file.parent])
    try:
        cddl = CDDLParser(args.cddl_file.read_text(encoding='utf-8'),
                          source_name=str(args.cddl_file), loader=loader)
    except Exception as e:  # noqa: BLE001 - any read or parse error is a schema problem
        problems = _schema_problems(args.cddl_file, modules, args.module_path, e)
        if json_mode:
            _exit_json(problems, valid=False)
        print("Error loading CDDL file:", file=sys.stderr)
        for line in render_text(problems).splitlines():
            print(f"  - {line}", file=sys.stderr)
        sys.exit(1)
    for message in cddl.warnings:
        print(f"Warning: {message}", file=sys.stderr)
    
    # Show types if requested
    if args.show_types:
        print("\nParsed CDDL Types:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        for type_name, type_def in cddl.types.items():
            print(f"\n{type_name} ({type_def['type']}):", file=sys.stderr)
            for key, field_info in type_def['fields'].items():
                opt = " (optional)" if field_info.get('optional') else ""
                registered = " [IANA registered]" if field_info.get('registered') else ""
                print(f"  {key}: {field_info['name']} -> {format_node(field_info['type'])}{opt}{registered}", 
                      file=sys.stderr)
        
        # Show groups
        if cddl.groups:
            print("\n" + "=" * 50, file=sys.stderr)
            print("CDDL Groups:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for group_name, group_fields in cddl.groups.items():
                print(f"\n{group_name}:", file=sys.stderr)
                for field in group_fields:
                    print(f"  {field}", file=sys.stderr)
        
        # Show type choices
        if cddl.type_choices:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Type Choices:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for choice_name, alternatives in cddl.type_choices.items():
                print(f"\n{choice_name}:", file=sys.stderr)
                for alt in alternatives:
                    print(f"  /= {format_node(alt)}", file=sys.stderr)
        
        # Show socket extensions
        if cddl.socket_extensions:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Socket Extensions:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for socket_name, extensions in cddl.socket_extensions.items():
                print(f"\n{socket_name}:", file=sys.stderr)
                for ext in extensions:
                    print(f"  //= {ext}", file=sys.stderr)
        
        # Show global registered parameters
        if cddl.registered_params:
            print("\n" + "=" * 50, file=sys.stderr)
            print("Global IANA Registered Parameters:", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            for keyindex, keyname in sorted(cddl.registered_params.items()):
                print(f"  {keyindex} -> {keyname}", file=sys.stderr)
        
        sys.exit(0)
    
    # Load CBOR data — single read, then decode in-memory (avoids opening the file twice)
    print(f"Loading CBOR data: {args.cbor_file}", file=sys.stderr)
    cbor_bytes = args.cbor_file.read_bytes()
    try:
        cbor_data = CBOR.loads(cbor_bytes, keep_encoding=True)
    except Exception as _e:  # noqa: BLE001 — loader raises CBORDecodeError (empty/truncated/trailing/malformed) or TypeError
        if json_mode:
            _exit_json([decode_diagnostic(_e, cbor_bytes) if isinstance(_e, CBORDecodeError)
                        else Diagnostic(str(_e), code="decode", category="decode")], valid=False)
        print(f"Error decoding CBOR: {_e}", file=sys.stderr)
        sys.exit(1)
    
    # Resolve the root type: explicit (--type) or inferred from the first CDDL entry
    root_type: Optional[str] = args.type
    type_inferred = False
    if root_type is None and cddl.first_definition:
        root_type = cddl.first_definition
        type_inferred = True
        print(f"No --type specified; using first CDDL entry: '{root_type}'", file=sys.stderr)

    # Validate against the root type.
    # - Explicit --type: hard failure on validation errors (existing behaviour).
    # - Inferred type:   log errors as warnings, then continue to show EDN output.
    checked_type, valid, diagnostics = root_type, None, ()
    if root_type:
        print(f"Validating CBOR against CDDL (type: {root_type})...", file=sys.stderr)
        analyzer = CBORAnalyzer(cddl, unique_choices=args.unique_choices)

        if analyzer.validate(cbor_data, root_type, cbor_bytes):
            valid = True
            print("[OK] Validation successful", file=sys.stderr)
        else:
            valid = False
            errors = analyzer.get_errors()
            diagnostics = analyzer.get_diagnostics()
            if type_inferred:
                # Non-fatal: warn and fall back to unannotated EDN output
                print("[WARNING] Type resolution/validation errors for inferred type "
                      f"'{root_type}' (proceeding to EDN output):", file=sys.stderr)
                for error in errors:
                    logger.warning(error)
                diagnostics = tuple(replace(d, severity="warning") for d in diagnostics)
                root_type = None  # Produce plain EDN without type annotations
            else:
                if json_mode:
                    _exit_json(diagnostics, valid=False, root_type=checked_type)
                print("[FAIL] Validation failed:", file=sys.stderr)
                for error in errors:
                    print(f"  - {error}", file=sys.stderr)
                sys.exit(1)

    if json_mode and not args.output:
        _exit_json(diagnostics, valid=valid, root_type=checked_type, status=0)

    # Generate EDN
    print("Generating EDN...", file=sys.stderr)
    generator = EDNGenerator(cddl, edn_format=args.edn_format)
    edn_output = generator.generate(cbor_data, root_type, args.annotate)
    
    # Output EDN
    if args.output:
        args.output.write_text(edn_output, encoding='utf-8')
        print(f"[OK] EDN written to: {args.output}", file=sys.stderr)
        if json_mode:
            _exit_json(diagnostics, valid=valid, root_type=checked_type, status=0)
    else:
        print("\n" + "=" * 50, file=sys.stderr)
        print("EDN Output:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print(edn_output)

if __name__ == '__main__':
    main()
