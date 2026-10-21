"""The structured checks: tags, constraints, maps and arrays and their members.

Split out of ``_validate_type_checked`` (#83); ``CBORAnalyzer`` composes it.
"""

from typing import Any, Dict, Optional, Set, Tuple

from .._cbor import CBORKey
from .._cddl import query, tagcontent
from .._cddl.ast import Unwrap
from .._cddl.printer import format_node
from .._cddl.typeexpr import bare_name
from .._decode import SimpleCBORDecoder
from .._diagnostics import _Index
from .._log import Colors, logger
from .._schema import _is_dollar_type
from .support import _CheckError, _describe_value, _mismatch_category, _tuple_hint, _UNKNOWN_TYPE


class StructuresMixin:
    """The structured checks: tags, constraints, maps and arrays and their members."""

    def _unwrap_tags(self, data: Any, type_def: Dict, type_name: str,
                     breadcrumb: str) -> Tuple[Any, bool]:
        """The data without the tags the rule declares, and whether it carried exactly those."""
        # Tags: the data must carry exactly the tags the rule declares
        # (``r = #6.501(m)``), outermost first. Any other tag is rejected.
        for expected_tag in type_def.get('cbor_tags', ()):
            if not self._is_tagged(data):
                error_msg = (f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                             f"but the data is not tagged{_tuple_hint(data)}")
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"tag {expected_tag}",
                             actual=_describe_value(data))
                return data, False
            if data.tag != expected_tag:
                error_msg = (f"Type '{type_name}' requires CBOR tag {expected_tag}, "
                             f"got tag {data.tag}")
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"tag {expected_tag}",
                             actual=f"tag {data.tag}")
                return data, False
            content_error = tagcontent.content_error(expected_tag, data.value)
            if content_error:
                error_msg = f"Type '{type_name}': {content_error}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, 'tag', expected=f"valid tag {expected_tag} content",
                             actual=_describe_value(data.value))
                return data, False
            data = data.value
            logger.debug(f"{Colors.CBOR}[{breadcrumb}] Unwrapped expected CBOR tag {expected_tag}{Colors.RESET}")
        if self._is_tagged(data):
            error_msg = (f"Type '{type_name}' expects an untagged {type_def.get('type', 'value')}, "
                         f"but the data has CBOR tag {data.tag}")
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg, 'tag', expected=f"untagged {type_def.get('type', 'value')}",
                         actual=f"tag {data.tag}")
            return data, False
        return data, True

    def _check_constraints(self, data: Any, type_def: Dict, type_name: str, breadcrumb: str) -> None:
        """``.and`` / ``.within`` around a structure: the value must match the other operand too (#120)."""
        for constraint in type_def.get('constraints', ()):
            for error in self._check_value(data, constraint):
                error_msg = f"Type '{type_name}' does not satisfy its control operand '{format_node(constraint)}': {error}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, finding=error)

    def _validate_cbor_rule(self, data: Any, type_def: Dict, type_name: str, breadcrumb: str) -> bool:
        """A rule that is a byte string holding CBOR (``bstr .cbor T``): decode it and check what is inside."""
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

    def _validate_map(self, data: Any, type_def: Dict, type_name: str, breadcrumb: str,
                      cbor_offset: Optional[int]) -> bool:
        """Check a map against its members: each one, the entries computed keys take, the rest."""
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

        # '? "a" => T' does not cut (RFC 8610 3.5.4): an entry whose value is not
        # a T is left for a later '* K => V' member that takes it (#202)
        deferred = self._deferred_keys(data, type_def)

        # Check required fields
        for key, field_info in type_def['fields'].items():
            if key in deferred:
                continue
            self._check_map_field(data, key, field_info, type_name)

        extra_keys, computed_counts = self._assign_computed_keys(data, type_def, type_name, deferred)
        self._check_computed_counts(type_def.get('computed_keys', ()), computed_counts, type_name, breadcrumb)
        self._check_repeated_choices(data, type_def, type_name, breadcrumb, computed_counts)
        self._report_unknown_keys(data, extra_keys, type_name, breadcrumb)
        return len(self.validation_errors) == 0

    def _check_map_field(self, data: Dict, key: Any, field_info: Dict, type_name: str) -> None:
        """Check one member the schema defines: present, optional or missing."""
        field_name = field_info['name']
        is_optional = field_info.get('optional', False)
        field_node = field_info.get('type') or _UNKNOWN_TYPE
        field_type = format_node(field_node)

        # Push field to breadcrumb
        self._push_breadcrumb(field_name, step=key)
        field_breadcrumb = self._get_breadcrumb()

        if key in data:
            self._check_present_field(data[key], key, field_name, field_node, field_type,
                                      type_name, field_breadcrumb)
        elif not is_optional:
            error_msg = f"Missing required field '{field_name}' (key {key}) in type '{type_name}'"
            logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}] [X] MISSING:{Colors.RESET} {error_msg}")
            self._report(error_msg, 'missing', at_parent=True,
                         expected=f"member '{field_name}' (key {key})", actual='absent')
        else:
            logger.debug(f"{Colors.DEBUG}[{field_breadcrumb}] [.]{Colors.RESET} Optional field not present")

        self._pop_breadcrumb()

    def _assign_computed_keys(self, data: Dict, type_def: Dict, type_name: str,
                              deferred: Any) -> Tuple[Set, Dict]:
        """Give the entries the schema does not name to computed-key members (``* label => value``, #104).

        Returns the keys no member took, and how many entries each member took."""
        defined_keys = set(type_def['fields'].keys()) - deferred
        actual_keys = set(data.keys())
        extra_keys = actual_keys - defined_keys
        # Keys allowed by computed-key members ('* label => value', #104)
        computed_keys = type_def.get('computed_keys', ())
        computed_counts = {}
        for key in sorted(extra_keys, key=repr):
            key_value = key.value if isinstance(key, CBORKey) else key
            candidates = []
            keyed = len(self._feature_trail)
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
            # the probes of the members the entry did not go to used no feature:
            # only the member it went to does, by its key and by its value
            del self._feature_trail[keyed:]
            self._check_value(key_value, computed_keys[chosen]['key'], strict=True)
            if chosen in matching:
                self._check_value(data[key], computed_keys[chosen]['type'])
            if chosen not in matching:
                self._push_breadcrumb(repr(key), step=key)  # the value is at the key
                for error in self._check_value(data[key], computed_keys[chosen]['type']):
                    self._report(
                        f"Value for key {key!r} in type '{type_name}' "
                        f"does not match '{format_node(computed_keys[chosen]['type'])}': {error}",
                        finding=error)
                self._pop_breadcrumb()
        return extra_keys, computed_counts

    def _check_computed_counts(self, computed_keys: Any, computed_counts: Dict, type_name: str,
                               breadcrumb: str) -> None:
        """Each computed-key member must have taken as many entries as its occurrence allows."""
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

    def _report_unknown_keys(self, data: Dict, extra_keys: Set, type_name: str, breadcrumb: str) -> None:
        """One diagnostic per unknown member, in the order they appear in the map (#155)."""
        for key in [k for k in data if k in extra_keys]:
            error_msg = f"Unknown field {key!r} not in schema for type '{type_name}'"
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg, 'unknown', key=key,
                         expected='only the members the schema defines',
                         actual=f"unknown key {key!r}")

    def _check_present_field(self, value: Any, key: Any, field_name: str, field_node: Any, field_type: str,
                              type_name: str, field_breadcrumb: str) -> None:
        """Check a member the data has: the control it carries, its type, then what is inside it."""
        value_repr = self._format_value_for_log(value)
        cbor_ctx = self._get_cbor_context(value)
        logger.debug(f"{Colors.MATCH}[{field_breadcrumb}] [OK]{Colors.RESET} Field present: " +
                   f"key={key}, type={field_type}, value={value_repr}{cbor_ctx}")

        cbor_control, type_mismatch = self._check_cbor_field(value, field_name, field_node, type_name, field_breadcrumb)

        # Tagged primitive (``x: #6.1(uint)``): check the tag, then the value inside
        written_node = field_node
        field_node, value, tag_error = self._peel_tagged_primitive(field_node, value)
        # the type as text, for messages and for rule names
        if field_node is not written_node:
            field_type = format_node(field_node)
        if tag_error:
            self._report(f"Field '{field_name}' in '{type_name}' {tag_error}")
            return

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
            return

        _base_type = query.head(_resolved) if _resolved else ''
        # Normalize CDDL built-in aliases to their canonical base types
        _alias_map = {
            'text': 'tstr', 'bytes': 'bstr',
            'nil': 'null'
        }
        _base_type = _alias_map.get(_base_type, _base_type)

        type_mismatch, mismatch_detail = self._field_type_mismatch(
            value, field_name, field_node, _resolved, _base_type, type_mismatch, field_breadcrumb)

        if type_mismatch:
            expected_text, actual_text = mismatch_detail or (field_type, _describe_value(value))
            self._report(f"Type mismatch for field '{field_name}' in '{type_name}': "
                         f"expected {expected_text}, got {actual_text}",
                         _mismatch_category(_base_type, value),
                         expected=expected_text, actual=actual_text)
        elif _base_type in self._ROOT_PRIMITIVES and _base_type != 'any':
            size_error = self.cddl.size_violation(
                value, _base_type, self.cddl.extract_size_constraint(_resolved))
            if size_error:
                logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} .size: {size_error}")
                self._report(
                    f"Field '{field_name}' in '{type_name}' violates .size: {size_error}")

        self._validate_field_value(value, field_node, field_type, field_name, field_breadcrumb, cbor_control)

    def _check_cbor_field(self, value: Any, field_name: str, field_node: Any, type_name: str,
                           field_breadcrumb: str) -> Tuple[Optional[Tuple[str, Any]], bool]:
        """A member declared ``bstr .cbor T``: decode the bytes and check what is inside.

        Returns the control (``None`` if the member has none) and whether the value is not a byte string."""
        type_mismatch = False
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

        return cbor_control, type_mismatch

    def _field_type_mismatch(self, value: Any, field_name: str, field_node: Any, _resolved: Any, _base_type: str,
                              type_mismatch: bool, field_breadcrumb: str) -> Tuple[bool, Optional[Tuple[str, str]]]:
        """Whether *value* is the wrong type for a member, and (expected, actual) when a control failed.

        *type_mismatch* is what an earlier check already found."""
        mismatch_detail = None  # (expected, actual) when a control, not the type, failed

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

        return type_mismatch, mismatch_detail

    def _validate_field_value(self, value: Any, field_node: Any, field_type: str, field_name: str,
                               field_breadcrumb: str, cbor_control: Any) -> None:
        """Recurse into the value of a present member: an inline array, a type choice or a nested type."""
        if bare_name(field_node) in ('tstr', 'uint', 'int', 'bstr', 'bool', 'float', 'any'):
            return

        # Check if field_type is an inline array definition: [ + type ] or [ * type ]
        # Other inline arrays ('[ a, * b ]') are named types of
        # their own (#133) and are checked as such.
        array_match = query.inline_array(field_node) or (
            ('', None) if query.is_array(field_node) else None)
        if array_match:
            self._validate_inline_array_field(value, array_match, field_name, field_breadcrumb)
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

    def _validate_inline_array_field(self, value: Any, array_match: Tuple[str, Any], field_name: str,
                                      field_breadcrumb: str) -> None:
        """A member written as an array (``[+ T]``, ``[* T]``): its length and each element."""
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
                self._check_inline_array_element(i, item, element_type, field_name)
                self._pop_breadcrumb()

    def _check_inline_array_element(self, i: int, item: Any, element_type: Any, field_name: str) -> None:
        """One element of a member written as an array. The caller has pushed its breadcrumb."""
        item_breadcrumb = self._get_breadcrumb()
        item_repr = self._format_value_for_log(item)
        logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")
        if element_type is None:
            return

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
                return
            if query.cbor_control(resolved_elem):
                for cbor_error in self._check_value(item, resolved_elem):
                    self._report(
                        f"Element [{i}] of field '{field_name}' {cbor_error}",
                        finding=cbor_error)
                return
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

    def _validate_array(self, data: Any, type_def: Dict, type_name: str, breadcrumb: str) -> bool:
        """Check an array against its entries: their assignment, the length and each element."""
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
                self._report(error_msg, finding=error_msg if isinstance(error_msg, _CheckError) else None)
            return len(self.validation_errors) == 0

        self._check_array_length(data, type_def, type_name, breadcrumb)
        self._check_array_elements(data, type_def, type_name)
        return len(self.validation_errors) == 0

    def _check_array_length(self, data: Any, type_def: Dict, type_name: str, breadcrumb: str) -> None:
        """The number of elements the entries' occurrences allow."""
        # Enforce the number of elements the entries' occurrences allow
        # ('[int, tstr]' takes exactly two). Without known bounds, only
        # a lone '+' entry is checked.
        length = type_def.get('length')
        if length is not None:
            low, high = length
            extra = ()  # the element an error points at, when it is one
            error_msg = None
            if len(data) < low:
                error_msg = (f"Array type '{type_name}' is missing element [{len(data)}]: "
                             f"expected at least {low} element(s), got {len(data)}")
            elif high is not None and len(data) > high:
                error_msg = (f"Array type '{type_name}' has unexpected element [{high}]: "
                             f"expected at most {high} element(s), got {len(data)}")
                extra = (_Index(high),)
            if error_msg:
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self._report(error_msg, extra_path=extra)
        elif type_def.get('occurrence', '') == '+' and len(data) == 0:
            error_msg = f"Array type '{type_name}' requires at least one element (+ occurrence)"
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg)

    def _check_array_elements(self, data: Any, type_def: Dict, type_name: str) -> None:
        """Check each element against the entry it falls under."""
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
            # Check element type: per-index pattern takes precedence over repeating
            elem_type = element_types.get(i) if i in element_types else repeating_type
            self._check_array_element(i, item, elem_type, type_name)
            self._pop_breadcrumb()

    def _check_array_element(self, i: int, item: Any, elem_type: Any, type_name: str) -> None:
        """One element of an array. The caller has pushed its breadcrumb."""
        item_breadcrumb = self._get_breadcrumb()
        item_repr = self._format_value_for_log(item)
        logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")

        if elem_type:
            elem_node, item, tag_error = self._peel_tagged_primitive(elem_type, item)
            if tag_error:
                self._report(f"Array element [{i}] of '{type_name}' {tag_error}")
                return
            # Resolve user alias first
            resolved_elem = self.cddl.resolve_alias(elem_node)
            if self._checked_as_whole(resolved_elem) or self.cddl.extract_regexp(resolved_elem) is not None:
                for choice_error in self._check_value(item, resolved_elem):
                    self._report(
                        f"Array element [{i}] of '{type_name}' {choice_error}",
                        finding=choice_error)
                return
            if query.cbor_control(resolved_elem):
                # 'bstr .cbor T': decode the bytes and check the item inside
                for cbor_error in self._check_value(item, resolved_elem):
                    self._report(
                        f"Array element [{i}] of '{type_name}' {cbor_error}",
                        finding=cbor_error)
                return
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
