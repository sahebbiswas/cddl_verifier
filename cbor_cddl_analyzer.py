#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator (Standalone Version)

This version includes basic CBOR decoding without external dependencies.
For full functionality, install cbor2: pip install cbor2
"""

import argparse
import logging
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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

# Configure logging
logger = logging.getLogger('cbor_cddl_analyzer')
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.WARNING)  # Default level


class SimpleCBORDecoder:
    """Simple CBOR decoder for basic data types."""
    
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.total_size = len(data)
        self.structure_map: Dict[int, str] = {}  # Map offsets to descriptions
        
        # Log initial CBOR data
        if logger.isEnabledFor(logging.DEBUG):
            hex_preview = self._format_hex(data[:min(32, len(data))], 0)
            logger.debug(f"{Colors.CBOR}CBOR Input:{Colors.RESET} {len(data)} bytes")
            logger.debug(f"  {hex_preview}")
    
    def _format_hex(self, data: bytes, offset: int, trim_at: int = 4) -> str:
        """Format bytes as hex with offset, trimming long sequences."""
        if len(data) <= trim_at:
            hex_str = ' '.join(f'{b:02x}' for b in data)
            return f"[@{offset:04x}] {hex_str}"
        else:
            hex_start = ' '.join(f'{b:02x}' for b in data[:trim_at])
            return f"[@{offset:04x}] {hex_start} ... ({len(data)} bytes total)"
    
    def decode(self, path: str = "") -> Any:
        """Decode CBOR data.
        
        Args:
            path: Current path in the data structure for tracking
        """
        if self.pos >= len(self.data):
            raise ValueError("Unexpected end of data")
        
        initial_byte = self.data[self.pos]
        start_pos = self.pos
        self.pos += 1
        
        major_type = initial_byte >> 5
        additional_info = initial_byte & 0x1F
        
        # Log what we're decoding with path context
        if logger.isEnabledFor(logging.DEBUG):
            context = f" {Colors.CDDL}({path}){Colors.RESET}" if path else ""
            logger.debug(f"{Colors.CBOR}[@{start_pos:04x}] {initial_byte:02x}{Colors.RESET}{context} " +
                        f"major_type={major_type} add_info={additional_info}")
        
        # Store structure information
        self.structure_map[start_pos] = f"{path}: major_type={major_type}"
        
        if major_type == 0:  # unsigned integer
            return self._decode_int(additional_info)
        elif major_type == 1:  # negative integer
            return -1 - self._decode_int(additional_info)
        elif major_type == 2:  # byte string
            length = self._decode_int(additional_info)
            result = self.data[self.pos:self.pos + length]
            self.pos += length
            return result
        elif major_type == 3:  # text string
            length = self._decode_int(additional_info)
            result = self.data[self.pos:self.pos + length].decode('utf-8')
            self.pos += length
            return result
        elif major_type == 4:  # array
            length = self._decode_int(additional_info)
            return [self.decode(f"{path}[{i}]" if path else f"[{i}]") for i in range(length)]
        elif major_type == 5:  # map
            length = self._decode_int(additional_info)
            result = {}
            for i in range(length):
                key = self.decode(f"{path}[key{i}]" if path else f"[key{i}]")
                value = self.decode(f"{path}.{key}" if path else f".{key}")
                result[key] = value
            return result
        elif major_type == 6:  # tag
            tag_num = self._decode_int(additional_info)
            # Decode the tagged content
            tagged_value = self.decode(f"{path}<tag{tag_num}>" if path else f"<tag{tag_num}>")
            # Return tuple (tag_num, value) to preserve tag information
            # For now, just return the value
            return tagged_value
        elif major_type == 7:  # special
            if additional_info == 20:
                return False
            elif additional_info == 21:
                return True
            elif additional_info == 22:
                return None
            elif additional_info == 25:  # float16
                return self._decode_float16()
            elif additional_info == 26:  # float32
                return self._decode_float32()
            elif additional_info == 27:  # float64
                return self._decode_float64()
        
        raise ValueError(f"Unsupported CBOR type: major={major_type}, additional={additional_info}")
    
    def _decode_int(self, additional_info: int) -> int:
        """Decode integer value."""
        if additional_info < 24:
            return additional_info
        elif additional_info == 24:
            value = self.data[self.pos]
            self.pos += 1
            return value
        elif additional_info == 25:
            value = struct.unpack('>H', self.data[self.pos:self.pos + 2])[0]
            self.pos += 2
            return value
        elif additional_info == 26:
            value = struct.unpack('>I', self.data[self.pos:self.pos + 4])[0]
            self.pos += 4
            return value
        elif additional_info == 27:
            value = struct.unpack('>Q', self.data[self.pos:self.pos + 8])[0]
            self.pos += 8
            return value
        raise ValueError(f"Invalid additional info for integer: {additional_info}")
    
    def _decode_float16(self) -> float:
        """Decode float16."""
        # Simple conversion, not fully IEEE 754 compliant
        bits = struct.unpack('>H', self.data[self.pos:self.pos + 2])[0]
        self.pos += 2
        return float(bits)  # Simplified
    
    def _decode_float32(self) -> float:
        """Decode float32."""
        value = struct.unpack('>f', self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return value
    
    def _decode_float64(self) -> float:
        """Decode float64."""
        value = struct.unpack('>d', self.data[self.pos:self.pos + 8])[0]
        self.pos += 8
        return value


class CDDLParser:
    """Simple CDDL parser for extracting type definitions and field names."""
    
    def __init__(self, cddl_content: str):
        self.content = cddl_content
        self.types: Dict[str, Dict] = {}
        self.groups: Dict[str, List] = {}  # Store group definitions
        self.type_choices: Dict[str, List] = {}  # Store type choice alternatives
        self.socket_extensions: Dict[str, List] = {}  # Store socket extension points
        self.registered_params: Dict[int, str] = {}  # Maps keyindex to keyname
        self.type_aliases: Dict[str, str] = {}  # Store simple type aliases (name = other_name)
        self.parse()
        
        # Add built-in CDDL primitive types
        self._add_builtin_types()
        
        # WORKAROUND: Manually add CBOR tag definitions that aren't being parsed
        # These should be parsed automatically, but there's a parsing condition bug
        self.type_aliases['tagged-unsigned-corim-map'] = '#6.501(unsigned-corim-map)'
        self.type_aliases['tagged-concise-swid-tag'] = '#6.505(bytes .cbor coswid.concise-swid-tag)'
        self.type_aliases['tagged-concise-mid-tag'] = '#6.506(bytes .cbor concise-mid-tag)'
    
    def _add_builtin_types(self):
        """Add CDDL built-in primitive types."""
        # Map CDDL standard names to our internal type names
        builtin_primitives = {
            'text': 'tstr',
            'bytes': 'bstr',
            'int': 'int',
            'uint': 'uint',
            'nint': 'int',  # negative int
            'bool': 'bool',
            'true': 'bool',
            'false': 'bool',
            'nil': 'nil',
            'null': 'nil',
            'undefined': 'undefined',
            'float': 'float',
            'float16': 'float',
            'float32': 'float',
            'float64': 'float',
            'any': 'any',
        }
        
        for builtin_name, internal_type in builtin_primitives.items():
            if builtin_name not in self.type_aliases:
                self.type_aliases[builtin_name] = internal_type
                logger.debug(f"Added built-in type: {builtin_name} -> {internal_type}")
    
    def parse(self):
        """Parse CDDL content to extract type definitions."""
        lines = self.content.split('\n')
        current_type = None
        current_fields = {}
        in_group = False
        current_group_name = None
        current_group_fields = []
        in_array_def = False  # Track if we're in an array type definition
        
        for line in lines:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith(';'):
                continue
            
            # Normalize whitespace for various checks
            line_normalized = line.replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
            line_normalized = line_normalized.replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>')
            
            # Handle socket extensions ($$name //= value)
            if '//=' in line:
                self._parse_socket_extension(line)
                continue
            
            # Handle type choice additions ($name /= value)
            if '/=' in line and '//=' not in line:
                self._parse_type_choice(line)
                continue
            
            # Handle group definitions (name = ( ... ))
            # Note: Groups can span multiple lines, ending with )
            # Must NOT confuse with IANA parameters &( ... )
            if '=' in line and '(' in line and not line_normalized.startswith('&') and not '{' in line and not '[' in line and '/=' not in line:
                # Check if this looks like a group (not a simple assignment)
                # Groups have format: name = ( fields )
                equals_pos = line.index('=')
                paren_pos = line.index('(')
                
                # Make sure ( comes after =
                if paren_pos > equals_pos:
                    group_name = line[:equals_pos].strip()
                    
                    # Clear current_type and current_fields to prevent pollution
                    current_type = None
                    current_fields = {}
                    
                    if line.count('(') > line.count(')'):
                        # Multi-line group
                        in_group = True
                        current_group_name = group_name
                        current_group_fields = []
                        # Extract any fields on this line
                        content = line[paren_pos+1:].strip()
                        if content and not content.startswith('&'):
                            current_group_fields.append(content)
                        continue
                    elif line.count('(') == line.count(')'):
                        # Single-line group
                        start = paren_pos + 1
                        end = line.rindex(')')
                        group_content = line[start:end].strip()
                        if group_content and not group_content.startswith('&'):
                            self.groups[group_name] = [group_content]
                        continue
            
            # Handle multi-line group content
            if in_group:
                if ')' in line:
                    # End of group
                    in_group = False
                    # Extract content before closing paren
                    end_paren = line.index(')')
                    content = line[:end_paren].strip()
                    if content:
                        current_group_fields.append(content)
                    if current_group_name:
                        self.groups[current_group_name] = current_group_fields
                    current_group_name = None
                    current_group_fields = []
                else:
                    # Middle of group - add the entire line
                    if line.strip():
                        current_group_fields.append(line.strip())
                continue
            
            # IANA registered parameter (e.g., "&( keyname : 0 ) => value" or "& ( keyname : 0 ) => value")
            if '&(' in line_normalized and ')' in line_normalized and '=>' in line_normalized:
                self._parse_registered_param(line, current_fields)
                continue
            
            # Simple type alias (e.g., "corim = concise-rim-type-choice")
            # Also includes CBOR tag notation: tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
            # Must come before type definition checks
            if '=' in line and '{' not in line and '[' not in line and '/=' not in line and '//=' not in line:
                # Exclude lines that look like group definitions: name = (
                if not (line.rstrip().endswith('(') or '= (' in line):
                    # Check if this is an alias (name = something)
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        alias_name = parts[0].strip()
                        alias_target = parts[1].strip()
                        # Remove any comments
                        if ';' in alias_target:
                            alias_target = alias_target.split(';')[0].strip()
                        # Store all single-line non-complex definitions as aliases
                        # This includes CBOR tag notation: tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
                        if alias_target and not any(c in alias_target for c in ['{', '}', '[', ']', '&']):
                            self.type_aliases[alias_name] = alias_target
                            logger.debug(f"Parsed type alias: {alias_name} = {alias_target}")
                            continue
            
            # Type definition start (e.g., "person = {")
            if '=' in line and '{' in line and '/=' not in line:
                type_name = line.split('=')[0].strip()
                # Handle generics like "non-empty<M>"
                if '<' in type_name and '>' in type_name:
                    type_name = type_name.split('<')[0].strip()
                current_type = type_name
                current_fields = {}
                self.types[type_name] = {'fields': current_fields, 'type': 'map'}
            
            # Array type definition (e.g., "items = [")
            elif '=' in line and '[' in line and '/=' not in line and '//=' not in line:
                type_name = line.split('=')[0].strip()
                current_type = type_name
                current_fields = {}
                in_array_def = True
                self.types[type_name] = {'fields': current_fields, 'type': 'array'}
            
            # Field definition (e.g., "name: tstr" or "0: tstr" or "0 : tstr")
            # Also handles named array fields (e.g., "environment: environment-map")
            elif ':' in line and current_type and '=>' not in line:
                # Remove trailing comma and closing braces
                line = line.rstrip(',}]').strip()
                
                # Extract comment if present (field name)
                comment_name = None
                if ';' in line:
                    parts_comment = line.split(';', 1)
                    line = parts_comment[0].strip()
                    comment_name = parts_comment[1].strip()
                
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value_type = parts[1].strip()
                    
                    # Handle optional fields (marked with ? or ?)
                    optional = False
                    if key.startswith('?'):
                        optional = True
                        key = key[1:].strip()
                    
                    # Handle CBOR tag notation #6.xxx(type)
                    if '#6.' in value_type:
                        # Extract just the base type for now
                        # e.g., "#6.37(uuid-type)" -> "uuid-type"
                        if '(' in value_type and ')' in value_type:
                            start = value_type.index('(')
                            end = value_type.rindex(')')
                            value_type = value_type[start+1:end]
                    
                    # Clean up value type
                    if '?' in value_type:
                        optional = True
                        value_type = value_type.replace('?', '').strip()
                    
                    value_type = value_type.replace('?', '').strip().rstrip(',')
                    
                    # Remove quotes from string keys
                    if key.startswith('"') and key.endswith('"'):
                        key = key[1:-1]
                    
                    # Use comment as field name, or key as fallback
                    field_name = comment_name if comment_name else key
                    
                    # Try to convert numeric keys to integers
                    try:
                        numeric_key = int(key)
                        current_fields[numeric_key] = {
                            'name': field_name,
                            'type': value_type,
                            'optional': optional
                        }
                    except ValueError:
                        current_fields[key] = {
                            'name': field_name,
                            'type': value_type,
                            'optional': optional
                        }
            
            # End of type definition
            elif line == '}' or line == ']':
                current_type = None
                in_array_def = False
    
    def _parse_socket_extension(self, line: str):
        """Parse socket extension definition: $$name //= value"""
        try:
            # Remove comments
            if ';' in line:
                line = line.split(';', 1)[0].strip()
            
            # Split on //=
            parts = line.split('//=', 1)
            if len(parts) != 2:
                return
            
            socket_name = parts[0].strip()
            socket_value = parts[1].strip()
            
            # Initialize socket list if needed
            if socket_name not in self.socket_extensions:
                self.socket_extensions[socket_name] = []
            
            # Add this extension to the list
            self.socket_extensions[socket_name].append(socket_value)
            
        except (ValueError, IndexError):
            pass  # Skip malformed lines
    
    def _parse_type_choice(self, line: str):
        """Parse type choice definition: $name /= value"""
        try:
            # Remove comments
            if ';' in line:
                line = line.split(';', 1)[0].strip()
            
            # Split on /=
            parts = line.split('/=', 1)
            if len(parts) != 2:
                return
            
            choice_name = parts[0].strip()
            choice_value = parts[1].strip()
            
            # Initialize choice list if needed
            if choice_name not in self.type_choices:
                self.type_choices[choice_name] = []
            
            # Add this choice to the list
            self.type_choices[choice_name].append(choice_value)
            
        except (ValueError, IndexError):
            pass  # Skip malformed lines
    
    def _parse_registered_param(self, line: str, current_fields: Dict):
        """Parse IANA registered parameter format: &( keyname : keyindex ) => value_type
        Handles variations with extra whitespace like & (, ) =>, etc.
        Also handles optional prefix: ? & ( keyname : keyindex ) => value_type"""
        try:
            # Remove any comments first
            if ';' in line:
                line = line.split(';', 1)[0].strip()
            
            # Check for optional prefix
            optional = False
            if line.strip().startswith('?'):
                optional = True
                line = line.strip()[1:].strip()
            
            # Normalize whitespace around special characters
            # Handle: & (, &(, & (
            line = line.replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
            # Handle: ) =>, )=>, ) =>
            line = line.replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>')
            
            # Check if this is a registered parameter line
            if '&(' not in line or ')' not in line or '=>' not in line:
                return
            
            # Extract the part between &( and )
            param_start = line.index('&(') + 2
            param_end = line.index(')')
            param_part = line[param_start:param_end].strip()
            
            # Extract value type after =>
            arrow_pos = line.index('=>')
            value_type = line[arrow_pos + 2:].strip().rstrip(',')
            
            # Parse keyname : keyindex (handle extra whitespace around :)
            if ':' in param_part:
                parts = param_part.split(':', 1)
                keyname = parts[0].strip()
                keyindex_str = parts[1].strip()
                
                # Handle optional fields in value type
                if '?' in value_type:
                    optional = True
                    value_type = value_type.replace('?', '').strip()
                
                try:
                    keyindex = int(keyindex_str)
                    
                    # Store in registered params for global lookup
                    self.registered_params[keyindex] = keyname
                    
                    # Store in current fields
                    current_fields[keyindex] = {
                        'name': keyname,
                        'type': value_type,
                        'optional': optional,
                        'registered': True
                    }
                except ValueError:
                    pass  # Skip if keyindex is not a number
        except (ValueError, IndexError):
            pass  # Skip malformed lines
    
    def resolve_type_alias(self, type_name: str, max_depth: int = 10) -> str:
        """Resolve a type alias to its actual type, following the chain."""
        resolved = type_name
        depth = 0
        logger.debug(f"Resolving type alias: {type_name}")
        while resolved in self.type_aliases and depth < max_depth:
            next_resolved = self.type_aliases[resolved]
            logger.debug(f"  {resolved} -> {next_resolved}")
            resolved = next_resolved
            depth += 1
        logger.debug(f"  Final resolution: {resolved}")
        return resolved
    
    def extract_cbor_tag(self, type_string: str) -> Optional[Tuple[int, str]]:
        """Extract CBOR tag number and inner type from tag notation.
        
        E.g., '#6.501(unsigned-corim-map)' -> (501, 'unsigned-corim-map')
        """
        import re
        match = re.match(r'#6\.(\d+)\(([^)]+)\)', type_string.strip())
        if match:
            tag_num = int(match.group(1))
            inner_type = match.group(2)
            logger.debug(f"Extracted CBOR tag {tag_num} with inner type: {inner_type}")
            return (tag_num, inner_type)
        return None
    
    def resolve_type_choice_for_data(self, choice_name: str, cbor_data: Any, validator=None) -> Optional[str]:
        """Resolve a type choice by checking which alternative matches the CBOR data.
        
        Tries each alternative and picks the first one that validates successfully.
        For CBOR tagged data, also tries to match based on tag numbers.
        
        Args:
            choice_name: Name of the type choice
            cbor_data: The CBOR data to match against
            validator: Optional CBORAnalyzer instance for validation-based matching
        
        Returns:
            The matching alternative type name, or None if no match
        """
        logger.debug(f"{Colors.CDDL}Resolving type choice '{choice_name}' for CBOR data{Colors.RESET}")
        
        alternatives = self.type_choices.get(choice_name)
        if not alternatives:
            logger.debug(f"  No alternatives found for {choice_name}")
            return None
        
        logger.debug(f"  Alternatives: {alternatives}")
        logger.debug(f"  CBOR data type: {type(cbor_data).__name__}")
        
        # Strategy 1: For each alternative, try to get its type and check basic compatibility
        compatible = []
        for alt in alternatives:
            logger.debug(f"  Checking alternative: {alt}")
            
            # Try to get the type definition for this alternative
            alt_type = self.get_type(alt, cbor_data)
            if not alt_type:
                logger.debug(f"    ✗ Cannot resolve type definition")
                continue
            
            # Check basic type compatibility (map vs array vs primitive)
            expected_type = alt_type['type']
            actual_type = type(cbor_data).__name__
            
            if expected_type == 'map' and isinstance(cbor_data, dict):
                logger.debug(f"    {Colors.MATCH}✓{Colors.RESET} Compatible: map matches dict")
                compatible.append(alt)
            elif expected_type == 'array' and isinstance(cbor_data, (list, tuple)):
                logger.debug(f"    {Colors.MATCH}✓{Colors.RESET} Compatible: array matches list/tuple")
                compatible.append(alt)
            else:
                logger.debug(f"    ✗ Incompatible: {expected_type} vs {actual_type}")
        
        # If we have compatible alternatives, try validation-based matching if validator available
        if compatible and validator:
            logger.debug(f"  {len(compatible)} compatible alternatives, trying validation...")
            
            for alt in compatible:
                logger.debug(f"  Attempting validation with: {alt}")
                alt_type = self.get_type(alt, cbor_data)
                
                # Try to validate against this alternative
                # Save current validation state
                saved_errors = validator.validation_errors.copy()
                saved_breadcrumb = validator.breadcrumb.copy()
                
                # Attempt validation
                try:
                    result = validator._validate_type(cbor_data, alt_type, alt)
                    if result and len(validator.validation_errors) == len(saved_errors):
                        logger.debug(f"    {Colors.MATCH}✓ VALIDATION SUCCESS{Colors.RESET}: Selected '{alt}'")
                        # Restore state and return
                        validator.validation_errors = saved_errors
                        validator.breadcrumb = saved_breadcrumb
                        return alt
                    else:
                        logger.debug(f"    ✗ Validation failed")
                except Exception as e:
                    logger.debug(f"    ✗ Validation error: {e}")
                
                # Restore state for next attempt
                validator.validation_errors = saved_errors
                validator.breadcrumb = saved_breadcrumb
        
        # Fallback: return first compatible alternative, or first alternative if none compatible
        if compatible:
            selected = compatible[0]
            logger.debug(f"  {Colors.INFO}Selected: {selected} (first compatible){Colors.RESET}")
            return selected
        elif alternatives:
            selected = alternatives[0]
            logger.debug(f"  {Colors.WARNING}Selected: {selected} (first alternative, no validation){Colors.RESET}")
            return selected
        
        return None
    
    def get_type(self, type_name: str, cbor_data: Any = None) -> Optional[Dict]:
        """Get type definition by name, resolving aliases and type choices if needed.
        
        Args:
            type_name: Name of the type to look up
            cbor_data: Optional CBOR data to help resolve type choices
        
        Returns:
            Type definition dict or None if not found
        """
        if not type_name:
            return None
        
        logger.debug(f"Getting type: {type_name}")
        
        # First try direct lookup
        if type_name in self.types:
            logger.debug(f"  Found directly in types")
            return self.types[type_name]
        
        # Try resolving alias
        resolved_name = self.resolve_type_alias(type_name)
        if resolved_name != type_name:
            logger.debug(f"  Resolved alias: {type_name} -> {resolved_name}")
            
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
                        logger.debug(f"  Resolved type choice to: {selected}")
                        # Recursively get the selected type
                        return self.get_type(selected, cbor_data)
                else:
                    # No data to help resolve, try first alternative
                    alternatives = self.type_choices[resolved_name]
                    if alternatives:
                        logger.debug(f"  Using first alternative: {alternatives[0]}")
                        return self.get_type(alternatives[0], cbor_data)
        
        # Check if the original name is a type choice
        if type_name in self.type_choices:
            logger.debug(f"  Type is a type choice")
            if cbor_data is not None:
                selected = self.resolve_type_choice_for_data(type_name, cbor_data)
                if selected:
                    return self.get_type(selected, cbor_data)
            else:
                alternatives = self.type_choices[type_name]
                if alternatives:
                    return self.get_type(alternatives[0], cbor_data)
        
        # Try extracting from CBOR tag notation
        tag_info = self.extract_cbor_tag(type_name)
        if tag_info:
            tag_num, inner_type = tag_info
            logger.debug(f"  Extracted from tag notation: inner type = {inner_type}")
            return self.get_type(inner_type, cbor_data)
        
        # Check if type_name is an alias to CBOR tag notation
        if type_name in self.type_aliases:
            alias_value = self.type_aliases[type_name]
            tag_info = self.extract_cbor_tag(alias_value)
            if tag_info:
                tag_num, inner_type = tag_info
                logger.debug(f"  Type is alias to tag notation {tag_num}: inner type = {inner_type}")
                return self.get_type(inner_type, cbor_data)
        
        logger.debug(f"  Type not found: {type_name}")
        return None
    
    def get_field_name(self, type_name: str, key: Any) -> Optional[str]:
        """Get field name for a given key in a type."""
        type_def = self.get_type(type_name)
        if not type_def:
            return None
        
        field_info = type_def['fields'].get(key)
        if field_info:
            return field_info['name']
        return None
    
    def get_type_choices(self, choice_name: str) -> Optional[List[str]]:
        """Get all type alternatives for a type choice."""
        return self.type_choices.get(choice_name)
    
    def get_group(self, group_name: str) -> Optional[List[str]]:
        """Get group definition by name."""
        return self.groups.get(group_name)
    
    def get_socket_extensions(self, socket_name: str) -> Optional[List[str]]:
        """Get all extensions for a socket."""
        return self.socket_extensions.get(socket_name)


class CBORAnalyzer:
    """Analyzes CBOR data against CDDL schema."""
    
    def __init__(self, cddl_parser: CDDLParser):
        self.cddl = cddl_parser
        self.validation_errors: List[str] = []
        self.breadcrumb: List[str] = []  # Track CDDL path for logging
        self.cbor_bytes: Optional[bytes] = None  # Raw CBOR bytes for offset tracking
        self.offset_map: Dict[int, int] = {}  # Map data id() to byte offset
    
    def _push_breadcrumb(self, component: str):
        """Add a component to the CDDL breadcrumb."""
        self.breadcrumb.append(component)
    
    def _pop_breadcrumb(self):
        """Remove the last component from the breadcrumb."""
        if self.breadcrumb:
            self.breadcrumb.pop()
    
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
                hex_bytes = ' '.join(f'{b:02x}' for b in self.cbor_bytes[start:end])
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
            if major_type == 5 and isinstance(data, dict):  # map
                # Decode length
                length = additional_info
                if additional_info == 24:
                    length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                elif additional_info == 25:
                    if current_offset + 1 < len(cbor_bytes):
                        length = int.from_bytes(cbor_bytes[current_offset:current_offset+2], 'big')
                    current_offset += 2
                
                # Process each key-value pair
                for key, value in data.items():
                    # Process key
                    current_offset = self._skip_cbor_item(cbor_bytes, current_offset)
                    # Process value
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, value, current_offset)
                
                return current_offset
            
            elif major_type == 4 and isinstance(data, (list, tuple)):  # array
                # Similar logic for arrays
                length = additional_info
                if additional_info == 24:
                    length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
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
    
    def validate(self, data: Any, type_name: str = None, cbor_bytes: bytes = None) -> bool:
        """Validate CBOR data against CDDL schema.
        
        Args:
            data: Decoded CBOR data
            type_name: Root type name for validation
            cbor_bytes: Optional raw CBOR bytes for hex logging
        """
        self.validation_errors = []
        self.breadcrumb = []  # Reset breadcrumb
        self.cbor_bytes = cbor_bytes  # Store for offset tracking
        
        logger.info("=" * 60)
        logger.info("VALIDATION STARTED")
        logger.info("=" * 60)
        
        # Show CBOR hex if available
        if cbor_bytes and logger.isEnabledFor(logging.DEBUG):
            hex_preview = ' '.join(f'{b:02x}' for b in cbor_bytes[:min(16, len(cbor_bytes))])
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
            
            type_def = self.cddl.get_type(type_name, cbor_data=data)
            if not type_def:
                logger.warning(f"Type '{type_name}' not found, attempting resolution...")
                
                # Try to provide helpful error message
                resolved = self.cddl.resolve_type_alias(type_name)
                if resolved != type_name:
                    logger.info(f"Type '{type_name}' is an alias for '{resolved}'")
                    # It's an alias
                    choices = self.cddl.type_choices.get(resolved)
                    if choices:
                        logger.info(f"'{resolved}' is a type choice with alternatives: {choices}")
                        logger.info("Attempting to auto-resolve type choice...")
                        
                        # Try to resolve automatically with validation
                        selected = self.cddl.resolve_type_choice_for_data(resolved, data, validator=self)
                        if selected:
                            logger.info(f"{Colors.MATCH}Auto-selected: {selected}{Colors.RESET}")
                            return self.validate(data, selected, cbor_bytes)
                        
                        self.validation_errors.append(
                            f"Type '{type_name}' resolves to type choice '{resolved}' with alternatives: {', '.join(choices)}. "
                            f"Could not auto-resolve. Please specify one of the concrete types."
                        )
                    else:
                        logger.warning(f"'{resolved}' is not a concrete type definition")
                        self.validation_errors.append(
                            f"Type '{type_name}' resolves to '{resolved}' which is not a concrete type definition"
                        )
                else:
                    logger.error(f"Type '{type_name}' not found in CDDL schema")
                    self.validation_errors.append(f"Type '{type_name}' not found in CDDL")
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
    
    def _validate_type(self, data: Any, type_def: Dict, type_name: str, cbor_offset: int = None) -> bool:
        """Validate data against a specific type definition.
        
        Args:
            data: The CBOR data to validate
            type_def: The CDDL type definition
            type_name: Name of the type
            cbor_offset: Optional CBOR byte offset for this data
        """
        breadcrumb = self._get_breadcrumb()
        offset_str = f"{Colors.CBOR}[@{cbor_offset:04x}]{Colors.RESET} " if cbor_offset is not None else ""
        logger.debug(f"{offset_str}{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating type '{type_name}'")
        logger.debug(f"  Expected: {type_def['type']}, Got: {type(data).__name__}")
        
        if type_def['type'] == 'map':
            if not isinstance(data, dict):
                error_msg = f"Expected map for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}] TYPE MISMATCH:{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
                return False
            
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Checking {len(type_def['fields'])} field definitions")
            
            # Check required fields
            for key, field_info in type_def['fields'].items():
                field_name = field_info['name']
                is_optional = field_info.get('optional', False)
                field_type = field_info.get('type', 'unknown')
                
                # Push field to breadcrumb
                self._push_breadcrumb(field_name)
                field_breadcrumb = self._get_breadcrumb()
                
                if key in data:
                    value = data[key]
                    value_repr = self._format_value_for_log(value)
                    cbor_ctx = self._get_cbor_context(value)
                    logger.debug(f"{Colors.MATCH}[{field_breadcrumb}] ✓{Colors.RESET} Field present: " +
                               f"key={key}, type={field_type}, value={value_repr}{cbor_ctx}")
                    
                    # Basic type checking for primitives
                    type_mismatch = False
                    if field_type == 'uint' or field_type == 'int':
                        if not isinstance(value, int):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected {field_type}, got {type(value).__name__}")
                    elif field_type == 'tstr':
                        if not isinstance(value, str):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected tstr, got {type(value).__name__}")
                    elif field_type == 'bstr':
                        if not isinstance(value, bytes):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected bstr, got {type(value).__name__}")
                    elif field_type and not field_type.startswith('$'):
                        # It's a structured type - check basic structure
                        nested_type_def = self.cddl.get_type(field_type)
                        if nested_type_def:
                            if nested_type_def['type'] == 'map' and not isinstance(value, dict):
                                type_mismatch = True
                                logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected map, got {type(value).__name__}")
                            elif nested_type_def['type'] == 'array' and not isinstance(value, (list, tuple)):
                                type_mismatch = True
                                logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected array, got {type(value).__name__}")
                    
                    if type_mismatch:
                        self.validation_errors.append(f"Type mismatch for field '{field_name}' in '{type_name}'")
                    
                    # Recursively validate nested structures
                    if field_type and field_type not in ['tstr', 'uint', 'int', 'bstr', 'bool', 'float', 'any']:
                        # Check if field_type is a type choice
                        if field_type.startswith('$'):
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
                            nested_type_def = self.cddl.get_type(field_type)
                            if nested_type_def:
                                logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Recursing into nested type: {field_type}")
                                self._validate_type(value, nested_type_def, field_type)
                    
                elif not is_optional:
                    error_msg = f"Missing required field '{field_name}' (key {key}) in type '{type_name}'"
                    logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}] ✗ MISSING:{Colors.RESET} {error_msg}")
                    self.validation_errors.append(error_msg)
                else:
                    logger.debug(f"{Colors.DEBUG}[{field_breadcrumb}] ○{Colors.RESET} Optional field not present")
                
                self._pop_breadcrumb()
            
            # Report extra fields
            defined_keys = set(type_def['fields'].keys())
            actual_keys = set(data.keys())
            extra_keys = actual_keys - defined_keys
            if extra_keys:
                logger.warning(f"{Colors.WARNING}[{breadcrumb}]{Colors.RESET} Extra fields not in schema: {extra_keys}")
        
        elif type_def['type'] == 'array':
            if not isinstance(data, (list, tuple)):
                error_msg = f"Expected array for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}] TYPE MISMATCH:{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
                return False
            
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Array has {len(data)} elements")
            
            # Validate array elements
            for i, item in enumerate(data):
                self._push_breadcrumb(f"[{i}]")
                item_breadcrumb = self._get_breadcrumb()
                item_repr = self._format_value_for_log(item)
                logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")
                self._pop_breadcrumb()
        
        return len(self.validation_errors) == 0
    
    def _format_value_for_log(self, value: Any, max_len: int = 50) -> str:
        """Format a value for logging, with truncation."""
        if isinstance(value, bytes):
            hex_str = ' '.join(f'{b:02x}' for b in value[:4])
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
            return repr(value)
    
    def get_errors(self) -> List[str]:
        """Get validation errors."""
        return self.validation_errors


class EDNGenerator:
    """Generates annotated EDN (Extended Diagnostic Notation) from CBOR data."""
    
    def __init__(self, cddl_parser: CDDLParser, edn_format: str = 'keyindex'):
        self.cddl = cddl_parser
        self.edn_format = edn_format  # 'keyindex', 'keyname', or 'both'
        self.indent_level = 0
        self.indent_str = "  "
    
    def generate(self, data: Any, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN representation of CBOR data."""
        self.indent_level = 0
        return self._generate_value(data, type_name, annotate)
    
    def _generate_value(self, value: Any, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for a value."""
        if isinstance(value, dict):
            return self._generate_map(value, type_name, annotate)
        elif isinstance(value, (list, tuple)):
            return self._generate_array(value, type_name, annotate)
        elif isinstance(value, bytes):
            return self._generate_bytes(value)
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif value is None:
            return "null"
        else:
            return str(value)
    
    def _generate_map(self, data: Dict, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for a map/object."""
        if not data:
            return "{}"
        
        type_def = self.cddl.get_type(type_name) if type_name else None
        
        lines = ["{"]
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
                key_str = f'"{field_name}"'
                annotation = ""
            elif self.edn_format == 'both' and is_registered and field_name:
                # Format: 0 / name /: value
                if isinstance(key, str):
                    key_str = f'"{key}"'
                else:
                    key_str = str(key)
                key_str = f'{key_str} / {field_name} /'
                annotation = ""
            else:  # keyindex format (default)
                # Format: 0: value  / name /
                if isinstance(key, str):
                    key_str = f'"{key}"'
                else:
                    key_str = str(key)
                
                # Add annotation comment
                annotation = ""
                if is_registered and field_name and annotate:
                    annotation = f"  / {field_name} /"
                elif field_name and field_name != str(key) and not is_registered and annotate:
                    # Regular field with comment name different from key
                    annotation = f"  / {field_name} /"
            
            # Recursively generate value with type information for nested structures
            value_str = self._generate_value(value, field_type, annotate)
            
            comma = "," if i < len(data) - 1 else ""
            lines.append(f"{indent}{key_str}: {value_str}{comma}{annotation}")
        
        self.indent_level -= 1
        lines.append(self.indent_str * self.indent_level + "}")
        
        return "\n".join(lines)
    
    def _generate_array(self, data: List, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for an array."""
        if not data:
            return "[]"
        
        lines = ["["]
        self.indent_level += 1
        
        for i, value in enumerate(data):
            indent = self.indent_str * self.indent_level
            value_str = self._generate_value(value, None, annotate)
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


def load_cddl(filepath: Path) -> CDDLParser:
    """Load and parse CDDL file."""
    try:
        content = filepath.read_text(encoding='utf-8')
        return CDDLParser(content)
    except Exception as e:
        print(f"Error loading CDDL file: {e}")
        sys.exit(1)


def load_cbor(filepath: Path) -> Any:
    """Load CBOR file."""
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


def main():
    parser = argparse.ArgumentParser(
        description='Analyze CBOR against CDDL and generate annotated EDN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze and validate
  %(prog)s schema.cddl data.cbor --validate --type person
  
  # Generate annotated EDN
  %(prog)s schema.cddl data.cbor --output data.edn --annotate
  
  # Validate and generate EDN
  %(prog)s schema.cddl data.cbor --validate --type person --output data.edn
        """
    )
    
    parser.add_argument('cddl_file', type=Path, help='Path to CDDL schema file')
    parser.add_argument('cbor_file', type=Path, help='Path to CBOR data file')
    parser.add_argument('-o', '--output', type=Path, help='Output EDN file (default: stdout)')
    parser.add_argument('-t', '--type', help='Root type name from CDDL for validation')
    parser.add_argument('-v', '--validate', action='store_true', help='Validate CBOR against CDDL')
    parser.add_argument('-a', '--annotate', action='store_true', default=True, 
                        help='Annotate EDN with field names from CDDL (default: True)')
    parser.add_argument('--no-annotate', action='store_false', dest='annotate',
                        help='Disable annotations in EDN output')
    parser.add_argument('--edn-format', choices=['keyindex', 'keyname', 'both'], default='keyindex',
                        help='EDN key format: keyindex (0: val / name /), keyname ("name": val), both (0 / name /: val)')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging of validation and type resolution')
    parser.add_argument('--show-types', action='store_true', 
                        help='Show parsed CDDL types and exit')
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    # Check if files exist
    if not args.cddl_file.exists():
        print(f"Error: CDDL file not found: {args.cddl_file}")
        sys.exit(1)
    
    if not args.cbor_file.exists():
        print(f"Error: CBOR file not found: {args.cbor_file}")
        sys.exit(1)
    
    # Load CDDL schema
    print(f"Loading CDDL schema: {args.cddl_file}", file=sys.stderr)
    cddl = load_cddl(args.cddl_file)
    
    # Show types if requested
    if args.show_types:
        print("\nParsed CDDL Types:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        for type_name, type_def in cddl.types.items():
            print(f"\n{type_name} ({type_def['type']}):", file=sys.stderr)
            for key, field_info in type_def['fields'].items():
                opt = " (optional)" if field_info.get('optional') else ""
                registered = " [IANA registered]" if field_info.get('registered') else ""
                print(f"  {key}: {field_info['name']} -> {field_info['type']}{opt}{registered}", 
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
                    print(f"  /= {alt}", file=sys.stderr)
        
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
    
    # Load CBOR data
    print(f"Loading CBOR data: {args.cbor_file}", file=sys.stderr)
    cbor_bytes = args.cbor_file.read_bytes()  # Keep raw bytes for logging
    cbor_data = load_cbor(args.cbor_file)
    
    # Validate if requested
    if args.validate:
        print("Validating CBOR against CDDL...", file=sys.stderr)
        analyzer = CBORAnalyzer(cddl)
        
        if analyzer.validate(cbor_data, args.type, cbor_bytes):
            print("[OK] Validation successful", file=sys.stderr)
        else:
            print("[FAIL] Validation failed:", file=sys.stderr)
            for error in analyzer.get_errors():
                print(f"  - {error}", file=sys.stderr)
            sys.exit(1)
    
    # Generate EDN
    print("Generating EDN...", file=sys.stderr)
    generator = EDNGenerator(cddl, edn_format=args.edn_format)
    edn_output = generator.generate(cbor_data, args.type, args.annotate)
    
    # Output EDN
    if args.output:
        args.output.write_text(edn_output, encoding='utf-8')
        print(f"[OK] EDN written to: {args.output}", file=sys.stderr)
    else:
        print("\n" + "=" * 50, file=sys.stderr)
        print("EDN Output:", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print(edn_output)


if __name__ == '__main__':
    main()
