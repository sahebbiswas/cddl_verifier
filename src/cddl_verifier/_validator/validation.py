"""The entry points: root rules, type expressions and the data checks they make.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

import logging
from typing import Any, Dict, Optional

from .._cbor_values import CBORTag
from .._cddl import query, tagcontent
from .._cddl.printer import format_node
from .._cddl.typeexpr import as_type, bare_name
from .._log import Colors, logger
from .._schema import _name_type
from .support import _describe_value, _tuple_hint


class ValidationMixin:
    """The entry points: root rules, type expressions and the data checks they make."""

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
        data, tagged = self._unwrap_tags(data, type_def, type_name, breadcrumb)
        if not tagged:
            return False

        logger.debug(f"{offset_str}{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating type '{type_name}'")
        logger.debug(f"  Expected: {type_def['type']}, Got: {type(data).__name__}")

        self._check_constraints(data, type_def, type_name, breadcrumb)

        if type_def.get('cbor_inner_type'):
            return self._validate_cbor_rule(data, type_def, type_name, breadcrumb)

        if type_def['type'] == 'map':
            return self._validate_map(data, type_def, type_name, breadcrumb, cbor_offset)
        if type_def['type'] == 'array':
            return self._validate_array(data, type_def, type_name, breadcrumb)

        return len(self.validation_errors) == 0
