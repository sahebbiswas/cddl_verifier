#!/usr/bin/env python3
"""
CBOR-CDDL Analyzer and EDN Generator (Standalone Version)

This version includes basic CBOR decoding without external dependencies.
For full functionality, install cbor2: pip install cbor2
"""

import argparse
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class SimpleCBORDecoder:
    """Simple CBOR decoder for basic data types."""
    
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
    
    def decode(self) -> Any:
        """Decode CBOR data."""
        if self.pos >= len(self.data):
            raise ValueError("Unexpected end of data")
        
        initial_byte = self.data[self.pos]
        self.pos += 1
        
        major_type = initial_byte >> 5
        additional_info = initial_byte & 0x1F
        
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
            return [self.decode() for _ in range(length)]
        elif major_type == 5:  # map
            length = self._decode_int(additional_info)
            result = {}
            for _ in range(length):
                key = self.decode()
                value = self.decode()
                result[key] = value
            return result
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
        self.registered_params: Dict[int, str] = {}  # Maps keyindex to keyname
        self.parse()
    
    def parse(self):
        """Parse CDDL content to extract type definitions."""
        lines = self.content.split('\n')
        current_type = None
        current_fields = {}
        in_group = False
        current_group_name = None
        current_group_fields = []
        
        for line in lines:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith(';'):
                continue
            
            # Normalize whitespace for various checks
            line_normalized = line.replace('& (', '&(').replace('&  (', '&(').replace('&   (', '&(')
            line_normalized = line_normalized.replace(') =>', ')=>').replace(')  =>', ')=>').replace(')   =>', ')=>')
            
            # Handle type choice additions ($name /= value)
            if '/=' in line:
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
            elif '=' in line and '[' in line and '/=' not in line:
                type_name = line.split('=')[0].strip()
                current_type = type_name
                current_fields = {}
                self.types[type_name] = {'fields': current_fields, 'type': 'array'}
            
            # Field definition (e.g., "name: tstr" or "0: tstr" or "0 : tstr")
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
    
    def get_type(self, type_name: str) -> Optional[Dict]:
        """Get type definition by name."""
        return self.types.get(type_name)
    
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


class CBORAnalyzer:
    """Analyzes CBOR data against CDDL schema."""
    
    def __init__(self, cddl_parser: CDDLParser):
        self.cddl = cddl_parser
        self.validation_errors: List[str] = []
    
    def validate(self, data: Any, type_name: str = None) -> bool:
        """Validate CBOR data against CDDL schema."""
        self.validation_errors = []
        
        if type_name:
            type_def = self.cddl.get_type(type_name)
            if not type_def:
                self.validation_errors.append(f"Type '{type_name}' not found in CDDL")
                return False
            
            return self._validate_type(data, type_def, type_name)
        
        return True
    
    def _validate_type(self, data: Any, type_def: Dict, type_name: str) -> bool:
        """Validate data against a specific type definition."""
        if type_def['type'] == 'map':
            if not isinstance(data, dict):
                self.validation_errors.append(
                    f"Expected map for type '{type_name}', got {type(data).__name__}"
                )
                return False
            
            # Check required fields
            for key, field_info in type_def['fields'].items():
                if not field_info.get('optional', False) and key not in data:
                    self.validation_errors.append(
                        f"Missing required field '{field_info['name']}' in type '{type_name}'"
                    )
        
        elif type_def['type'] == 'array':
            if not isinstance(data, (list, tuple)):
                self.validation_errors.append(
                    f"Expected array for type '{type_name}', got {type(data).__name__}"
                )
                return False
        
        return len(self.validation_errors) == 0
    
    def get_errors(self) -> List[str]:
        """Get validation errors."""
        return self.validation_errors


class EDNGenerator:
    """Generates annotated EDN (Extended Diagnostic Notation) from CBOR data."""
    
    def __init__(self, cddl_parser: CDDLParser):
        self.cddl = cddl_parser
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
            is_registered = False
            if type_def and annotate:
                field_info = type_def['fields'].get(key)
                if field_info:
                    field_name = field_info['name']
                    is_registered = field_info.get('registered', False)
            
            # For registered parameters, use keyname as the key in EDN
            if is_registered and field_name:
                key_str = f'"{field_name}"'
                annotation = ""  # No annotation needed since key is already the name
            else:
                # Format key normally
                if isinstance(key, str):
                    key_str = f'"{key}"'
                else:
                    key_str = str(key)
                
                # Add annotation comment if we have a field name different from key
                annotation = ""
                if field_name and field_name != str(key):
                    annotation = f"  / {field_name} /"
            
            value_str = self._generate_value(value, None, annotate)
            
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
    parser.add_argument('--show-types', action='store_true', 
                        help='Show parsed CDDL types and exit')
    
    args = parser.parse_args()
    
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
    cbor_data = load_cbor(args.cbor_file)
    
    # Validate if requested
    if args.validate:
        print("Validating CBOR against CDDL...", file=sys.stderr)
        analyzer = CBORAnalyzer(cddl)
        
        if analyzer.validate(cbor_data, args.type):
            print("[OK] Validation successful", file=sys.stderr)
        else:
            print("[FAIL] Validation failed:", file=sys.stderr)
            for error in analyzer.get_errors():
                print(f"  - {error}", file=sys.stderr)
            sys.exit(1)
    
    # Generate EDN
    print("Generating EDN...", file=sys.stderr)
    generator = EDNGenerator(cddl)
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
