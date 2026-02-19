#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator

Analyzes CBOR data against CDDL schemas and generates annotated EDN output.
For full CBOR support, use simple_cbor module or install cbor2: pip install cbor2
"""

import argparse
import logging
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import CBOR encoder/decoder from separate module
try:
    from simple_cbor import CBOR
    HAS_SIMPLE_CBOR = True
    
    # Compatibility wrapper for existing code that uses SimpleCBORDecoder
    class SimpleCBORDecoder:
        """Compatibility wrapper around unified CBOR class."""
        def __init__(self, data: bytes):
            self.data = data
        
        def decode(self, breadcrumb: str = "") -> Any:
            """Decode CBOR data using unified CBOR class."""
            return CBOR.loads(self.data)
    
except ImportError:
    HAS_SIMPLE_CBOR = False
    # Will use inline fallback decoder below if simple_cbor not available

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


# CBOR Major Type Constants (RFC 8949)
MAJOR_TYPE_UINT = 0      # Unsigned integer
MAJOR_TYPE_NINT = 1      # Negative integer
MAJOR_TYPE_BSTR = 2      # Byte string
MAJOR_TYPE_TSTR = 3      # Text string
MAJOR_TYPE_ARRAY = 4     # Array
MAJOR_TYPE_MAP = 5       # Map
MAJOR_TYPE_TAG = 6       # Tagged item
MAJOR_TYPE_SIMPLE = 7    # Simple values (bool, null, float)

# Simple Value Constants
SIMPLE_FALSE = 20
SIMPLE_TRUE = 21
SIMPLE_NULL = 22


# Inline CBOR decoder (fallback if simple_cbor module not available)
if not HAS_SIMPLE_CBOR:
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
        
        if major_type == MAJOR_TYPE_UINT:
            return self._decode_int(additional_info)
        elif major_type == MAJOR_TYPE_NINT:
            return -1 - self._decode_int(additional_info)
        elif major_type == MAJOR_TYPE_BSTR:
            length = self._decode_int(additional_info)
            result = self.data[self.pos:self.pos + length]
            self.pos += length
            return result
        elif major_type == MAJOR_TYPE_TSTR:
            length = self._decode_int(additional_info)
            result = self.data[self.pos:self.pos + length].decode('utf-8')
            self.pos += length
            return result
        elif major_type == MAJOR_TYPE_ARRAY:
            length = self._decode_int(additional_info)
            return [self.decode(f"{path}[{i}]" if path else f"[{i}]") for i in range(length)]
        elif major_type == MAJOR_TYPE_MAP:
            length = self._decode_int(additional_info)
            result = {}
            for i in range(length):
                key = self.decode(f"{path}[key{i}]" if path else f"[key{i}]")
                value = self.decode(f"{path}.{key}" if path else f".{key}")
                result[key] = value
            return result
        elif major_type == MAJOR_TYPE_TAG:
            tag_num = self._decode_int(additional_info)
            # Decode the tagged content
            tagged_value = self.decode(f"{path}<tag{tag_num}>" if path else f"<tag{tag_num}>")
            # Return tuple (tag_num, value) to preserve tag information
            logger.debug(f"{Colors.CBOR}Tag {tag_num} wrapping:{Colors.RESET} {type(tagged_value).__name__}")
            return (tag_num, tagged_value)
        elif major_type == MAJOR_TYPE_SIMPLE:
            if additional_info == SIMPLE_FALSE:
                return False
            elif additional_info == SIMPLE_TRUE:
                return True
            elif additional_info == SIMPLE_NULL:
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
        """Decode IEEE 754 half-precision (float16) per RFC 8949 §3.3."""
        bits = struct.unpack('>H', self.data[self.pos:self.pos + 2])[0]
        self.pos += 2
        exp  = (bits >> 10) & 0x1F
        mant =  bits        & 0x3FF
        sign = -1.0 if (bits >> 15) else 1.0
        if exp == 0:    # subnormal
            return sign * (2.0 ** -14) * (mant / 1024.0)
        elif exp == 31: # infinity or NaN
            return sign * (float('inf') if mant == 0 else float('nan'))
        else:           # normal
            return sign * (2.0 ** (exp - 15)) * (1.0 + mant / 1024.0)
    
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
    * The CDDL subset covers practical attestation schemas; it does not
      implement the full RFC 8610 grammar.
    * ``.regexp`` patterns are matched with ``re.fullmatch``; Unicode locale
      flags and CDDL-specific escapes are not interpreted.
    * Value-range predicates (``.ge``, ``.gt``, ``.le``, ``.lt``) are
      enforced on ``uint`` and ``int`` fields only; ``float`` ranges are not
      evaluated.

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
    """
    
    def __init__(self, cddl_content: str):
        """Initialise the parser and immediately parse *cddl_content*.

        Args:
            cddl_content: Raw CDDL schema text.  May be empty (``""``), in
                which case only the built-in primitive aliases are available.

        After construction the parsed data is available through the instance
        attributes described in the class docstring.
        """
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
        pending_field_line = None  # Track incomplete field definitions (multi-line)
        
        for line in lines:
            line = line.strip()
            
            # Handle continuation of pending field (multi-line registered param)
            if pending_field_line:
                # This line should contain the type
                continuation = line.rstrip(',').strip()
                if continuation and not line.startswith(';'):
                    full_line = pending_field_line + ' ' + continuation
                    pending_field_line = None
                    # Re-process the complete line
                    line_normalized = full_line.replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
                    line_normalized = line_normalized.replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>')
                    if '&(' in line_normalized and ')' in line_normalized and '=>' in line_normalized:
                        self._parse_registered_param(full_line, current_fields)
                    continue
                elif not line.startswith(';'):
                    # Empty line, skip and continue waiting
                    continue
                # If it's a comment, clear pending and continue
                pending_field_line = None
            
            # Skip comments and empty lines
            if not line or line.startswith(';'):
                continue
            
            # Normalize whitespace for various checks
            line_normalized = line.replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
            line_normalized = line_normalized.replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>')
            # Strip quoted strings once; used by structural-char guards below so that
            # .regexp patterns (e.g. "[A-Z]{3}") don't trigger map/array detection.
            _line_unquoted = re.sub(r'"[^"]*"', '', line)
            
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
            # Must NOT confuse with IANA parameters &( ... ) or annotations like .size (M..N)
            if '=' in line and '(' in line and not line_normalized.startswith('&') and not '{' in line and not '[' in line and '/=' not in line and '#6.' not in line:
                # Check if this looks like a group (not a simple assignment)
                # Groups have format: name = ( fields ) or name = (
                # Annotations have format: name = type .constraint (value)
                # Match "name = (" with optional whitespace
                if re.match(r'^[^=]+=\s*\(', line.strip()):
                    # This is a group definition
                    equals_pos = line.index('=')
                    paren_pos = line.index('(')
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
            
            # Single-line map body: name = { ... } with all fields on one line.
            # Must be checked BEFORE the IANA-param check, because a line like
            #   record = { &(name:0)=>tstr, &(age:1)=>uint }
            # also satisfies '&(' / '=>' and would otherwise be mis-dispatched
            # to _parse_registered_param with no current_type set.
            # Use _line_unquoted so .regexp quantifiers like {3} don't match.
            if ('=' in line and '{' in _line_unquoted and '}' in _line_unquoted
                    and '/=' not in line and '//=' not in line
                    and not line_normalized.startswith('&')):
                equals_pos  = line.index('=')
                type_name   = line[:equals_pos].strip()
                if '<' in type_name and '>' in type_name:
                    type_name = type_name.split('<')[0].strip()
                brace_open  = line.index('{')
                brace_close = line.rindex('}')
                body = line[brace_open + 1 : brace_close].strip()
                current_fields = {}
                self.types[type_name] = {'fields': current_fields, 'type': 'map'}
                if body:
                    tokens = [t.strip() for t in body.split(',') if t.strip()]
                    for token in tokens:
                        tok_norm = (token
                                    .replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
                                    .replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>'))
                        if '&(' in tok_norm and '=>' in tok_norm:
                            self._parse_registered_param(token, current_fields)
                        elif ':' in token and '=>' not in token:
                            token = token.rstrip(',}]').strip()
                            optional = token.startswith('?')
                            if optional:
                                token = token[1:].strip()
                            parts = token.split(':', 1)
                            if len(parts) == 2:
                                key        = parts[0].strip().strip('"')
                                value_type = parts[1].strip()
                                size_c = self.extract_size_constraint(value_type)
                                entry  = {'name': key, 'type': value_type, 'optional': optional}
                                if size_c:
                                    entry['size_constraint'] = size_c
                                try:
                                    current_fields[int(key)] = entry
                                except ValueError:
                                    current_fields[key] = entry
                current_type = None  # fully parsed on this line
                continue

            # IANA registered parameter (e.g., "&( keyname : 0 ) => value" or "& ( keyname : 0 ) => value")
            if '&(' in line_normalized and ')' in line_normalized and '=>' in line_normalized:
                # Check if value type is on the same line
                arrow_pos = line.index('=>')
                value_after_arrow = line[arrow_pos + 2:].strip().rstrip(',')
                if not value_after_arrow or value_after_arrow == '':
                    # Type is on next line, save this line as pending
                    pending_field_line = line
                    continue
                else:
                    self._parse_registered_param(line, current_fields)
                    continue
            
            # Simple type alias (e.g., "corim = concise-rim-type-choice")
            # Also includes CBOR tag notation: tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
            # Also includes annotated primitives: short-text = tstr .size (1..10)
            # Must come before type definition checks
            # Strip quoted strings before structural-char guards so that .regexp
            # patterns (which contain "[...]" literals) are not incorrectly excluded.
            if '=' in line and '{' not in _line_unquoted and '[' not in _line_unquoted and '/=' not in line and '//=' not in line:
                # Exclude lines that look like group definitions: name = (content)
                # but allow .size (M..N) annotations which have '(' later in the line
                if not re.match(r'^[^=]+=\s*\(', line.strip()):
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
                        # _line_unquoted (computed above) strips quoted strings so that
                        # .regexp patterns don't trigger the structural-char exclusion.
                        _alias_check = re.sub(r'"[^"]*"', '', alias_target)
                        if alias_target and not any(c in _alias_check for c in ['{', '}', '[', ']', '&']):
                            self.type_aliases[alias_name] = alias_target
                            logger.debug(f"Parsed type alias: {alias_name} = {alias_target}")
                            continue
            
            # Type definition start (e.g., "person = {" or single-line "person = { ... }")
            if '=' in line and '{' in line and '/=' not in line:
                type_name = line.split('=')[0].strip()
                # Handle generics like "non-empty<M>"
                if '<' in type_name and '>' in type_name:
                    type_name = type_name.split('<')[0].strip()
                current_type = type_name
                current_fields = {}
                self.types[type_name] = {'fields': current_fields, 'type': 'map'}
            
            # Array type definition (e.g., "items = [" or "numbers = [ + uint ]")
            elif '=' in line and '[' in line and '/=' not in line and '//=' not in line:
                type_name = line.split('=')[0].strip()
                current_type = type_name
                current_fields = {}
                # Try to extract inline occurrence and element type: [ + uint ], [ * tstr ]
                inline_match = re.match(r'.*=\s*\[\s*([+*]?)\s*([^\]]+?)\s*\]', line)
                if inline_match:
                    inline_occurrence = inline_match.group(1).strip()  # '+', '*', or ''
                    inline_elem_type  = inline_match.group(2).strip()
                    self.types[type_name] = {
                        'fields': current_fields,
                        'type': 'array',
                        'element_types': {0: inline_elem_type},
                        'occurrence': inline_occurrence,
                    }
                    current_type = None  # Single-line definition — no body to parse
                else:
                    self.types[type_name] = {
                        'fields': current_fields,
                        'type': 'array',
                        'element_types': {},
                        'occurrence': '',
                    }
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
        
        # Post-processing: Convert named array fields to indexed element_types
        for type_name, type_def in self.types.items():
            if type_def.get('type') == 'array' and 'fields' in type_def:
                fields = type_def['fields']
                if not fields:
                    # element_types was already set by inline parse; do not overwrite
                    continue
                element_types = {}
                for idx, (field_name, field_info) in enumerate(fields.items()):
                    element_type = field_info.get('type', '')
                    if element_type.startswith('[') and not element_type.endswith(']'):
                        element_type = element_type + ' ]'
                    element_types[idx] = element_type
                    logger.debug(f"Array type '{type_name}' element {idx}: {field_name} -> {element_type}")
                type_def['element_types'] = element_types
    
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
                    
                    # Extract .size constraint if present
                    size_constraint = self.extract_size_constraint(value_type)
                    
                    # Store in current fields
                    field_info = {
                        'name': keyname,
                        'type': value_type,
                        'optional': optional,
                        'registered': True
                    }
                    
                    if size_constraint:
                        field_info['size_constraint'] = size_constraint
                        logger.debug(f"Field {keyname} has size constraint: {size_constraint}")
                    
                    current_fields[keyindex] = field_info
                except ValueError:
                    pass  # Skip if keyindex is not a number
        except (ValueError, IndexError):
            pass  # Skip malformed lines
    
    def resolve_type_alias(self, type_name: str, max_depth: int = 10) -> str:
        """Follow the alias chain for *type_name* and return the terminal type.

        Stops after *max_depth* hops to prevent infinite loops on circular
        definitions.  If the chain cannot be fully resolved within *max_depth*
        steps, the last successfully resolved name is returned.

        Args:
            type_name:  The name to resolve (e.g., ``"unsigned-corim-map"``).
            max_depth:  Maximum alias hops before giving up (default 10).

        Returns:
            The terminal type name after alias resolution, or *type_name*
            itself if it is not an alias.

        Example::

            cddl = CDDLParser("a = b\nb = tstr")
            cddl.resolve_type_alias("a")  # → "tstr"
        """
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
    
    def extract_cbor_control(self, type_string: str) -> Optional[Tuple[str, str]]:
        """Extract .cbor control operator from type string.
        
        E.g., 'bytes .cbor concise-mid-tag' -> ('bytes', 'concise-mid-tag')
              'bstr .cbor my-type' -> ('bstr', 'my-type')
        """
        # Match: (bytes|bstr) .cbor type-name
        match = re.match(r'(bytes?|bstr)\s+\.cbor\s+(.+)', type_string.strip())
        if match:
            base_type = match.group(1)
            inner_type = match.group(2).strip()
            logger.debug(f"Extracted .cbor control: {base_type} contains CBOR-encoded {inner_type}")
            return (base_type, inner_type)
        return None
    
    def extract_size_constraint(self, type_string: str) -> Optional[dict]:
        """Extract .size constraint from type string.
        
        Returns dict with:
        - 'min': minimum length (or None)
        - 'max': maximum length (or None)
        - 'exact': exact length (or None)
        
        Examples:
        - 'bytes .size 16' -> {'exact': 16}
        - 'text .size (8..64)' -> {'min': 8, 'max': 64}
        - 'bstr .size (16..)' -> {'min': 16, 'max': None}
        - 'tstr .size (..100)' -> {'min': None, 'max': 100}
        """
        # Match: .size N (exact)
        match = re.search(r'\.size\s+(\d+)(?!\.)' , type_string)
        if match:
            return {'exact': int(match.group(1)), 'min': None, 'max': None}
        
        # Match: .size (M..N) (range)
        match = re.search(r'\.size\s+\((\d+)\.\.(\d+)\)', type_string)
        if match:
            return {'exact': None, 'min': int(match.group(1)), 'max': int(match.group(2))}
        
        # Match: .size (M..) (at least M)
        match = re.search(r'\.size\s+\((\d+)\.\.\)', type_string)
        if match:
            return {'exact': None, 'min': int(match.group(1)), 'max': None}
        
        # Match: .size (..N) (at most N)
        match = re.search(r'\.size\s+\(\.\.(\d+)\)', type_string)
        if match:
            return {'exact': None, 'min': None, 'max': int(match.group(1))}
        
        return None
    
    def extract_value_range(self, type_string: str) -> Optional[dict]:
        """Extract numeric value-range predicates from a CDDL type string.

        Supports ``.ge``, ``.gt``, ``.le``, ``.lt`` (RFC 8610 §3.8.1).
        Multiple predicates on the same type string are all captured.

        Examples::

            'uint .le 150'          -> {'ge': None, 'gt': None, 'le': 150, 'lt': None}
            'uint .ge 0 .le 100'    -> {'ge': 0,    'gt': None, 'le': 100, 'lt': None}
            'int .gt -1 .lt 128'    -> {'ge': None, 'gt': -1,   'le': None, 'lt': 128}

        Returns ``None`` if no range predicates are found.
        """
        result = {'ge': None, 'gt': None, 'le': None, 'lt': None}
        found = False
        for op in ('ge', 'gt', 'le', 'lt'):
            m = re.search(rf'\.{op}\s+(-?\d+(?:\.\d+)?)', type_string)
            if m:
                # Use int if value has no decimal point, float otherwise
                raw = m.group(1)
                result[op] = float(raw) if '.' in raw else int(raw)
                found = True
        return result if found else None

    def extract_regexp(self, type_string: str) -> Optional[str]:
        """Extract a ``.regexp`` pattern from a CDDL type string.

        Example::

            'tstr .regexp "[a-z]+"' -> '[a-z]+'

        Returns the pattern string (without surrounding quotes), or ``None``.
        """
        m = re.search(r'\.regexp\s+"([^"]*)"', type_string)
        return m.group(1) if m else None

    def extract_cbor_tag(self, type_string: str) -> Optional[Tuple[int, str]]:
        """Extract CBOR tag number and inner type from tag notation.
        
        E.g., '#6.501(unsigned-corim-map)' -> (501, 'unsigned-corim-map')
        """
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
        
        # Check if cbor_data is a tagged value (tag_num, value)
        actual_tag = None
        actual_data = cbor_data
        if isinstance(cbor_data, tuple) and len(cbor_data) == 2 and isinstance(cbor_data[0], int):
            actual_tag = cbor_data[0]
            actual_data = cbor_data[1]
            logger.debug(f"  CBOR data is tagged: tag {actual_tag}")
        
        # Strategy 1: If we have a CBOR tag, try to match by tag number
        if actual_tag is not None:
            logger.debug(f"  Attempting tag-based matching for tag {actual_tag}")
            for alt in alternatives:
                # Get the alternative's type definition
                alt_type_name = self.resolve_type_alias(alt)
                
                # Extract tag number from the type definition
                tag_info = self.extract_cbor_tag(alt_type_name)
                if tag_info:
                    expected_tag, inner_type = tag_info
                    if expected_tag == actual_tag:
                        logger.debug(f"    {Colors.MATCH}[OK] Tag match:{Colors.RESET} {alt} expects tag {expected_tag}")
                        return alt
                    else:
                        logger.debug(f"    [X] Tag mismatch: {alt} expects tag {expected_tag}, got {actual_tag}")
        
        # Strategy 2: For each alternative, try to get its type and check basic compatibility
        compatible = []
        for alt in alternatives:
            logger.debug(f"  Checking alternative: {alt}")
            
            # Try to get the type definition for this alternative
            alt_type = self.get_type(alt, actual_data)
            if not alt_type:
                logger.debug(f"    [X] Cannot resolve type definition")
                continue
            
            # Check basic type compatibility (map vs array vs primitive)
            expected_type = alt_type['type']
            actual_type = type(actual_data).__name__
            
            if expected_type == 'map' and isinstance(actual_data, dict):
                logger.debug(f"    {Colors.MATCH}[OK]{Colors.RESET} Compatible: map matches dict")
                compatible.append(alt)
            elif expected_type == 'array' and isinstance(actual_data, (list, tuple)):
                logger.debug(f"    {Colors.MATCH}[OK]{Colors.RESET} Compatible: array matches list/tuple")
                compatible.append(alt)
            elif expected_type == 'bstr' and isinstance(actual_data, bytes):
                logger.debug(f"    {Colors.MATCH}[OK]{Colors.RESET} Compatible: bstr matches bytes")
                compatible.append(alt)
            else:
                logger.debug(f"    [X] Incompatible: {expected_type} vs {actual_type}")
        
        # If we have compatible alternatives, try validation-based matching if validator available
        if compatible and validator:
            logger.debug(f"  {len(compatible)} compatible alternatives, trying validation...")
            
            for alt in compatible:
                logger.debug(f"  Attempting validation with: {alt}")
                alt_type = self.get_type(alt, actual_data)
                
                # Try to validate against this alternative
                # Save current validation state
                saved_errors = validator.validation_errors.copy()
                saved_breadcrumb = validator.breadcrumb.copy()
                
                # Attempt validation
                try:
                    result = validator._validate_type(actual_data, alt_type, alt)
                    if result and len(validator.validation_errors) == len(saved_errors):
                        logger.debug(f"    {Colors.MATCH}[OK] VALIDATION SUCCESS{Colors.RESET}: Selected '{alt}'")
                        # Restore state and return
                        validator.validation_errors = saved_errors
                        validator.breadcrumb = saved_breadcrumb
                        return alt
                    else:
                        logger.debug(f"    [X] Validation failed")
                except Exception as e:
                    logger.debug(f"    [X] Validation error: {e}")
                
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
        
        # Try extracting .cbor control operator
        cbor_control = self.extract_cbor_control(type_name)
        if cbor_control:
            base_type, inner_type = cbor_control
            logger.debug(f"  Extracted .cbor control: {base_type} containing {inner_type}")
            # Return a synthetic type definition for bytes
            return {
                'type': 'bstr',
                'fields': {},
                'cbor_inner_type': inner_type  # Store inner type for validation
            }
        
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

    def __init__(self, cddl_parser: CDDLParser):
        """Initialise the analyzer with a parsed CDDL schema.

        Args:
            cddl_parser: A :class:`CDDLParser` instance holding the schema.
        """
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
            if major_type == MAJOR_TYPE_MAP and isinstance(data, dict):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
                    current_offset += 1
                elif additional_info == 25:
                    if current_offset + 1 < len(cbor_bytes):
                        _length = int.from_bytes(cbor_bytes[current_offset:current_offset+2], 'big')
                    current_offset += 2
                
                # Process each key-value pair
                for key, value in data.items():
                    # Process key
                    current_offset = self._skip_cbor_item(cbor_bytes, current_offset)
                    # Process value
                    if current_offset < len(cbor_bytes):
                        current_offset = self._build_offset_map(cbor_bytes, value, current_offset)
                
                return current_offset
            
            elif major_type == MAJOR_TYPE_ARRAY and isinstance(data, (list, tuple)):
                # Decode length (not currently used, but needed to advance offset)
                _length = additional_info
                if additional_info == 24:
                    _length = cbor_bytes[current_offset] if current_offset < len(cbor_bytes) else 0
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
            self.validation_errors.append(f"Type '{type_name}' not found in CDDL")
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
                        tagged ``(tag_num, value)`` tuple).
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
                            return self._validate_root(data, selected)
                        
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
        
        # Unwrap CBOR tagged data (tag_num, value) tuples
        cbor_tag = None
        if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], int):
            cbor_tag = data[0]
            data = data[1]
            logger.debug(f"{Colors.CBOR}[{breadcrumb}] Unwrapped CBOR tag {cbor_tag}{Colors.RESET}")
        
        logger.debug(f"{offset_str}{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating type '{type_name}'")
        logger.debug(f"  Expected: {type_def['type']}, Got: {type(data).__name__}")
        
        # Check if this is a .cbor control type (bytes containing CBOR)
        if type_def.get('cbor_inner_type'):
            inner_type = type_def['cbor_inner_type']
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Type has .cbor control: contains {inner_type}")
            
            if not isinstance(data, bytes):
                error_msg = f"Expected bytes (for .cbor control) for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
                return False
            
            # Decode the nested CBOR
            try:
                logger.debug(f"{Colors.CBOR}[{breadcrumb}]{Colors.RESET} Decoding nested CBOR ({len(data)} bytes)")
                nested_decoder = SimpleCBORDecoder(data)
                nested_data = nested_decoder.decode(f"{breadcrumb}:cbor")
                logger.debug(f"{Colors.MATCH}[{breadcrumb}]{Colors.RESET} Decoded nested CBOR successfully")
                
                # Validate the decoded data against the inner type
                nested_type_def = self.cddl.get_type(inner_type, nested_data)
                if nested_type_def:
                    logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Validating nested CBOR against: {inner_type}")
                    self._push_breadcrumb("cbor")
                    self._validate_type(nested_data, nested_type_def, inner_type)
                    self._pop_breadcrumb()
                else:
                    logger.warning(f"{Colors.WARNING}[{breadcrumb}]{Colors.RESET} Could not find type definition for: {inner_type}")
                    
                return len(self.validation_errors) == 0
                
            except Exception as e:
                error_msg = f"Failed to decode nested CBOR in type '{type_name}': {e}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
                return False
        
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
                    logger.debug(f"{Colors.MATCH}[{field_breadcrumb}] [OK]{Colors.RESET} Field present: " +
                               f"key={key}, type={field_type}, value={value_repr}{cbor_ctx}")
                    
                    # Basic type checking for primitives
                    type_mismatch = False
                    
                    # Check for .cbor control operator (bytes containing CBOR data)
                    cbor_control = self.cddl.extract_cbor_control(field_type) if field_type else None
                    if cbor_control:
                        base_type, inner_type = cbor_control
                        logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Field has .cbor control: {base_type} .cbor {inner_type}")
                        
                        # Verify it's bytes
                        if not isinstance(value, bytes):
                            type_mismatch = True
                            logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Expected bytes (for .cbor), got {type(value).__name__}")
                            self.validation_errors.append(f"Field '{field_name}' should be bytes containing CBOR data")
                        else:
                            # Decode the nested CBOR and validate it
                            try:
                                logger.debug(f"{Colors.CBOR}[{field_breadcrumb}]{Colors.RESET} Decoding nested CBOR ({len(value)} bytes)")
                                nested_decoder = SimpleCBORDecoder(value)
                                nested_data = nested_decoder.decode(f"{field_breadcrumb}:cbor")
                                logger.debug(f"{Colors.MATCH}[{field_breadcrumb}]{Colors.RESET} Decoded nested CBOR: {type(nested_data).__name__}")
                                
                                # Validate the decoded data against the inner type
                                nested_type_def = self.cddl.get_type(inner_type, nested_data)
                                if nested_type_def:
                                    logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Validating nested CBOR against: {inner_type}")
                                    self._validate_type(nested_data, nested_type_def, inner_type)
                                else:
                                    logger.warning(f"{Colors.WARNING}[{field_breadcrumb}]{Colors.RESET} Could not find type definition for: {inner_type}")
                            except Exception as e:
                                logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Failed to decode nested CBOR: {e}")
                                self.validation_errors.append(f"Failed to decode nested CBOR in field '{field_name}': {e}")
                    
                    # field_type may contain user alias + annotation (e.g. 'my-text .size 16')
                    # Resolve alias first, then extract base type.
                    _resolved = self.cddl.resolve_type_alias(field_type) if field_type else field_type
                    _base_type = re.split(r'[\s.]', _resolved)[0] if _resolved else ''
                    # Normalize CDDL built-in aliases to their canonical base types
                    _alias_map = {
                        'text': 'tstr', 'bytes': 'bstr', 'true': 'bool', 'false': 'bool',
                        'nint': 'int', 'float16': 'float', 'float32': 'float', 'float64': 'float',
                        'nil': 'null', 'undefined': 'null'
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
                        else:
                            # Check .ge / .gt / .le / .lt value-range predicates
                            vrange = self.cddl.extract_value_range(_resolved)
                            if vrange:
                                if vrange['ge'] is not None and value < vrange['ge']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} < .ge {vrange['ge']}")
                                elif vrange['gt'] is not None and value <= vrange['gt']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} not > .gt {vrange['gt']}")
                                elif vrange['le'] is not None and value > vrange['le']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Value {value} > .le {vrange['le']}")
                                elif vrange['lt'] is not None and value >= vrange['lt']:
                                    type_mismatch = True
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
                    elif field_type == 'tstr' or _base_type == 'tstr':
                        if not isinstance(value, str):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected tstr, got {type(value).__name__}")
                        else:
                            # Check size constraint from inline annotation or resolved alias
                            size = field_info.get('size_constraint') or self.cddl.extract_size_constraint(_resolved)
                            if size:
                                length = len(value)
                                if size.get('exact') is not None and length != size['exact']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected exactly {size['exact']}, got {length}")
                                elif size.get('min') is not None and length < size['min']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected at least {size['min']}, got {length}")
                                elif size.get('max') is not None and length > size['max']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected at most {size['max']}, got {length}")
                            # Check .regexp constraint
                            pattern = self.cddl.extract_regexp(_resolved)
                            if pattern and not type_mismatch:
                                if not re.fullmatch(pattern, value):
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Regexp mismatch: {value!r} does not match /{pattern}/")
                    elif field_type == 'bstr' or _base_type == 'bstr':
                        if not isinstance(value, bytes):
                            type_mismatch = True
                            logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Type mismatch: expected bstr, got {type(value).__name__}")
                        else:
                            # Check size constraint from inline annotation or resolved alias
                            size = field_info.get('size_constraint') or self.cddl.extract_size_constraint(_resolved)
                            if size:
                                length = len(value)
                                if size.get('exact') is not None and length != size['exact']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected exactly {size['exact']} bytes, got {length}")
                                elif size.get('min') is not None and length < size['min']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected at least {size['min']} bytes, got {length}")
                                elif size.get('max') is not None and length > size['max']:
                                    type_mismatch = True
                                    logger.debug(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Size mismatch: expected at most {size['max']} bytes, got {length}")

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
                        # Check if field_type is an inline array definition: [ + type ] or [ * type ]
                        array_match = re.match(r'^\[\s*([+*]?)\s*(.+?)\s*\]$', field_type)
                        if array_match:
                            # It's an inline array definition
                            quantifier = array_match.group(1)  # + or * or empty
                            element_type = array_match.group(2).strip()
                            
                            logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Field is inline array: [{quantifier} {element_type}]")
                            
                            if not isinstance(value, (list, tuple)):
                                logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Expected array, got {type(value).__name__}")
                                self.validation_errors.append(f"Expected array for field '{field_name}', got {type(value).__name__}")
                            else:
                                # Validate array constraints
                                if quantifier == '+' and len(value) == 0:
                                    logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}]{Colors.RESET} Array must have at least one element")
                                    self.validation_errors.append(f"Array field '{field_name}' must not be empty")
                                
                                # Validate each array element
                                logger.debug(f"{Colors.CDDL}[{field_breadcrumb}]{Colors.RESET} Validating {len(value)} array elements")
                                for i, item in enumerate(value):
                                    self._push_breadcrumb(f"[{i}]")
                                    item_breadcrumb = self._get_breadcrumb()
                                    item_repr = self._format_value_for_log(item)
                                    logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")
                                    
                                    # Check if element type is a type choice
                                    if element_type.startswith('$'):
                                        logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element type is a choice: {element_type}")
                                        selected_type = self.cddl.resolve_type_choice_for_data(element_type, item, validator=self)
                                        if selected_type:
                                            logger.debug(f"{Colors.MATCH}[{item_breadcrumb}]{Colors.RESET} Resolved choice to: {selected_type}")
                                            nested_type_def = self.cddl.get_type(selected_type)
                                            if nested_type_def:
                                                self._validate_type(item, nested_type_def, selected_type)
                                        else:
                                            logger.warning(f"{Colors.WARNING}[{item_breadcrumb}]{Colors.RESET} Could not resolve type choice: {element_type}")
                                    else:
                                        # Regular element type
                                        nested_type_def = self.cddl.get_type(element_type)
                                        if nested_type_def:
                                            self._validate_type(item, nested_type_def, element_type)
                                    
                                    self._pop_breadcrumb()
                        
                        # Check if field_type is a type choice
                        elif field_type.startswith('$'):
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
                    logger.error(f"{Colors.MISMATCH}[{field_breadcrumb}] [X] MISSING:{Colors.RESET} {error_msg}")
                    self.validation_errors.append(error_msg)
                else:
                    logger.debug(f"{Colors.DEBUG}[{field_breadcrumb}] [.]{Colors.RESET} Optional field not present")
                
                self._pop_breadcrumb()
            
            # Report extra fields
            defined_keys = set(type_def['fields'].keys())
            actual_keys = set(data.keys())
            extra_keys = actual_keys - defined_keys
            if extra_keys:
                error_msg = f"Unknown fields not in schema for type '{type_name}': {extra_keys}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
        
        elif type_def['type'] == 'array':
            if not isinstance(data, (list, tuple)):
                error_msg = f"Expected array for type '{type_name}', got {type(data).__name__}"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}] TYPE MISMATCH:{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)
                return False
            
            logger.debug(f"{Colors.CDDL}[{breadcrumb}]{Colors.RESET} Array has {len(data)} elements")
            
            # Enforce + occurrence (at-least-one) for the top-level array type
            occurrence = type_def.get('occurrence', '')
            if occurrence == '+' and len(data) == 0:
                error_msg = f"Array type '{type_name}' requires at least one element (+ occurrence)"
                logger.error(f"{Colors.MISMATCH}[{breadcrumb}]{Colors.RESET} {error_msg}")
                self.validation_errors.append(error_msg)

            # Validate element types
            element_types = type_def.get('element_types', {})
            # Single repeating element type uses index 0 as the pattern
            repeating_type = element_types.get(0) if element_types else None

            for i, item in enumerate(data):
                self._push_breadcrumb(f"[{i}]")
                item_breadcrumb = self._get_breadcrumb()
                item_repr = self._format_value_for_log(item)
                logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Element: {item_repr}")

                # Check element type: per-index pattern takes precedence over repeating
                elem_type = element_types.get(i) if i in element_types else repeating_type
                if elem_type:
                    # Resolve user alias first
                    resolved_elem = self.cddl.resolve_type_alias(elem_type) if elem_type else elem_type
                    # Extract base type and any .size constraint
                    base_elem_type = re.split(r'[\s.]', resolved_elem)[0] if resolved_elem else ''
                    size_constraint = self.cddl.extract_size_constraint(resolved_elem)

                    # Check primitive type first
                    elem_valid = self._check_primitive_type(item, base_elem_type)
                    if elem_valid is False:
                        error_msg = (
                            f"Array element [{i}] of '{type_name}' has wrong type: "
                            f"expected {base_elem_type}, got {type(item).__name__}"
                        )
                        logger.error(f"{Colors.MISMATCH}[{item_breadcrumb}]{Colors.RESET} {error_msg}")
                        self.validation_errors.append(error_msg)
                    # Enforce size constraint if primitive check passed
                    elif elem_valid is True and size_constraint:
                        if base_elem_type in ('tstr', 'bstr') and isinstance(item, (str, bytes)):
                            length = len(item)
                            size_ok = True
                            if size_constraint.get('exact') is not None:
                                size_ok = (length == size_constraint['exact'])
                            elif size_constraint.get('min') is not None and length < size_constraint['min']:
                                size_ok = False
                            elif size_constraint.get('max') is not None and length > size_constraint['max']:
                                size_ok = False
                            if not size_ok:
                                error_msg = (
                                    f"Array element [{i}] violates size constraint: "
                                    f"expected {size_constraint}, got length {length}"
                                )
                                logger.error(f"{Colors.MISMATCH}[{item_breadcrumb}]{Colors.RESET} {error_msg}")
                                self.validation_errors.append(error_msg)
                    # elem_valid is None → structured type (map, array, choice)
                    elif elem_valid is None:
                        nested_type_def = self.cddl.get_type(base_elem_type, item)
                        if nested_type_def:
                            logger.debug(f"{Colors.CDDL}[{item_breadcrumb}]{Colors.RESET} Recursing into nested type: {base_elem_type}")
                            self._validate_type(item, nested_type_def, base_elem_type)
                        else:
                            logger.warning(f"{Colors.WARNING}[{item_breadcrumb}]{Colors.RESET} Unknown element type: {base_elem_type}")

                self._pop_breadcrumb()
        
        return len(self.validation_errors) == 0
    
    def _check_primitive_type(self, value, type_name):
        """Check whether value matches the named CDDL primitive type.

        Returns True if compatible, False if definitely incompatible, and None
        if type_name is not a recognised primitive (caller should recurse).

        Args:
            value:     The Python value decoded from CBOR.
            type_name: A CDDL primitive name such as 'uint', 'tstr', 'bool',
                       'bstr', 'float', 'int', 'nil', or 'null'.
        """
        resolved = self.cddl.resolve_type_alias(type_name)
        # Strip any CDDL annotations (.size, etc.) to get just the base type
        t = re.split(r'[\s.]', resolved)[0] if resolved else type_name
        if t == 'uint':
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            return value >= 0
        if t == 'int':
            return isinstance(value, int) and not isinstance(value, bool)
        if t == 'bool':
            return isinstance(value, bool)
        if t in ('nil', 'null'):
            return value is None
        if t == 'float':
            return isinstance(value, float)
        if t == 'tstr':
            return isinstance(value, str)
        if t == 'bstr':
            return isinstance(value, bytes)
        if t == 'any':
            return True
        return None

    def _format_value_for_log(self, value, max_len=50):
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
            return str(value)
    def get_errors(self) -> List[str]:
        """Return the list of validation error messages from the last :meth:`validate` call.

        Returns an empty list when the last call succeeded.  The list is
        reset at the start of every :meth:`validate` call.
        """
        return self.validation_errors


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
                        or a ``(tag_num, value)`` tuple for tagged items.
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
            return self._generate_value(data, type_name, annotate)
        finally:
            self.indent_level = 0  # always reset, even when an exception propagates
    
    def _generate_value(self, value: Any, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for a value."""
        logger.debug(f"EDN: _generate_value called with type_name='{type_name}'")
        
        # Resolve type choice to actual matched type
        if type_name and type_name.startswith('$'):
            # It's a type choice - resolve to the actual type that matches the data
            resolved = self.cddl.resolve_type_choice_for_data(type_name, value)
            if resolved:
                logger.debug(f"EDN: Resolved type choice {type_name} -> {resolved}")
                type_name = resolved
        elif type_name:
            # Check if this type is an alias and resolve to final type
            resolved = self.cddl.resolve_type_alias(type_name)
            logger.debug(f"EDN: Checked alias for '{type_name}', got '{resolved}'")
            if resolved != type_name and resolved in self.cddl.type_choices:
                # It's an alias to a choice - resolve the choice
                final_resolved = self.cddl.resolve_type_choice_for_data(resolved, value)
                if final_resolved:
                    logger.debug(f"EDN: Resolved alias+choice {type_name} -> {resolved} -> {final_resolved}")
                    type_name = final_resolved
        
        # Handle CBOR tagged tuples (tag_num, value)
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
            tag_num = value[0]
            inner_value = value[1]
            
            # Determine the actual tag type name and inner type name
            # If type_name is a tagged type definition, extract the inner type
            tag_type_name = type_name
            inner_type_name = type_name
            
            if type_name:
                # Check if type_name is itself a tag definition (e.g., "tagged-unsigned-corim-map")
                # We should look up its definition to find the inner type
                type_def = self.cddl.get_type(type_name)
                
                # Try to extract inner type from tag notation in aliases
                # e.g., tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
                if type_name in self.cddl.type_aliases:
                    alias_value = self.cddl.type_aliases[type_name]
                    tag_info = self.cddl.extract_cbor_tag(alias_value)
                    if tag_info:
                        extracted_tag_num, extracted_inner = tag_info
                        if extracted_tag_num == tag_num:
                            # This is the right tag type
                            inner_type_name = extracted_inner
                            logger.debug(f"EDN: Tag {tag_num}: outer type='{tag_type_name}', inner type='{inner_type_name}'")
                            
                            # Further resolve the inner type if it's an alias
                            resolved_inner = self.cddl.resolve_type_alias(inner_type_name)
                            if resolved_inner != inner_type_name:
                                logger.debug(f"EDN: Resolved inner type alias {inner_type_name} -> {resolved_inner}")
                                inner_type_name = resolved_inner
            
            # Check if this type has .cbor control (nested CBOR in bytes)
            if type_name:
                type_def = self.cddl.get_type(type_name, inner_value)
                if type_def and type_def.get('cbor_inner_type'):
                    # Decode nested CBOR and generate EDN for it
                    inner_type = type_def['cbor_inner_type']
                    if isinstance(inner_value, bytes):
                        try:
                            logger.debug(f"EDN: Decoding nested CBOR ({len(inner_value)} bytes) for type {inner_type}")
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
                            if annotate and tag_type_name:
                                # Add type annotation comment showing the tagged type
                                tag_comment = f"/ {tag_type_name} / "
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
                if annotate and tag_type_name:
                    tag_annotation = f"/ {tag_type_name} / "
                    result = f"{tag_annotation}{tag_num}(\n"
                else:
                    result = f"{tag_num}(\n"
                
                result += '\n'.join(indented_lines)
                result += f"\n{tag_indent})"
                return result
            else:
                # Single-line content
                if annotate and tag_type_name:
                    tag_annotation = f"/ {tag_type_name} / "
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
    
    def _generate_map(self, data: Dict, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for a map/object."""
        if not data:
            return "{}"
        
        type_def = self.cddl.get_type(type_name) if type_name else None
        
        # Add type name header if we have one and annotations are enabled
        # Format: / type / {
        type_header = ""
        if type_name and annotate and type_name not in ['map', 'dict']:
            type_header = f"/ {type_name} / "
        
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
                if isinstance(key, str):
                    key_str = self._edn_str(key)
                else:
                    key_str = str(key)
                key_str = f'{key_str} / {field_name} /'
                annotation = ""
            else:  # keyindex format (default)
                # Format: / name / 0: value
                if isinstance(key, str):
                    key_str = self._edn_str(key)
                else:
                    key_str = str(key)
                
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
    
    def _generate_array(self, data: List, type_name: str = None, annotate: bool = True) -> str:
        """Generate EDN for an array."""
        if not data:
            return "[]"
        
        # Check if type_name is an inline array definition: [ + type ] or [ * type ]
        element_type = None
        element_types_by_index = {}  # For structured arrays like [type1, type2]
        
        if type_name:
            # Try inline array: [ + type ] or [ * type ]
            array_match = re.match(r'^\[\s*([+*]?)\s*(.+?)\s*\]$', type_name)
            if array_match:
                element_type = array_match.group(2).strip()
                logger.debug(f"EDN: Inline array type detected, element type: {element_type}")
            else:
                # Try to get array type definition for structured arrays
                # e.g., reference-triple-record = [environment-map, [ + measurement-map ]]
                logger.debug(f"EDN: Checking if '{type_name}' is a structured array type")
                type_def = self.cddl.get_type(type_name)
                if type_def and type_def.get('type') == 'array':
                    # Check if it has element_types (structured array)
                    if 'element_types' in type_def and type_def['element_types']:
                        element_types_by_index = type_def.get('element_types', {})
                        logger.debug(f"EDN: Structured array type '{type_name}' with element types: {element_types_by_index}")
                    else:
                        logger.debug(f"EDN: Array type '{type_name}' has no element_types")
                else:
                    logger.debug(f"EDN: Type '{type_name}' is not an array type (type={type_def.get('type') if type_def else 'not found'})")
        
        # Add type name header if we have a structured array type and annotations are enabled
        # Format: / type / [
        type_header = ""
        if type_name and annotate and not type_name.startswith('['):
            # It's a named array type (not inline array syntax)
            type_header = f"/ {type_name} / "
        
        lines = [type_header + "["]
        self.indent_level += 1
        
        for i, value in enumerate(data):
            indent = self.indent_str * self.indent_level
            
            # Determine the type for this specific element
            resolved_element_type = None
            
            # First check if we have a type for this specific index (structured arrays)
            if element_types_by_index and i in element_types_by_index:
                resolved_element_type = element_types_by_index[i]
                logger.debug(f"EDN: Array element [{i}] has indexed type: {resolved_element_type}")
            elif element_type:
                # For uniform arrays, all elements have the same type
                resolved_element_type = element_type
                logger.debug(f"EDN: Array element [{i}] using uniform type: {resolved_element_type}")
            
            # For inline arrays with type choices, resolve the type for each element
            if resolved_element_type and resolved_element_type.startswith('$'):
                # It's a type choice - need to resolve it
                # Unwrap tagged tuple for type resolution
                resolution_data = value
                if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
                    resolution_data = value  # Keep tuple for tag matching
                
                # Try to resolve the type choice based on the actual data
                resolved = self.cddl.resolve_type_choice_for_data(resolved_element_type, resolution_data)
                if resolved:
                    resolved_element_type = resolved
                    logger.debug(f"EDN: Resolved array element [{i}] type choice to: {resolved}")
            elif resolved_element_type:
                # Check if this type is itself a structured array
                elem_type_def = self.cddl.get_type(resolved_element_type)
                if elem_type_def and elem_type_def.get('type') == 'array' and isinstance(value, (list, tuple)):
                    # It's a structured array - pass the type so it can use element_types
                    logger.debug(f"EDN: Array element [{i}] type '{resolved_element_type}' is a structured array")
                    # The type name stays as-is, _generate_value will handle it
                elif resolved_element_type:
                    # Not a type choice, but might be an alias - try to resolve
                    resolved_alias = self.cddl.resolve_type_alias(resolved_element_type)
                    if resolved_alias != resolved_element_type:
                        logger.debug(f"EDN: Resolved array element [{i}] type alias {resolved_element_type} -> {resolved_alias}")
                        resolved_element_type = resolved_alias
                    else:
                        # Check if we can get a type definition directly
                        # This handles cases like 'comid-entity-map' which isn't an alias but 'entity-map' exists
                        type_def = self.cddl.get_type(resolved_element_type)
                        if not type_def and '-' in resolved_element_type:
                            # Try variants like removing prefix
                            base_type = resolved_element_type.split('-', 1)[1] if resolved_element_type.count('-') > 0 else resolved_element_type
                            if base_type != resolved_element_type:
                                alt_type_def = self.cddl.get_type(base_type)
                                if alt_type_def:
                                    logger.debug(f"EDN: Using base type {base_type} for {resolved_element_type}")
                                    resolved_element_type = base_type
            
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


def load_cddl(filepath: Path) -> CDDLParser:
    """Load a CDDL schema file from *filepath* and return a :class:`CDDLParser`.

    Args:
        filepath: Path to the ``.cddl`` file.

    Returns:
        A fully parsed :class:`CDDLParser` instance ready for validation or
        EDN generation.

    Exits:
        Calls ``sys.exit(1)`` on any I/O or parse error (CLI helper).
    """
    try:
        content = filepath.read_text(encoding='utf-8')
        return CDDLParser(content)
    except Exception as e:
        print(f"Error loading CDDL file: {e}")
        sys.exit(1)


def load_cbor(filepath: Path) -> Any:
    """Load and decode a CBOR binary file from *filepath*.

    Tries ``cbor2.load`` first; falls back to the bundled
    :class:`SimpleCBORDecoder` if ``cbor2`` is not installed.

    Args:
        filepath: Path to the ``.cbor`` binary file.

    Returns:
        The decoded Python object (``dict``, ``list``, scalar, etc.).

    Exits:
        Calls ``sys.exit(1)`` on any I/O or decode error (CLI helper).
    """
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
  # Decode CBOR and display annotated EDN (stdout)
  %(prog)s schema.cddl data.cbor

  # Validate CBOR against a named type and display annotated EDN
  %(prog)s schema.cddl data.cbor --type corim-map

  # Write annotated EDN to a file
  %(prog)s schema.cddl data.cbor --type corim-map --output data.edn

  # Suppress field-name annotations
  %(prog)s schema.cddl data.cbor --no-annotate

  # Use human-readable key names instead of integer indices
  %(prog)s schema.cddl data.cbor --edn-format keyname
        """
    )
    
    parser.add_argument('cddl_file', type=Path, help='Path to CDDL schema file')
    parser.add_argument('cbor_file', type=Path, help='Path to CBOR data file')
    parser.add_argument('-o', '--output', type=Path, help='Output EDN file (default: stdout)')
    parser.add_argument('-t', '--type', help='Root type name from CDDL for validation')
    # Annotations are on by default.  Use --no-annotate to suppress field-name comments.
    # The old -a/--annotate flag was removed: `action='store_true', default=True` meant
    # passing -a never changed anything — the flag was always True either way.
    parser.set_defaults(annotate=True)
    parser.add_argument('--no-annotate', action='store_false', dest='annotate',
                        help='Suppress field-name comments in EDN output')
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
    
    # Load CBOR data — single read, then decode in-memory (avoids opening the file twice)
    print(f"Loading CBOR data: {args.cbor_file}", file=sys.stderr)
    cbor_bytes = args.cbor_file.read_bytes()
    try:
        cbor_data = CBOR.loads(cbor_bytes)
    except Exception as _e:  # noqa: BLE001 — catch-all needed: loader raises ValueError/NotImplementedError/UnicodeDecodeError depending on input
        print(f"Error decoding CBOR: {_e}", file=sys.stderr)
        sys.exit(1)
    
    # Validate if type is specified
    if args.type:
        print(f"Validating CBOR against CDDL (type: {args.type})...", file=sys.stderr)
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