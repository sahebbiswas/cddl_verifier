"""``EDNGenerator``: annotated EDN (RFC 8949 section 8) from decoded CBOR and a CDDL schema.

Split from ``_analyzer`` (#83). Nothing in the validator uses it.
"""

from typing import Any, Dict, List, Optional

from ._cbor import CBORKey
from ._cbor_values import CBORTag
from ._cddl import query
from ._cddl.ast import Type
from ._cddl.printer import format_node
from ._cddl.typeexpr import as_type, bare_name
from ._decode import SimpleCBORDecoder
from ._log import logger
from ._schema import CDDLParser, _is_dollar_type, _name_type, _text_of


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
