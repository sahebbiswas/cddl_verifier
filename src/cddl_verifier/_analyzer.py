#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator

Analyzes CBOR data against CDDL schemas and generates annotated EDN output.
Internal implementation module of the ``cddl_verifier`` package. Use the
public ``cddl_verifier`` API (``validate``, ``Validator``) instead.
"""

from dataclasses import replace
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._cddl import abnf, computed, conversions, query, tagcontent
from ._cddl.typeexpr import as_type, bare_name
from ._cddl.ast import (BytesLit, Control, FloatLit, GroupRef, InlineGroup, IntLit, Major, Member,
                        Range, Tag, TextLit, Type, Unwrap)
from ._cddl.printer import format_node
from ._cddl.items import float_width_fits, major_error, tag_parts
from ._decode import SimpleCBORDecoder  # noqa: F401 (re-exported)
from ._validator.support import (  # noqa: F401 (_CheckError, _NO_STEP: re-exported)
    MAJOR_TYPE_ARRAY, MAJOR_TYPE_MAP, _BYTE_TEXT_FORMS, _CONTROL_CHARS,
    _CheckError, _EmbeddedFrame, _FLOAT_WIDTHS, _NO_STEP, _UNKNOWN_TYPE, _describe_value,
    _entries_wanted, _mismatch_category, _tuple_hint)
from ._validator.reporting import ReportingMixin
from ._validator.embedded import EmbeddedMixin
from ._schema import CDDLParser, _is_dollar_type, _name_type  # noqa: F401 (CDDLParser: re-exported)
from ._log import Colors, logger

from ._cbor import CBORKey
from ._cbor_values import UNDEFINED, CBORSimple, CBORTag
from ._diagnostics import _Index


class CBORAnalyzer(ReportingMixin, EmbeddedMixin):
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

    #: Controls that compute a literal (``.plus``, ``.cat``, ``.det``) or read text: ABNF
    #: (RFC 9165) and the conversions of RFC 9741.
    _TEXT_CONTROLS = (computed.OPERATORS | frozenset(conversions.BYTE_DECODERS)
                      | frozenset({'abnf', 'abnfb', 'base10', 'json', 'printf', 'join'}))

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

    #: How many splits of a string ``.join`` tries before it gives up.
    _JOIN_BUDGET = 20000

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
