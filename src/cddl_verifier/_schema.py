"""``CDDLParser``: a CDDL schema loaded, resolved and exposed as the tables the validator uses.

Split from ``_analyzer`` (#83). The one back-reference to the validator (``CBORAnalyzer``,
for choice checking, #74) is a deferred import inside the method, so this module has no
module-level dependency on ``_analyzer``.
"""

import copy
import functools
from typing import Any, Dict, List, Optional, Tuple

from ._cbor_values import CBORTag
from ._cddl import computed, parse_cddl, parse_prelude, query, xsd_regex
from ._cddl.ast import (FloatLit, GroupRef, IntLit, Name, Paren, Range, TextLit, Type, walk)
from ._cddl.legacy import LegacyTables
from ._cddl.modules import ModuleLoader, link, unloaded_warnings
from ._cddl.printer import format_node
from ._cddl.resolve import resolve
from ._cddl.typeexpr import as_type, bare_name, rename
from ._log import logger


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
        self._computed: Dict[Any, Any] = {}  # computed literals by node (RFC 9165)
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
        except xsd_regex.XsdRegexLimit as exc:  # fail closed: the value is not accepted
            return f"cannot check {value[:20]!r}... against .regexp /{pattern}/: {exc}"
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

    def compute(self, node) -> list:
        """The values of a computed literal (``3 .plus 5``, ``"a" .cat "b"``), cached.

        Raises:
            computed.ComputeError: An operand is not made of literals (see ``computed``).
        """
        cached = self._computed.get(node)
        if cached is None:
            rules = self.resolved.rules if self.resolved is not None else {}
            try:
                cached = computed.evaluate(rules, node)
            except computed.ComputeError as exc:
                cached = exc
            self._computed[node] = cached
        if isinstance(cached, Exception):
            raise cached
        return cached

    def array_entries(self, node) -> list:
        """The ``(type, occurrence)`` entries of the array *node* names, for ``.printf`` and ``.join``."""
        rules = self.resolved.rules if self.resolved is not None else {}
        entries = computed.array_entries(rules, node)
        if entries is None:
            raise computed.ComputeError("the argument must be an array that is written out")
        return entries

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
            from ._analyzer import CBORAnalyzer  # deferred: the analyzer imports this module
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
