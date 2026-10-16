"""Primitive types, ranges and literals.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

import math
from typing import Any, List, Optional, Tuple

from .._cbor_values import CBORSimple, UNDEFINED
from .._cddl import query, tagcontent
from .._cddl.ast import BytesLit, FloatLit, IntLit, Range, TextLit, Type
from .._cddl.items import float_width_fits
from .._cddl.printer import format_node
from .._cddl.typeexpr import as_type, bare_name
from .._schema import _name_type
from .support import _CheckError, _FLOAT_WIDTHS, _tuple_hint

_NODE_TYPES = (Type,)
_MEMO_LIMIT = 20_000  # entries per table; a validation meets far fewer distinct type nodes


class PrimitivesMixin:
    """Primitive types, ranges and literals."""

    #: Primitives the map-field and array-element code has no branch for, so
    #: they are checked by :meth:`_check_value` (#99).
    _WHOLE_PRIMITIVES = frozenset({'nint', 'biguint', 'bignint', 'float16', 'float32', 'float64'})

    _NUMBER_TYPES = frozenset({'uint', 'int', 'float', 'float16', 'float32', 'float64',
                               'float16-32', 'float32-64'})
    
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

    def _remembered(self, kind: str, expr, compute):
        """``compute(expr)``, kept for as long as the analyzer lives (#85).

        What the checks work out about a schema node (how it is checked, its text) does not depend
        on the data, and the same few nodes are met again for every item of an array. The key is
        the node itself (by identity, the entry holds it so the identity stays valid); anything
        that is not a node, and a table that has grown large, is not kept.
        """
        if not isinstance(expr, _NODE_TYPES):
            return compute(expr)
        table = self._memo.setdefault(kind, {})
        hit = table.get(id(expr))
        if hit is not None and hit[0] is expr:
            return hit[1]
        value = compute(expr)
        if len(table) >= _MEMO_LIMIT:
            table.clear()
        table[id(expr)] = (expr, value)
        return value

    def _checked_as_whole(self, expr) -> bool:
        """True for a type that only :meth:`_check_value` can check: an inline
        choice (``uint / tstr``), a type socket (``$name``), a choice from a
        group (``&(a: 0, b: 1)``), a parenthesized type (``(uint) .size 1``)
        or a literal (``1``, ``"x"``, #73) or an item reference (``#1``, #167).

        *expr* may be ``None`` or empty: no type.
        """
        return self._remembered('whole', expr, self._checked_as_whole_uncached)

    def _checked_as_whole_uncached(self, expr) -> bool:
        if not expr:
            return False
        expr = as_type(expr)
        name = bare_name(expr)
        return (len(expr.alternatives) > 1
                or self._split_set_controls(expr) is not None
                or self._text_control(expr) is not None
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
