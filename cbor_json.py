#!/usr/bin/env python3
"""
JSON ↔ CBOR Conversion Module

Bidirectional conversion between JSON and CBOR with type preservation.

Features:
- Convert JSON to CBOR
- Convert CBOR to JSON
- Preserve CBOR-specific types (bytes, tags)
- Handle special values (NaN, Infinity, -Infinity)
- Pretty-printing support

Usage:
    # JSON to CBOR
    cbor_bytes = json_to_cbor('{"name": "test", "id": 42}')
    
    # CBOR to JSON
    json_str = cbor_to_json(cbor_bytes)
    
    # With type preservation
    json_str = cbor_to_json(cbor_bytes, typed=True)
"""

import json
import base64
import math
from typing import Any, Dict, List, Union
from simple_cbor import CBOR, cbor_encode, cbor_decode


class CBORJSONEncoder(json.JSONEncoder):
    """
    JSON encoder that handles CBOR-specific types.
    
    Encodes:
    - bytes as base64 strings with type annotation
    - Tagged values as objects with $cbor and $tag fields
    - Special floats (NaN, Infinity)
    """
    
    def __init__(self, *args, typed: bool = False, **kwargs):
        """
        Create a JSON encoder that optionally preserves CBOR-specific types as annotated JSON.
        
        Parameters:
            *args, **kwargs: Passed through to the base JSONEncoder initializer.
            typed (bool): If True, encode CBOR-specific values (bytes, tags, NaN, Infinity, -Infinity) using explicit `$cbor` annotations; if False, emit JSON-friendly representations without type annotations.
        """
        super().__init__(*args, **kwargs)
        self.typed = typed
    
    def default(self, obj):
        """
        Serialize CBOR-specific Python objects into JSON-friendly representations.
        
        Handles:
        - bytes: when `self.typed` is True, returns an object with `"$cbor": "bytes"` and `"$value"` containing the base64 string; otherwise returns a base64 string.
        - CBOR tags represented as two-element tuples `(tag_num, value)`: when `self.typed` is True, returns an object with `"$cbor": "tag"`, `"$tag"`, and `"$value"`; otherwise returns the inner `value`.
        - Special float values: `NaN`, `Infinity`, and `-Infinity` are represented with typed annotations when `self.typed` is True; otherwise `NaN` becomes `None`, and infinities become the strings `"Infinity"` or `"-Infinity"`.
        
        Parameters:
            obj: The object to serialize; may be bytes, a `(tag_num, value)` tuple, a float (including NaN/Infinity), or any other object handled by the base encoder.
        
        Returns:
            A JSON-serializable representation of `obj` according to the rules above, or delegates to the superclass for other types.
        """
        # Handle bytes
        if isinstance(obj, bytes):
            if self.typed:
                return {
                    "$cbor": "bytes",
                    "$value": base64.b64encode(obj).decode('ascii')
                }
            else:
                # Without typing, use base64 string directly
                return base64.b64encode(obj).decode('ascii')
        
        # Handle tagged values (tuple of tag_num, value)
        # Check this BEFORE general tuple handling
        if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], int) and obj[0] >= 0:
            tag_num, value = obj
            if self.typed:
                return {
                    "$cbor": "tag",
                    "$tag": tag_num,
                    "$value": value
                }
            else:
                # Without typing, just return the value
                return value
        
        # Handle special floats
        if isinstance(obj, float):
            if math.isnan(obj):
                return {"$cbor": "NaN"} if self.typed else None
            elif math.isinf(obj):
                if obj > 0:
                    return {"$cbor": "Infinity"} if self.typed else "Infinity"
                else:
                    return {"$cbor": "-Infinity"} if self.typed else "-Infinity"
        
        return super().default(obj)


def cbor_to_json(cbor_bytes: bytes, typed: bool = False, pretty: bool = False, 
                 indent: int = 2) -> str:
    """
                 Convert CBOR-encoded bytes into a JSON string.
                 
                 Parameters:
                     cbor_bytes (bytes): CBOR-encoded input.
                     typed (bool): If True, preserve CBOR-native types using JSON annotations (e.g., bytes, tags, special floats).
                     pretty (bool): If True, produce human-readable JSON with indentation and sorted keys.
                     indent (int): Number of spaces to use for indentation when pretty is True.
                 
                 Returns:
                     json_str (str): JSON representation of the input CBOR data.
                 """
    # Decode CBOR
    data = cbor_decode(cbor_bytes)
    
    # Pre-process to handle CBOR-specific types
    processed = _preprocess_for_json(data, typed)
    
    # Convert to JSON
    if pretty:
        return json.dumps(processed, indent=indent, sort_keys=True, ensure_ascii=False)
    else:
        return json.dumps(processed, ensure_ascii=False)


def _preprocess_for_json(obj: Any, typed: bool) -> Any:
    """
    Convert a CBOR-native Python object into a JSON-compatible representation.
    
    This performs recursive transformation of CBOR-specific constructs:
    - bytes are encoded as base64 strings or as {"$cbor": "bytes", "$value": "<base64>"} when `typed` is True.
    - CBOR tags represented as two-element tuples (tag_num, value) are replaced by the inner value or by
      {"$cbor": "tag", "$tag": tag_num, "$value": ...} when `typed` is True.
    - Special float values are converted to annotations when `typed` is True:
      - NaN -> {"$cbor": "NaN"} (otherwise `None`)
      - Infinity -> {"$cbor": "Infinity"} (otherwise "Infinity")
      - -Infinity -> {"$cbor": "-Infinity"} (otherwise "-Infinity")
    - dicts and lists are processed recursively; other scalar types are returned unchanged.
    
    Parameters:
        obj (Any): A Python value decoded from CBOR (may include bytes, (tag_num, value) tuples, floats, dicts, lists, and scalars).
        typed (bool): If True, preserve CBOR-specific types using explicit annotation objects; if False, convert to plain JSON-friendly values.
    
    Returns:
        Any: A JSON-serializable Python structure representing the input with CBOR-specific constructs converted as described above.
    """
    # Handle bytes
    if isinstance(obj, bytes):
        if typed:
            return {
                "$cbor": "bytes",
                "$value": base64.b64encode(obj).decode('ascii')
            }
        else:
            return base64.b64encode(obj).decode('ascii')
    
    # Handle tagged values (tag_num, value) tuples
    if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], int) and obj[0] >= 0:
        tag_num, value = obj
        processed_value = _preprocess_for_json(value, typed)
        if typed:
            return {
                "$cbor": "tag",
                "$tag": tag_num,
                "$value": processed_value
            }
        else:
            return processed_value
    
    # Handle special floats
    if isinstance(obj, float):
        if math.isnan(obj):
            return {"$cbor": "NaN"} if typed else None
        elif math.isinf(obj):
            if obj > 0:
                return {"$cbor": "Infinity"} if typed else "Infinity"
            else:
                return {"$cbor": "-Infinity"} if typed else "-Infinity"
    
    # Handle dicts recursively
    if isinstance(obj, dict):
        return {k: _preprocess_for_json(v, typed) for k, v in obj.items()}
    
    # Handle lists recursively
    if isinstance(obj, list):
        return [_preprocess_for_json(item, typed) for item in obj]
    
    # Return as-is for basic types
    return obj


def json_to_cbor(json_str: str, canonical: bool = False) -> bytes:
    """
    Convert a JSON string (optionally containing CBOR annotations) into CBOR-encoded bytes.
    
    Recognizes typed CBOR annotations in objects with a "$cbor" key (for example, bytes via {"$cbor": "bytes", "$value": "<base64>"} and tags via {"$cbor": "tag", "$tag": N, "$value": V}) and converts them to native CBOR types before encoding.
    
    Parameters:
        json_str (str): JSON text to convert.
        canonical (bool): If True, produce canonical CBOR encoding (deterministic ordering and encoding rules).
    
    Returns:
        bytes: CBOR-encoded representation of the parsed and processed JSON data.
    """
    # Parse JSON
    data = json.loads(json_str)
    
    # Process typed annotations
    processed = _process_cbor_annotations(data)
    
    # Encode to CBOR
    return cbor_encode(processed, canonical=canonical)


def _process_cbor_annotations(obj: Any) -> Any:
    """
    Recursively process CBOR type annotations in JSON data.
    
    Converts:
        {"$cbor": "bytes", "$value": "base64"} → bytes
        {"$cbor": "tag", "$tag": N, "$value": V} → (N, V)
        {"$cbor": "NaN"} → float('nan')
        {"$cbor": "Infinity"} → float('inf')
        {"$cbor": "-Infinity"} → float('-inf')
    """
    if isinstance(obj, dict):
        # Check for CBOR type annotation
        if "$cbor" in obj:
            cbor_type = obj["$cbor"]
            
            if cbor_type == "bytes" and "$value" in obj:
                # Decode base64 to bytes
                return base64.b64decode(obj["$value"])
            
            elif cbor_type == "tag" and "$tag" in obj and "$value" in obj:
                # Create tagged value tuple
                tag_num = obj["$tag"]
                value = _process_cbor_annotations(obj["$value"])
                return (tag_num, value)
            
            elif cbor_type == "NaN":
                return float('nan')
            
            elif cbor_type == "Infinity":
                return float('inf')
            
            elif cbor_type == "-Infinity":
                return float('-inf')
        
        # Recursively process dict values
        return {k: _process_cbor_annotations(v) for k, v in obj.items()}
    
    elif isinstance(obj, list):
        # Recursively process list items
        return [_process_cbor_annotations(item) for item in obj]
    
    else:
        return obj


def cbor_file_to_json_file(cbor_path: str, json_path: str, 
                           typed: bool = False, pretty: bool = True):
    """
                           Convert a CBOR file to a JSON file, optionally preserving CBOR-specific types.
                           
                           Parameters:
                               cbor_path (str): Path to the input CBOR file to read.
                               json_path (str): Path to the output JSON file to write (overwrites if exists).
                               typed (bool): If True, preserve CBOR-specific types in JSON using annotations.
                               pretty (bool): If True, write human-readable pretty-printed JSON.
                           """
    with open(cbor_path, 'rb') as f:
        cbor_bytes = f.read()
    
    json_str = cbor_to_json(cbor_bytes, typed=typed, pretty=pretty)
    
    with open(json_path, 'w') as f:
        f.write(json_str)


def json_file_to_cbor_file(json_path: str, cbor_path: str, 
                           canonical: bool = False):
    """
                           Convert a JSON file to CBOR and write the resulting CBOR bytes to the specified output file.
                           
                           Parameters:
                               json_path (str): Path to the input JSON file.
                               cbor_path (str): Path to the output CBOR file to be written.
                               canonical (bool): If True, produce canonical CBOR encoding.
                           """
    with open(json_path, 'r') as f:
        json_str = f.read()
    
    cbor_bytes = json_to_cbor(json_str, canonical=canonical)
    
    with open(cbor_path, 'wb') as f:
        f.write(cbor_bytes)


# CLI functionality
if __name__ == '__main__':
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Convert between JSON and CBOR formats',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # CBOR to JSON
  python cbor_json.py to-json input.cbor output.json
  python cbor_json.py to-json input.cbor output.json --pretty --typed
  
  # JSON to CBOR
  python cbor_json.py to-cbor input.json output.cbor
  python cbor_json.py to-cbor input.json output.cbor --canonical
  
  # Stdin/stdout
  cat data.cbor | python cbor_json.py to-json - -
  echo '{"test": 1}' | python cbor_json.py to-cbor - output.cbor
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # to-json command
    to_json_parser = subparsers.add_parser('to-json', help='Convert CBOR to JSON')
    to_json_parser.add_argument('input', help='Input CBOR file (use - for stdin)')
    to_json_parser.add_argument('output', help='Output JSON file (use - for stdout)')
    to_json_parser.add_argument('--typed', action='store_true',
                                help='Preserve CBOR types with annotations')
    to_json_parser.add_argument('--pretty', action='store_true',
                                help='Pretty-print JSON with indentation')
    to_json_parser.add_argument('--indent', type=int, default=2,
                                help='Indentation spaces (default: 2)')
    
    # to-cbor command
    to_cbor_parser = subparsers.add_parser('to-cbor', help='Convert JSON to CBOR')
    to_cbor_parser.add_argument('input', help='Input JSON file (use - for stdin)')
    to_cbor_parser.add_argument('output', help='Output CBOR file (use - for stdout)')
    to_cbor_parser.add_argument('--canonical', action='store_true',
                                help='Use canonical CBOR encoding')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.command == 'to-json':
            # Read CBOR input
            if args.input == '-':
                cbor_bytes = sys.stdin.buffer.read()
            else:
                with open(args.input, 'rb') as f:
                    cbor_bytes = f.read()
            
            # Convert to JSON
            json_str = cbor_to_json(cbor_bytes, typed=args.typed, 
                                   pretty=args.pretty, indent=args.indent)
            
            # Write JSON output
            if args.output == '-':
                sys.stdout.write(json_str)
                sys.stdout.write('\n')
            else:
                with open(args.output, 'w') as f:
                    f.write(json_str)
            
            if args.output != '-':
                print(f"[OK] Converted {args.input} to {args.output}", file=sys.stderr)
        
        elif args.command == 'to-cbor':
            # Read JSON input
            if args.input == '-':
                json_str = sys.stdin.read()
            else:
                with open(args.input, 'r') as f:
                    json_str = f.read()
            
            # Convert to CBOR
            cbor_bytes = json_to_cbor(json_str, canonical=args.canonical)
            
            # Write CBOR output
            if args.output == '-':
                sys.stdout.buffer.write(cbor_bytes)
            else:
                with open(args.output, 'wb') as f:
                    f.write(cbor_bytes)
            
            if args.output != '-':
                print(f"[OK] Converted {args.input} to {args.output} ({len(cbor_bytes)} bytes)", 
                      file=sys.stderr)
    
    except Exception as e:
        print(f"[X] Error: {e}", file=sys.stderr)
        sys.exit(1)