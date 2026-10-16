"""Controls on text and byte strings: ``.abnf``, ``.printf``, ``.join`` and the conversions.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from .._cddl import abnf, computed, conversions
from .._cddl.ast import Control
from .._cddl.printer import format_node
from .._cddl.typeexpr import as_type
from .support import _BYTE_TEXT_FORMS, _CheckError, _describe_value


class TextControlsMixin:
    """Controls on text and byte strings: ``.abnf``, ``.printf``, ``.join`` and the conversions."""

    #: Controls that compute a literal (``.plus``, ``.cat``, ``.det``) or read text: ABNF
    #: (RFC 9165) and the conversions of RFC 9741.
    _TEXT_CONTROLS = (computed.OPERATORS | frozenset(conversions.BYTE_DECODERS)
                      | frozenset({'abnf', 'abnfb', 'base10', 'json', 'printf', 'join'}))

    #: How many splits of a string ``.join`` tries before it gives up.
    _JOIN_BUDGET = 20000

    @classmethod
    def _text_control(cls, expr) -> Optional[Control]:
        """The control when *expr* is exactly ``target .op arg`` with one of :attr:`_TEXT_CONTROLS`."""
        expr = as_type(expr)
        if len(expr.alternatives) == 1:
            node = expr.alternatives[0]
            if isinstance(node, Control) and node.op in cls._TEXT_CONTROLS:
                return node
        return None

    def _text_control_errors(self, value: Any, expr, control: Control, _depth: int,
                             strict: bool) -> List[str]:
        """Check *value* against ``target .op arg`` for the controls of :attr:`_TEXT_CONTROLS`."""
        op = control.op
        shown = format_node(expr)
        if op in computed.OPERATORS:
            try:
                items = self.cddl.compute(control)
            except computed.ComputeError as exc:
                return [_CheckError(f"cannot check '{shown}': {exc}", category='schema')]
            if computed.contains(items, value):
                return []
            wanted = self._show_items(items)
            return [_CheckError(f"expected {wanted} ('{shown}'), got {self._format_value_for_log(value)}",
                                category='value', expected=wanted,
                                actual=self._format_value_for_log(value))]
        errors = self._check_value(value, control.target, _depth + 1, strict)
        if errors:  # the target's own type and controls
            return errors
        try:
            if op in ('abnf', 'abnfb'):
                return self._abnf_errors(value, control)
            if op == 'join':
                return self._join_errors(value, control, _depth, strict)
            if not isinstance(value, str):
                return [_CheckError(f"expected tstr (.{op}), got {type(value).__name__}", category='type',
                                    expected='a text string', actual=_describe_value(value))]
            if op == 'printf':
                return self._printf_errors(value, control, _depth, strict)
            if op == 'base10':
                number = conversions.decode_base10(value)
                if number is None:
                    return [_CheckError(f"{value!r} is not a decimal integer without leading zeros "
                                        f"(.base10)", category='value', expected='.base10 integer text',
                                        actual=self._format_value_for_log(value))]
                decoded = number
            elif op == 'json':
                try:
                    decoded = conversions.decode_json(value)
                except ValueError as exc:
                    return [_CheckError(f"the text is not JSON (.json): {exc}", category='value',
                                        expected='JSON text', actual=self._format_value_for_log(value))]
            else:
                decoded = conversions.BYTE_DECODERS[op](value)
                if decoded is None:
                    return [_CheckError(f"{value!r} is not {_BYTE_TEXT_FORMS[op]} (.{op})", category='value',
                                        expected=_BYTE_TEXT_FORMS[op],
                                        actual=self._format_value_for_log(value))]
        except computed.ComputeError as exc:
            return [_CheckError(f"cannot check '{shown}': {exc}", category='schema')]
        inner = self._check_value(decoded, control.arg, _depth + 1, strict)
        return [error.reworded(f"the value read from the text (.{op}) does not match: {error}")
                if isinstance(error, _CheckError) else error for error in inner]

    @staticmethod
    def _show_items(items) -> str:
        """The values of a computed literal, as CDDL writes them (cut short)."""
        def one(item):
            if isinstance(item, computed.Interval):
                return f"{item.low}{'..' if item.inclusive else '...'}{item.high}"
            if item.kind == 'text':
                return json.dumps(item.value, ensure_ascii=False)
            if item.kind == 'bytes':
                return "h'" + item.value.hex() + "'"
            return repr(item.value)
        text = ' / '.join(one(item) for item in items[:4]) + (' / ...' if len(items) > 4 else '')
        return text if len(text) <= 80 else text[:77] + '...'

    def _abnf_errors(self, value: Any, control: Control) -> List[str]:
        """``text .abnf "..."`` and ``bytes .abnfb "..."``: the string is in the language of the ABNF."""
        op = control.op
        if not isinstance(value, (str, bytes)):
            return [_CheckError(f"expected a string (.{op}), got {type(value).__name__}", category='type',
                                expected='a text or byte string', actual=_describe_value(value))]
        items = self.cddl.compute(control.arg)
        grammar_text = computed.single(items)
        if grammar_text is None or grammar_text.kind != 'text':
            raise computed.ComputeError(f"the argument of '.{op}' must be one text string")
        try:
            grammar = abnf.compile_abnf(grammar_text.value)
        except abnf.AbnfError as exc:
            return [_CheckError(f"the argument of '.{op}' is not usable ABNF: {exc}", category='schema')]
        if op == 'abnfb':
            symbols = list(value.encode('utf-8') if isinstance(value, str) else value)
        elif isinstance(value, str):
            symbols = [ord(char) for char in value]
        else:
            try:  # '.abnf' reads the bytes as UTF-8 text
                symbols = [ord(char) for char in value.decode('utf-8')]
            except UnicodeDecodeError:
                return [_CheckError("the byte string is not UTF-8 text, which .abnf reads", category='value',
                                    expected='UTF-8 text matching the ABNF',
                                    actual=self._format_value_for_log(value))]
        try:
            if grammar.matches(symbols):
                return []
        except abnf.AbnfLimit:
            return [_CheckError(f"matching .{op} took too much work; the string was not accepted",
                                category='value', actual=self._format_value_for_log(value))]
        return [_CheckError(f"{self._format_value_for_log(value)} does not match the ABNF (.{op})",
                            category='value', expected=f"a string matching the ABNF of .{op}",
                            actual=self._format_value_for_log(value))]

    def _printf_errors(self, value: str, control: Control, _depth: int, strict: bool) -> List[str]:
        """``text .printf [format, items...]``: *value* is what ``printf`` writes for items that fit."""
        entries = self.cddl.array_entries(control.arg)
        fmt = computed.single(self.cddl.compute(entries[0][0]))
        if fmt is None or fmt.kind != 'text':
            raise computed.ComputeError("the format of '.printf' must be one text string")
        try:
            parts = conversions.parse_format(fmt.value)
        except conversions.PrintfError as exc:
            return [_CheckError(f"the format of .printf is not usable: {exc}", category='schema')]
        specs = [part for part in parts if not isinstance(part, str)]
        if len(specs) != len(entries) - 1:
            return [_CheckError(f"the format of .printf has {len(specs)} conversion(s) but the array gives "
                                f"{len(entries) - 1} data item(s)", category='schema')]
        try:
            groups = conversions.match_printf(parts, value)
        except conversions.PrintfLimit:
            return [_CheckError("matching the text against the .printf format took too many steps; it was "
                                "not accepted", category='value', actual=self._format_value_for_log(value))]
        if groups is not None:
            readings = [conversions.read_conversion(spec, group) for spec, group in zip(specs, groups)]
            if all(reading is not None for reading in readings):
                for number, (reading, (item, _occurrence)) in enumerate(zip(readings, entries[1:]), 1):
                    if self._check_value(reading, item, _depth + 1, strict):
                        break
                else:
                    return []
        return [_CheckError(f"{value!r} is not what printf writes for {format_node(control.arg)} (.printf)",
                            category='value', expected=f"text from the .printf format {fmt.value!r}",
                            actual=self._format_value_for_log(value))]

    def _join_errors(self, value: Any, control: Control, _depth: int, strict: bool) -> List[str]:
        """``text .join [a, ".", b]``: *value* is the concatenation of strings that fit the elements.

        Literal elements are markers; each other element takes the piece between
        the markers around it, and the piece must fit its type (as text, or as bytes).
        """
        if not isinstance(value, (str, bytes)):
            return [_CheckError(f"expected a string (.join), got {type(value).__name__}", category='type',
                                expected='a text or byte string', actual=_describe_value(value))]
        entries = self.cddl.array_entries(control.arg)
        elements = []  # (literal bytes, kind) or (None, type)
        for entry, occurrence in entries:
            if occurrence is not None:
                raise computed.ComputeError("the elements of .join must be written out, each once")
            try:
                literal = computed.single(self.cddl.compute(entry))
            except computed.ComputeError:
                literal = None
            if literal is not None and literal.kind in ('text', 'bytes'):
                data = literal.value.encode('utf-8') if literal.kind == 'text' else literal.value
                elements.append((data, literal.kind))
            else:
                elements.append((None, entry))
        data = value.encode('utf-8') if isinstance(value, str) else value
        wanted = f"a string joined from {format_node(control.arg)}"
        failure = [_CheckError(f"{self._format_value_for_log(value)} is not the concatenation of "
                               f"{format_node(control.arg)} (.join)", category='value', expected=wanted,
                               actual=self._format_value_for_log(value))]
        if not elements:
            return [] if not data else failure
        first_kind = elements[0][1] if elements[0][0] is not None else None
        if first_kind is not None and first_kind != ('text' if isinstance(value, str) else 'bytes'):
            return failure  # the result is the kind of string the first element is
        budget = [self._JOIN_BUDGET]
        fits: Dict[Tuple[int, int, int], bool] = {}

        def piece_fits(index: int, start: int, end: int) -> bool:
            key = (index, start, end)
            if key not in fits:
                budget[0] -= 1
                chunk = data[start:end]
                good = not self._check_value(chunk, elements[index][1], _depth + 1, strict)
                if not good:
                    try:
                        good = not self._check_value(chunk.decode('utf-8'), elements[index][1],
                                                     _depth + 1, strict)
                    except UnicodeDecodeError:
                        pass
                fits[key] = good
            return fits[key]

        reached = {0}
        for index, (literal, _kind) in enumerate(elements):
            following = set()
            for start in reached:
                if literal is not None:
                    if data.startswith(literal, start):
                        following.add(start + len(literal))
                    continue
                nxt = elements[index + 1][0] if index + 1 < len(elements) else None
                if index + 1 == len(elements):
                    ends = [len(data)]
                elif nxt is not None:  # the marker that follows tells where this piece can end
                    ends, at = [], data.find(nxt, start)
                    while at != -1:
                        ends.append(at)
                        at = data.find(nxt, at + 1)
                else:
                    ends = range(start, len(data) + 1)
                for end in ends:
                    if budget[0] <= 0:
                        return [_CheckError("matching .join tried too many ways to split the string; it "
                                            "was not accepted", category='value',
                                            actual=self._format_value_for_log(value))]
                    if piece_fits(index, start, end):
                        following.add(end)
            reached = following
            if not reached:
                return failure
        return [] if len(data) in reached else failure
