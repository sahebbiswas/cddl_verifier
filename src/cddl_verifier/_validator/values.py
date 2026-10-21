"""Checking one value against one type: set controls, parentheses and choices.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

from dataclasses import replace
from typing import Any, List, Optional, Tuple

from .._cddl import computed, query, tagcontent
from .._cddl.ast import Control, IntLit, Major, Tag, Type, Unwrap
from .._cddl.items import major_error, tag_parts
from .._cddl.printer import format_node
from .._cddl.typeexpr import as_type, bare_name
from .._log import logger
from .._schema import _name_type
from .support import _CheckError, _describe_value, _tuple_hint


class ValuesMixin:
    """Checking one value against one type: set controls, parentheses and choices."""

    #: Controls that compare the value with their argument instead of
    #: describing its shape: equal to (``.eq``), different from (``.ne``), and
    #: matching the argument type too (``.and``, ``.within``).
    _SET_CONTROLS = frozenset({'eq', 'ne', 'and', 'within', 'feature'})

    def _check_value(self, value: Any, expr, _depth: int = 0,
                     strict: bool = False) -> List[_CheckError]:
        """Validate *value* against a CDDL type expression; return the errors.

        See :meth:`_check_errors`. Each error is a :class:`_CheckError`: its
        message, and what is known about where it is and what it expected.
        """
        mark = len(self._feature_trail)
        errors = [error if isinstance(error, _CheckError) else _CheckError(error)
                  for error in self._check_errors(value, expr, _depth, strict)]
        if errors:
            del self._feature_trail[mark:]  # what did not match used no feature
        return errors

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

        control = self._text_control(expr)
        if control is not None:  # '3 .plus 5', '"a" .cat "b"', 'text .b64u bytes', 'text .abnf "..."'
            return self._text_control_errors(value, expr, control, _depth, strict)

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
        """Check *value* against the argument of ``.eq``, ``.ne``, ``.and`` or ``.within``.

        For ``.feature`` the value already matched the target; the feature is used if enabled.
        """
        if op == 'feature':
            return self._feature_errors(arg)
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

    def _feature_errors(self, arg) -> List[_CheckError]:
        """Use the feature named by the controller *arg* of ``.feature``, or say it is disabled.

        RFC 9165 section 4 leaves open what a tool does with a feature it does not
        enable; here the target does not match, so a choice falls through to its other
        alternatives and a lone ``T .feature "x"`` is a failure that names the feature.
        """
        try:
            name, _detail = self.cddl.feature(arg)
        except computed.ComputeError as exc:
            # a controller that is a parameter of a generic rule: the schema checks the
            # name of every instance as it is read, here it is accepted as other controls
            # of an instance are (the instance is not expanded)
            logger.debug(f"Cannot name the feature of '.feature {format_node(arg)}': {exc}; accepting")
            return []
        if self.features is not None and name not in self.features:
            return [_CheckError(f"feature '{name}' is not enabled", category='type',
                                expected=f"feature '{name}' enabled", actual="feature disabled")]
        self._feature_trail.append(name)
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
