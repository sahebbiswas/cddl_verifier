#!/usr/bin/env python3
"""
JSON ↔ CBOR Conversion Module

Bidirectional conversion between JSON and CBOR with type preservation.

Features:
- Convert JSON to CBOR
- Convert CBOR to JSON
- Preserve CBOR-specific types (bytes, tags)
- Handle special values (NaN, Infinity, -Infinity, undefined, simple values)
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
import re
import base64
import math
from typing import Any, Dict, List, Union
from ._cbor import CBOR, CBORKey, cbor_encode, cbor_decode, map_key
from ._cbor_values import UNDEFINED, CBORSimple, CBORTag


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
        Initialize encoder.
        
        Args:
            typed: If True, add type annotations for CBOR-specific types
        """
        super().__init__(*args, **kwargs)
        self.typed = typed
    
    def default(self, obj):
        """Handle CBOR-specific types."""
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
        
        # Handle tagged values
        if isinstance(obj, CBORTag):
            tag_num, value = obj.tag, obj.value
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
        
        # undefined and the other simple values (#168)
        if obj is UNDEFINED:
            return {"$cbor": "undefined"} if self.typed else None
        if isinstance(obj, CBORSimple):
            return {"$cbor": "simple", "$value": obj.number} if self.typed else obj.number
        
        return super().default(obj)


def cbor_to_json(cbor_bytes: bytes, typed: bool = False, pretty: bool = False,
                 indent: int = 2, sort_keys: bool = False) -> str:
    """
    Convert CBOR bytes to JSON string.
    
    Args:
        cbor_bytes: CBOR encoded bytes
        typed: If True, preserve CBOR-specific types with annotations
        pretty: If True, pretty-print with indentation
        indent: Number of spaces for indentation (if pretty=True)
        sort_keys: Sort JSON object keys alphabetically (default: False).
            Note: untyped, CBOR integer keys become strings in JSON, so
            sorting is lexicographic ("1","10","2") rather than numeric
            (1,2,10). Leave False to preserve insertion order.

    Raises:
        ValueError: untyped, when two map keys become the same JSON key
            (``0`` and ``"0"``). With ``typed=True`` a map with non-string
            keys is written as ``{"$cbor": "map", "$value": [[k, v], ...]}``.
    
    Returns:
        JSON string
    
    Example:
        >>> cbor_bytes = cbor_encode({0: "test", 1: b"data"})
        >>> json_str = cbor_to_json(cbor_bytes, typed=True, pretty=True)
        >>> print(json_str)
        {
          "$cbor": "map",
          "$value": [
            [
              0,
              "test"
            ],
            [
              1,
              {
                "$cbor": "bytes",
                "$value": "ZGF0YQ=="
              }
            ]
          ]
        }
    """
    # Decode CBOR
    data = cbor_decode(cbor_bytes)
    
    # Pre-process to handle CBOR-specific types
    processed = _preprocess_for_json(data, typed)
    
    # Convert to JSON
    if pretty:
        return json.dumps(processed, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
    else:
        return json.dumps(processed, sort_keys=sort_keys, ensure_ascii=False)


def _preprocess_for_json(obj: Any, typed: bool) -> Any:
    """
    Pre-process CBOR data to make it JSON-compatible.
    
    Converts:
    - bytes → base64 string or typed annotation
    - CBORTag → value or typed annotation
    - nested structures recursively
    """
    if isinstance(obj, CBORKey):
        obj = obj.value

    # Handle bytes
    if isinstance(obj, bytes):      
        
        if typed:
            return {
                "$cbor": "bytes",
                "$value": base64.b64encode(obj).decode('ascii')
            }
        else:
            return base64.b64encode(obj).decode('ascii')
    
    # Handle tagged values
    if isinstance(obj, CBORTag):
        tag_num, value = obj.tag, obj.value
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
    
    # undefined and the other simple values (#168): untyped, JSON has no
    # counterpart, so they become null and the simple value's number
    if obj is UNDEFINED:
        return {"$cbor": "undefined"} if typed else None
    if isinstance(obj, CBORSimple):
        return {"$cbor": "simple", "$value": obj.number} if typed else obj.number

    # Handle dicts recursively
    if isinstance(obj, dict):
        if typed and any(not isinstance(k, str) or k == "$cbor" for k in obj):
            # JSON object keys are strings: keep other keys as [key, value]
            # pairs (#91). A "$cbor" key would read back as an annotation.
            return {
                "$cbor": "map",
                "$value": [[_preprocess_for_json(k, typed), _preprocess_for_json(v, typed)]
                           for k, v in obj.items()],
            }
        result = {}
        for k, v in obj.items():
            key = _json_key(k)
            if key in result:
                raise ValueError(
                    f"CBOR map keys {_original_key(obj, key)!r} and {k!r} both become the "
                    f"JSON key {key!r}; use typed=True to keep both")
            result[key] = _preprocess_for_json(v, typed)
        return result
    
    # Handle lists recursively
    if isinstance(obj, list):
        return [_preprocess_for_json(item, typed) for item in obj]
    
    # Return as-is for basic types
    return obj


def _json_key(key: Any) -> str:
    """The JSON object key untyped conversion writes for a CBOR map key."""
    if isinstance(key, CBORKey):
        key = key.value
    if isinstance(key, str):
        return key
    if isinstance(key, bytes):
        return base64.b64encode(key).decode('ascii')
    if isinstance(key, (bool, int, float)) or key is None:
        return json.dumps(key)  # 0 → "0", True → "true", None → "null", 1.5 → "1.5"
    if key is UNDEFINED or isinstance(key, CBORSimple):
        return repr(key)  # "undefined", "simple(16)"
    return json.dumps(_preprocess_for_json(key, False), ensure_ascii=False)


def _original_key(obj: dict, json_key: str) -> Any:
    """The first key of *obj* that becomes *json_key*."""
    return next(k for k in obj if _json_key(k) == json_key)


def _reject_duplicate_keys(pairs: List) -> Dict:
    """``object_pairs_hook`` that rejects a JSON object key given twice."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


#: The annotation types of typed JSON and the keys each one takes besides ``$cbor``.
#: ``$cbor`` is the only reserved key: ``$tag`` and ``$value`` mean something only inside
#: an object that has it. (#79)
_ANNOTATION_KEYS = {
    "bytes": frozenset({"$value"}),
    "tag": frozenset({"$tag", "$value"}),
    "map": frozenset({"$value"}),
    "simple": frozenset({"$value"}),
    "undefined": frozenset(),
    "NaN": frozenset(),
    "Infinity": frozenset(),
    "-Infinity": frozenset(),
}
_SPECIAL_FLOATS = {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}


def json_to_cbor(json_str: str, canonical: bool = False, annotations: bool = True) -> bytes:
    """
    Convert JSON string to CBOR bytes.

    Args:
        json_str: JSON string
        canonical: If True, use canonical CBOR encoding
        annotations: If True (the default), an object with a ``"$cbor"`` key is a
            type annotation (see below) and is read strictly. If False, the JSON is
            plain data and ``"$cbor"`` is an ordinary key: use this for JSON from a
            source that must not be able to make byte strings, tags or simple values.

    Returns:
        CBOR encoded bytes

    Raises:
        ValueError: the text is not JSON, a JSON object key is given twice, the JSON is
            nested too deeply, or an annotation is malformed. The message gives the
            place in the document, for example ``$.items[2].data``.

    ``"$cbor"`` is the one reserved key. An object that has it is an annotation and
    must be exactly one of these, with no other keys and no unknown type (so a typo
    such as ``"byte"`` is an error, not a map):

        {"$cbor": "bytes", "$value": "base64data"}
        {"$cbor": "tag", "$tag": 32, "$value": "http://example.com"}
        {"$cbor": "map", "$value": [[key, value], ...]}
        {"$cbor": "simple", "$value": 16}
        {"$cbor": "undefined"}   {"$cbor": "NaN"}   {"$cbor": "Infinity"}   {"$cbor": "-Infinity"}

    The base64 of ``bytes`` is the standard alphabet with padding, as ``cbor_to_json``
    writes it: no whitespace, no missing padding, no stray bits in the last character.
    ``$tag`` is an integer from 0 to 2**64 - 1. To write an ordinary map that has the
    key ``"$cbor"``, use the ``map`` form.

    Example:
        >>> json_str = '{"name": "test", "id": 42}'
        >>> cbor_bytes = json_to_cbor(json_str)
        >>> data = cbor_decode(cbor_bytes)
        >>> print(data)
        {'name': 'test', 'id': 42}
    """
    try:
        # Parse JSON; a key given twice would otherwise silently keep the last value
        data = json.loads(json_str, object_pairs_hook=_reject_duplicate_keys)
        processed = _process_cbor_annotations(data) if annotations else data
        return cbor_encode(processed, canonical=canonical)
    except RecursionError:
        raise ValueError("the JSON is nested too deeply") from None


def _where(path: str, key: Any) -> str:
    """*path* extended by an object key (a string) or an array index (an int)."""
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if re.fullmatch(r"[A-Za-z_$][\w$]*", key) else f"{path}[{json.dumps(key)}]"


def _bytes_annotation(value: Any, path: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{path}: a $cbor bytes annotation needs its $value as a base64 "
                         f"string, got {_describe(value)}")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:  # binascii.Error is one
        raise ValueError(f"{path}: the $value of a $cbor bytes annotation is not valid "
                         f"base64 ({exc}); use the standard alphabet with padding and no "
                         f"whitespace") from None
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError(f"{path}: the $value of a $cbor bytes annotation is not canonical "
                         f"base64 (the unused bits of its last character are not zero)")
    return raw


def _describe(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 40 else text[:37] + "..."


def _process_cbor_annotations(obj: Any, path: str = "$") -> Any:
    """
    Recursively read the CBOR type annotations of typed JSON (#79).

    Converts:
        {"$cbor": "bytes", "$value": "base64"} → bytes
        {"$cbor": "tag", "$tag": N, "$value": V} → CBORTag(N, V)
        {"$cbor": "map", "$value": [[K, V], ...]} → {K: V, ...}
        {"$cbor": "undefined"} → UNDEFINED
        {"$cbor": "simple", "$value": N} → CBORSimple(N)
        {"$cbor": "NaN"} → float('nan')
        {"$cbor": "Infinity"} → float('inf')
        {"$cbor": "-Infinity"} → float('-inf')

    An object with a ``$cbor`` key is always an annotation: an unknown type, a missing
    or extra key, or a malformed value raises ``ValueError`` naming *path*. Any other
    object is ordinary data.
    """
    if isinstance(obj, list):
        return [_process_cbor_annotations(item, _where(path, i)) for i, item in enumerate(obj)]
    if not isinstance(obj, dict):
        return obj
    if "$cbor" not in obj:
        return {k: _process_cbor_annotations(v, _where(path, k)) for k, v in obj.items()}

    kind = obj["$cbor"]
    if not isinstance(kind, str) or kind not in _ANNOTATION_KEYS:
        raise ValueError(f"{path}: unknown $cbor type {_describe(kind)}; expected one of "
                         f"{', '.join(_ANNOTATION_KEYS)}. An ordinary object with the key "
                         f"\"$cbor\" must be written as a $cbor map")
    allowed = _ANNOTATION_KEYS[kind]
    extra = sorted(set(obj) - allowed - {"$cbor"})
    if extra:
        raise ValueError(f"{path}: a $cbor {kind} annotation takes "
                         f"{', '.join(['$cbor'] + sorted(allowed))} and no other key, "
                         f"got {', '.join(extra)}")
    missing = sorted(allowed - set(obj))
    if missing:
        raise ValueError(f"{path}: a $cbor {kind} annotation needs {', '.join(missing)}")

    if kind in _SPECIAL_FLOATS:
        return _SPECIAL_FLOATS[kind]
    if kind == "undefined":
        return UNDEFINED
    value_path = _where(path, "$value")
    if kind == "bytes":
        return _bytes_annotation(obj["$value"], value_path)
    if kind == "simple":
        try:
            return CBORSimple(obj["$value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{value_path}: invalid $value in a $cbor simple annotation: "
                             f"{exc}") from None
    if kind == "tag":
        try:
            tag = CBORTag(obj["$tag"], None)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{_where(path, '$tag')}: invalid $tag in a $cbor tag "
                             f"annotation: {exc}") from None
        return CBORTag(tag.tag, _process_cbor_annotations(obj["$value"], value_path))

    # a map: [[key, value], ...], for keys that are not all strings (#91)
    pairs = obj["$value"]
    if not isinstance(pairs, list):
        raise ValueError(f"{value_path}: a $cbor map needs a list of [key, value] pairs as "
                         f"$value, got {_describe(pairs)}")
    result = {}
    for i, pair in enumerate(pairs):
        pair_path = _where(value_path, i)
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f"{pair_path}: a $cbor map entry must be a [key, value] pair, "
                             f"got {_describe(pair)}")
        # Same key form as the CBOR decoder (#88): 1, true and 1.0 stay distinct
        try:
            key = map_key(_process_cbor_annotations(pair[0], _where(pair_path, 0)))
        except TypeError as exc:
            raise ValueError(f"{_where(pair_path, 0)}: not usable as a map key: {exc}") from None
        if key in result:
            raise ValueError(f"{pair_path}: Duplicate map key in $cbor map: {key!r}")
        result[key] = _process_cbor_annotations(pair[1], _where(pair_path, 1))
    return result


def cbor_file_to_json_file(cbor_path: str, json_path: str, 
                           typed: bool = False, pretty: bool = True):
    """
    Convert CBOR file to JSON file.
    
    Args:
        cbor_path: Path to input CBOR file
        json_path: Path to output JSON file
        typed: If True, preserve CBOR types
        pretty: If True, pretty-print JSON

    The JSON file is written as UTF-8, whatever the locale.

    Example:
        >>> cbor_file_to_json_file('data.cbor', 'data.json', pretty=True)
    """
    with open(cbor_path, 'rb') as f:
        cbor_bytes = f.read()
    
    json_str = cbor_to_json(cbor_bytes, typed=typed, pretty=pretty)
    
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(json_str)


def json_file_to_cbor_file(json_path: str, cbor_path: str,
                           canonical: bool = False, annotations: bool = True):
    """
    Convert JSON file to CBOR file.
    
    Args:
        json_path: Path to input JSON file
        cbor_path: Path to output CBOR file
        canonical: If True, use canonical CBOR encoding
        annotations: Read ``"$cbor"`` objects as type annotations (see
            :func:`json_to_cbor`); False reads the JSON as plain data

    The JSON file is read as UTF-8, whatever the locale.

    Example:
        >>> json_file_to_cbor_file('data.json', 'data.cbor', canonical=True)
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        json_str = f.read()
    
    cbor_bytes = json_to_cbor(json_str, canonical=canonical, annotations=annotations)
    
    with open(cbor_path, 'wb') as f:
        f.write(cbor_bytes)


# CLI functionality
def main(argv=None):
    """Command-line interface for converting between JSON and CBOR."""
    # Command-line interface for converting between JSON and CBOR
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Convert between JSON and CBOR formats',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # CBOR to JSON
  python -m cddl_verifier.json_codec to-json input.cbor output.json
  python -m cddl_verifier.json_codec to-json input.cbor output.json --pretty --typed
  
  # JSON to CBOR
  python -m cddl_verifier.json_codec to-cbor input.json output.cbor
  python -m cddl_verifier.json_codec to-cbor input.json output.cbor --canonical
  python -m cddl_verifier.json_codec to-cbor input.json output.cbor --plain
  
  # Stdin/stdout
  cat data.cbor | python -m cddl_verifier.json_codec to-json - -
  echo '{"test": 1}' | python -m cddl_verifier.json_codec to-cbor - output.cbor
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
    to_cbor_parser.add_argument('--plain', action='store_true',
                                help='Read the JSON as plain data: "$cbor" objects are not '
                                     'type annotations')
    
    args = parser.parse_args(argv)
    
    if not args.command:
        # 
        parser.print_help()
        sys.exit(1)
    
    try:
        # 
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
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(json_str)
            
            if args.output != '-':
                print(f"[OK] Converted {args.input} to {args.output}", file=sys.stderr)
        
        elif args.command == 'to-cbor':
            # Read JSON input
            if args.input == '-':
                json_str = sys.stdin.read()
            else:
                with open(args.input, 'r', encoding='utf-8') as f:
                    json_str = f.read()
            
            # Convert to CBOR
            cbor_bytes = json_to_cbor(json_str, canonical=args.canonical,
                                      annotations=not args.plain)
            
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
        # Handle 
        print(f"[X] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
