#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator

Analyzes CBOR data against CDDL schemas and generates annotated EDN output.
Internal implementation module of the ``cddl_verifier`` package. Use the
public ``cddl_verifier`` API (``validate``, ``Validator``) instead.
"""


import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._cddl import query, tagcontent
from ._cddl.typeexpr import as_type, bare_name
from ._cddl.ast import GroupRef, InlineGroup, Member, Tag, Type, Unwrap
from ._cddl.printer import format_node

from ._decode import SimpleCBORDecoder  # noqa: F401 (re-exported)
from ._validator.support import (  # noqa: F401 (_CheckError, _NO_STEP: re-exported)
    MAJOR_TYPE_ARRAY, MAJOR_TYPE_MAP, _BYTE_TEXT_FORMS, _CONTROL_CHARS,
    _CheckError, _EmbeddedFrame, _FLOAT_WIDTHS, _NO_STEP, _UNKNOWN_TYPE, _describe_value,
    _entries_wanted, _mismatch_category, _tuple_hint)
from ._validator.reporting import ReportingMixin
from ._validator.embedded import EmbeddedMixin
from ._validator.textcontrols import TextControlsMixin
from ._validator.primitives import PrimitivesMixin
from ._validator.values import ValuesMixin
from ._schema import CDDLParser, _is_dollar_type, _name_type  # noqa: F401 (CDDLParser: re-exported)
from ._log import Colors, logger

from ._cbor import CBORKey
from ._cbor_values import CBORTag
from ._diagnostics import _Index


class CBORAnalyzer(ReportingMixin, EmbeddedMixin, TextControlsMixin, PrimitivesMixin, ValuesMixin):
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
    
    _ROOT_PRIMITIVES = frozenset(
        {'uint', 'int', 'nint', 'biguint', 'bignint', 'bool', 'nil', 'null', 'float',
         'tstr', 'bstr', 'any', 'float16', 'float32', 'float64'})

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

            # '? "a" => T' does not cut (RFC 8610 3.5.4): an entry whose value is not
            # a T is left for a later '* K => V' member that takes it (#202)
            deferred = self._deferred_keys(data, type_def)

            # Check required fields
            for key, field_info in type_def['fields'].items():
                if key in deferred:
                    continue
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
            defined_keys = set(type_def['fields'].keys()) - deferred
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
            self._check_repeated_choices(data, type_def, type_name, breadcrumb, computed_counts)
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
                    self._report(error_msg, finding=error_msg if isinstance(error_msg, _CheckError) else None)
                return len(self.validation_errors) == 0

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
                    self._push_breadcrumb(f"[{pos}]", step=_Index(pos))  # where the element is
                    try:
                        errors = self._check_value(data[pos], node)
                    finally:
                        self._pop_breadcrumb()
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
            message = f"Array element [{index}] of '{type_name}' matches no entry ({reasons})"
            # When one entry failed inside the element (an array or map in it),
            # the finding is at that place; otherwise at the element
            deeper = [error for _, error in tried[index]
                      if isinstance(error, _CheckError) and error.path]
            if len(deeper) == 1:
                found = deeper[0]
                return _CheckError(message, expected=found.expected, actual=found.actual,
                                   category=found.category, path=(_Index(index),) + found.path,
                                   schema_path=found.schema_path, rule=found.rule,
                                   span=found.span, located=found.located)
            return _CheckError(message, path=(_Index(index),))
        return _CheckError(f"Array type '{type_name}' has unexpected element [{index}]",
                           path=(_Index(index),))

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

    def _deferred_keys(self, data: dict, type_def: dict) -> set:
        """Keys of optional members written ``K => T`` that a later member may take (#202).

        An entry whose value is not a ``T`` stays free when some computed-key member
        (``* K => V``) accepts both its key and its value; otherwise it is the
        ``K => T`` member's, and its mismatch is reported there.
        """
        computed_keys = type_def.get('computed_keys')
        if not computed_keys:
            return set()
        deferred = set()
        for key, info in type_def['fields'].items():
            if key not in data or info.get('cut', True) or not info.get('optional'):
                continue
            if not self._check_value(data[key], info.get('type') or _UNKNOWN_TYPE):
                continue  # it matches: the member takes it
            key_value = key.value if isinstance(key, CBORKey) else key
            if any(not self._check_value(key_value, c['key'], strict=True)
                   and not self._check_value(data[key], c['type']) for c in computed_keys):
                deferred.add(key)
        return deferred

    def _check_repeated_choices(self, data: dict, type_def: dict, type_name: str, breadcrumb,
                                counts: dict) -> None:
        """A group choice repeated in a map (``{+ g}``): each repetition took one alternative (#201).

        The members of all the alternatives were checked as optional ones, and the
        entries of wildcard members counted in *counts* (by position in
        ``computed_keys``). Here each alternative says how many repetitions it can
        stand for: an alternative with fixed keys is present when an entry of it
        matches, takes its other required members, and is used once (a key cannot
        repeat); one with only wildcard members is used as often as its entries
        need (each repetition takes the member's own minimum to maximum), and when
        its members are all optional as often as the choice allows. The numbers
        of repetitions the alternatives can stand for together have to meet the
        occurrence of the choice.
        """
        unbounded = float('inf')
        computed = type_def.get('computed_keys', ())

        def complain(error_msg, *args, **kwargs):
            logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
            self._report(error_msg, *args, **kwargs)

        for repeated in type_def.get('repeated', ()):
            least, most = 0, 0  # repetitions the alternatives can stand for, in total
            for alternative in repeated['alternatives']:
                types, members = alternative['types'], alternative['members']
                taken = [counts.get(m['index'], 0) for m in members]
                can_be_empty = not alternative['required'] and all(m['min'] == 0 for m in members)
                if types:
                    found = any(k in data and not self._check_value(data[k], types[k]) for k in types)
                    if not found and not any(taken):
                        most += 1 if can_be_empty else 0
                        continue
                    least += 1
                    most += 1  # one repetition: a fixed key cannot repeat
                    for key in alternative['required']:
                        if key not in data:
                            name = alternative['names'].get(key) or key
                            error_msg = f"Missing required field '{name}' (key {key}) in type '{type_name}'"
                            complain(error_msg, 'missing', at_parent=True,
                                     expected=f"member '{name}' (key {key})", actual='absent')
                    for member, count in zip(members, taken):
                        if count < member['min'] or (member['max'] is not None and count > member['max']):
                            complain(f"Type '{type_name}': an alternative of a repeated group choice takes "
                                     f"{_entries_wanted(member['min'], member['max'])} with key "
                                     f"'{format_node(computed[member['index']]['key'])}', found {count}")
                    continue
                # no fixed key: the alternative repeats as often as its entries need
                low = max([1 if any(taken) else 0] +
                          [-(-count // member['max']) for member, count in zip(members, taken)
                           if member['max']])
                high = min([unbounded] + [count // member['min'] for member, count in zip(members, taken)
                                          if member['min']])
                if not members:
                    high = unbounded  # nothing to take: any number of empty repetitions
                if high < low:
                    complain(f"Type '{type_name}': the entries of a repeated group choice cannot be "
                             f"divided into repetitions of its alternatives")
                least += low
                most += high
            wanted_low, wanted_high = repeated['min'], repeated['max']
            if (wanted_high is not None and least > wanted_high) or most < wanted_low:
                wanted = (f"at least {wanted_low}" if wanted_high is None else
                          f"exactly {wanted_low}" if wanted_low == wanted_high else
                          f"at most {wanted_high}" if wanted_low == 0 else f"{wanted_low} to {wanted_high}")
                found = least if least == most else f"{least} to {'many' if most == unbounded else most}"
                complain(f"Type '{type_name}' needs {wanted} repetitions of a group choice, found {found}")

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


def __getattr__(name):
    """The command-line layer (``_cli``) and ``EDNGenerator`` (``_edn``) moved out (#83): keep
    the old import path working.

    Lazy, because both import this module."""
    if name in _CLI_NAMES:
        from . import _cli
        return getattr(_cli, name)
    if name in _EDN_NAMES:
        from . import _edn
        return getattr(_edn, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CLI_NAMES = frozenset({'load_cddl', 'load_cbor', 'main', '_exit_json', '_schema_problems', '_run_cli'})
_EDN_NAMES = frozenset({'EDNGenerator'})


if __name__ == '__main__':
    from ._cli import main
    main()
