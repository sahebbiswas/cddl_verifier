#!/usr/bin/env python3
"""
Simple CBOR - Unified Encoder, Decoder, and Diagnostic Dumper

A complete CBOR implementation in a single class with:
- Encoding Python objects to CBOR bytes
- Decoding CBOR bytes to Python objects
- Diagnostic dumps with hex view and type annotations
- Dictionary/list-like interface for data access

CBOR Major Types:
- 0: Unsigned integer
- 1: Negative integer  
- 2: Byte string
- 3: Text string
- 4: Array
- 5: Map
- 6: Tagged item
- 7: Simple values (bool, null, float)

References:
- RFC 8949: Concise Binary Object Representation (CBOR)
"""

import struct
from typing import Any, Union, Tuple, Dict, List

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
SIMPLE_FLOAT16 = 25
SIMPLE_FLOAT32 = 26
SIMPLE_FLOAT64 = 27


class CBOR:
    """
    Unified CBOR encoder, decoder, and diagnostic dumper.
    
    This class provides complete CBOR functionality:
    - Encode Python objects to CBOR bytes
    - Decode CBOR bytes to Python objects
    - Generate diagnostic dumps with hex view
    - Access/modify data with dictionary/list interface
    
    Example:
        # Create from data
        cbor = CBOR({0: "test", 1: [1, 2, 3]})
        
        # Load from bytes
        cbor = CBOR.load(cbor_bytes)
        
        # Access data
        print(cbor[0])
        
        # Modify data
        cbor[0] = "modified"
        
        # Encode to bytes
        new_bytes = cbor.encode()
        
        # View diagnostic dump
        print(cbor.diag())
    """
    
    def __init__(self, data: Any = None):
        """
        Initialize CBOR object with Python data.
        
        Args:
            data: Python object to work with
        """
        self.data = data
        self._cached_bytes = None
        self._indent_str = "  "
    
    # ========================================================================
    # CLASS METHODS - Loading and Convenience
    # ========================================================================
    
    @classmethod
    def load(cls, cbor_bytes: bytes) -> 'CBOR':
        """
        Load CBOR bytes and decode to Python object.
        
        Args:
            cbor_bytes: CBOR encoded bytes
            
        Returns:
            CBOR object with decoded data
        """
        obj = cls()
        obj._cached_bytes = cbor_bytes
        obj.data = obj._decode(cbor_bytes)
        return obj
    
    @classmethod
    def loads(cls, cbor_bytes: bytes) -> Any:
        """
        Decode CBOR bytes directly to Python object.
        
        Args:
            cbor_bytes: CBOR encoded bytes
            
        Returns:
            Decoded Python object
        """
        return cls.load(cbor_bytes).data
    
    # ========================================================================
    # ENCODING - Convert Python objects to CBOR bytes
    # ========================================================================
    
    def encode(self, canonical: bool = False) -> bytes:
        """
        Encode current data to CBOR bytes.
        
        Args:
            canonical: If True, use canonical (deterministic) encoding per RFC 8949 §4.2
        
        Returns:
            CBOR encoded bytes
        
        Canonical encoding rules (RFC 8949 Section 4.2):
        - Integers use shortest form
        - Map keys sorted by encoded byte comparison
        - Definite-length encoding only
        - No duplicate map keys
        """
        self._canonical = canonical
        self._cached_bytes = self._encode_item(self.data)
        self._canonical = False  # Reset
        return self._cached_bytes
    
    def dumps(self) -> bytes:
        """Alias for encode()."""
        return self.encode()
    
    def _encode_item(self, obj: Any) -> bytes:
        """Encode a single Python object to CBOR."""
        # Handle tagged values (tag_num, value)
        if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], int) and obj[0] >= 0:
            tag_num, value = obj
            if tag_num < 2**64:  # Valid tag range
                return self._encode_tag(tag_num, value)
        
        # Handle by type
        if obj is None:
            return bytes([0xf6])
        elif obj is False:
            return bytes([0xf4])
        elif obj is True:
            return bytes([0xf5])
        elif isinstance(obj, bool):
            return bytes([0xf5 if obj else 0xf4])
        elif isinstance(obj, int):
            return self._encode_int(obj)
        elif isinstance(obj, bytes):
            return self._encode_bytes(obj)
        elif isinstance(obj, str):
            return self._encode_string(obj)
        elif isinstance(obj, (list, tuple)):
            return self._encode_array(obj)
        elif isinstance(obj, dict):
            return self._encode_map(obj)
        elif isinstance(obj, float):
            return self._encode_float(obj)
        else:
            raise TypeError(f"Cannot encode type {type(obj).__name__}")
    
    def _encode_int(self, value: int) -> bytes:
        """Encode an integer."""
        if value >= 0:
            return self._encode_uint(MAJOR_TYPE_UINT, value)
        else:
            return self._encode_uint(MAJOR_TYPE_NINT, -1 - value)
    
    def _encode_uint(self, major_type: int, value: int) -> bytes:
        """Encode unsigned integer with given major type."""
        initial_byte = major_type << 5
        
        if value < 24:
            return bytes([initial_byte | value])
        elif value < 256:
            return bytes([initial_byte | 24, value])
        elif value < 65536:
            return bytes([initial_byte | 25]) + struct.pack('>H', value)
        elif value < 4294967296:
            return bytes([initial_byte | 26]) + struct.pack('>I', value)
        else:
            return bytes([initial_byte | 27]) + struct.pack('>Q', value)
    
    def _encode_bytes(self, value: bytes) -> bytes:
        """Encode byte string (major type 2)."""
        result = self._encode_uint(MAJOR_TYPE_BSTR, len(value))
        return result + value
    
    def _encode_string(self, value: str) -> bytes:
        """Encode text string (major type 3)."""
        utf8_bytes = value.encode('utf-8')
        result = self._encode_uint(MAJOR_TYPE_TSTR, len(utf8_bytes))
        return result + utf8_bytes
    
    def _encode_array(self, value: Union[list, tuple]) -> bytes:
        """Encode array (major type 4)."""
        result = self._encode_uint(MAJOR_TYPE_ARRAY, len(value))
        for item in value:
            result += self._encode_item(item)
        return result
    
    def _encode_map(self, value: dict) -> bytes:
        """Encode map (major type 5)."""
        result = self._encode_uint(MAJOR_TYPE_MAP, len(value))
        
        # Canonical encoding: sort keys by their encoded representation
        if hasattr(self, '_canonical') and self._canonical:
            # Encode all keys and sort by byte comparison
            encoded_pairs = []
            for key, val in value.items():
                encoded_key = self._encode_item(key)
                encoded_val = self._encode_item(val)
                encoded_pairs.append((encoded_key, encoded_val))
            
            # Sort by encoded key bytes
            encoded_pairs.sort(key=lambda x: x[0])
            
            # Concatenate sorted pairs
            for encoded_key, encoded_val in encoded_pairs:
                result += encoded_key + encoded_val
        else:
            # Standard encoding: maintain dict order
            for key, val in value.items():
                result += self._encode_item(key)
                result += self._encode_item(val)
        
        return result
    
    def _encode_tag(self, tag_num: int, value: Any) -> bytes:
        """Encode tagged value (major type 6)."""
        result = self._encode_uint(MAJOR_TYPE_TAG, tag_num)
        result += self._encode_item(value)
        return result
    
    def _encode_float(self, value: float) -> bytes:
        """Encode float (major type 7) - uses float64."""
        return bytes([0xfb]) + struct.pack('>d', value)
    
    # ========================================================================
    # DECODING - Convert CBOR bytes to Python objects
    # ========================================================================
    
    def _decode(self, data: bytes) -> Any:
        """Decode CBOR bytes to Python object."""
        self._decode_data = data
        self._decode_pos = 0
        
        if not data:
            raise ValueError("Empty CBOR data")
        
        return self._decode_item()
    
    def _decode_item(self) -> Any:
        """Decode a single CBOR item."""
        if self._decode_pos >= len(self._decode_data):
            raise ValueError("Unexpected end of data")
        
        initial_byte = self._decode_data[self._decode_pos]
        self._decode_pos += 1
        
        major_type = (initial_byte >> 5) & 0x07
        additional_info = initial_byte & 0x1f
        
        if major_type == MAJOR_TYPE_UINT:
            return self._decode_uint(additional_info)
        elif major_type == MAJOR_TYPE_NINT:
            value = self._decode_uint(additional_info)
            return -1 - value
        elif major_type == MAJOR_TYPE_BSTR:
            length = self._decode_length(additional_info)
            return self._read_bytes(length)
        elif major_type == MAJOR_TYPE_TSTR:
            length = self._decode_length(additional_info)
            bytes_data = self._read_bytes(length)
            return bytes_data.decode('utf-8')
        elif major_type == MAJOR_TYPE_ARRAY:
            return self._decode_array(additional_info)
        elif major_type == MAJOR_TYPE_MAP:
            return self._decode_map(additional_info)
        elif major_type == MAJOR_TYPE_TAG:
            tag_num = self._decode_uint(additional_info)
            tagged_value = self._decode_item()
            return (tag_num, tagged_value)
        elif major_type == MAJOR_TYPE_SIMPLE:
            return self._decode_simple(additional_info)
        else:
            raise ValueError(f"Unknown major type: {major_type}")
    
    def _decode_uint(self, additional_info: int) -> int:
        """Decode unsigned integer."""
        if additional_info < 24:
            return additional_info
        elif additional_info == 24:
            return self._read_bytes(1)[0]
        elif additional_info == 25:
            return struct.unpack('>H', self._read_bytes(2))[0]
        elif additional_info == 26:
            return struct.unpack('>I', self._read_bytes(4))[0]
        elif additional_info == 27:
            return struct.unpack('>Q', self._read_bytes(8))[0]
        else:
            raise ValueError(f"Invalid additional info for uint: {additional_info}")
    
    def _decode_length(self, additional_info: int) -> int:
        """Decode length value."""
        if additional_info == 31:
            raise NotImplementedError("Indefinite-length items not supported")
        return self._decode_uint(additional_info)
    
    def _decode_array(self, additional_info: int) -> List[Any]:
        """Decode array."""
        length = self._decode_length(additional_info)
        array = []
        for i in range(length):
            array.append(self._decode_item())
        return array
    
    def _decode_map(self, additional_info: int) -> Dict[Any, Any]:
        """Decode map."""
        length = self._decode_length(additional_info)
        map_dict = {}
        for i in range(length):
            key = self._decode_item()
            value = self._decode_item()
            map_dict[key] = value
        return map_dict
    
    def _decode_simple(self, additional_info: int) -> Any:
        """Decode simple values."""
        if additional_info == SIMPLE_FALSE:
            return False
        elif additional_info == SIMPLE_TRUE:
            return True
        elif additional_info == SIMPLE_NULL:
            return None
        elif additional_info == 23:
            raise NotImplementedError("Undefined value not supported")
        elif additional_info == SIMPLE_FLOAT16:
            bytes_data = self._read_bytes(2)
            return struct.unpack('>e', bytes_data)[0] if hasattr(struct, 'unpack') else 0.0
        elif additional_info == SIMPLE_FLOAT32:
            bytes_data = self._read_bytes(4)
            return struct.unpack('>f', bytes_data)[0]
        elif additional_info == SIMPLE_FLOAT64:
            bytes_data = self._read_bytes(8)
            return struct.unpack('>d', bytes_data)[0]
        else:
            raise ValueError(f"Unknown simple value: {additional_info}")
    
    def _read_bytes(self, n: int) -> bytes:
        """Read n bytes from decode data."""
        if self._decode_pos + n > len(self._decode_data):
            raise ValueError("Unexpected end of data")
        result = self._decode_data[self._decode_pos:self._decode_pos + n]
        self._decode_pos += n
        return result
    
    # ========================================================================
    # DIAGNOSTIC DUMP - Pretty-printed hex view with annotations
    # ========================================================================
    
    def diag(self, indent: str = "  ") -> str:
        """
        Generate diagnostic dump of current data.
        
        Args:
            indent: Indentation string for nested structures
            
        Returns:
            Pretty-printed diagnostic dump with hex bytes and types
        """
        # Encode if not already encoded
        if self._cached_bytes is None:
            self._cached_bytes = self.encode()
        
        return self._generate_diag(self._cached_bytes, indent)
    
    def _generate_diag(self, data: bytes, indent: str) -> str:
        """Generate diagnostic dump from CBOR bytes."""
        self._diag_data = data
        self._diag_pos = 0
        self._diag_indent_str = indent
        self._diag_current_indent = 0
        self._diag_lines = []
        
        if not data:
            return "# Empty CBOR data"
        
        self._diag_dump_item()
        return '\n'.join(self._diag_lines)
    
    def _diag_dump_item(self, label: str = "") -> None:
        """Dump a single CBOR item with diagnostic info."""
        if self._diag_pos >= len(self._diag_data):
            self._diag_add_line(self._diag_pos, b'', "# ERROR: Unexpected end of data")
            return
        
        start_pos = self._diag_pos
        initial_byte = self._diag_data[self._diag_pos]
        self._diag_pos += 1
        
        major_type = (initial_byte >> 5) & 0x07
        additional_info = initial_byte & 0x1f
        
        # Dispatch based on major type
        if major_type == MAJOR_TYPE_UINT:
            self._diag_dump_uint(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_NINT:
            self._diag_dump_nint(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_BSTR:
            self._diag_dump_bstr(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_TSTR:
            self._diag_dump_tstr(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_ARRAY:
            self._diag_dump_array(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_MAP:
            self._diag_dump_map(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_TAG:
            self._diag_dump_tag(start_pos, additional_info, label)
        elif major_type == MAJOR_TYPE_SIMPLE:
            self._diag_dump_simple(start_pos, additional_info, label)
    
    def _diag_dump_uint(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump unsigned integer."""
        value, end_pos = self._diag_read_uint(additional_info)
        hex_bytes = self._diag_data[start_pos:end_pos]
        self._diag_add_line(start_pos, hex_bytes, f"{label}uint({value})")
    
    def _diag_dump_nint(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump negative integer."""
        uint_value, end_pos = self._diag_read_uint(additional_info)
        value = -1 - uint_value
        hex_bytes = self._diag_data[start_pos:end_pos]
        self._diag_add_line(start_pos, hex_bytes, f"{label}nint({value})")
    
    def _diag_dump_bstr(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump byte string."""
        length, length_end = self._diag_read_uint(additional_info)
        
        # Header
        header_bytes = self._diag_data[start_pos:length_end]
        self._diag_add_line(start_pos, header_bytes, f"{label}bytes({length})")
        
        # Data
        if length > 0:
            self._diag_current_indent += 1
            data_start = length_end
            data_end = length_end + length
            data_bytes = self._diag_data[data_start:data_end]
            
            if length <= 32:
                self._diag_add_line(data_start, data_bytes, f"h'{data_bytes.hex()}'")
            else:
                # Show first and last 16 bytes
                self._diag_add_line(data_start, data_bytes[:16], f"h'{data_bytes[:16].hex()}'")
                self._diag_add_line(data_start + 16, b'...', f"# ... ({length - 32} more bytes) ...")
                self._diag_add_line(data_end - 16, data_bytes[-16:], f"h'{data_bytes[-16:].hex()}'")
            
            self._diag_current_indent -= 1
            self._diag_pos = data_end
    
    def _diag_dump_tstr(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump text string."""
        length, length_end = self._diag_read_uint(additional_info)
        
        # Header
        header_bytes = self._diag_data[start_pos:length_end]
        self._diag_add_line(start_pos, header_bytes, f"{label}text({length})")
        
        # Data
        if length > 0:
            self._diag_current_indent += 1
            data_start = length_end
            data_end = length_end + length
            data_bytes = self._diag_data[data_start:data_end]
            
            try:
                text = data_bytes.decode('utf-8')
                if length <= 64:
                    self._diag_add_line(data_start, data_bytes, f'"{text}"')
                else:
                    self._diag_add_line(data_start, data_bytes[:32], f'"{text[:32]}..."')
            except UnicodeDecodeError:
                self._diag_add_line(data_start, data_bytes, f"# Invalid UTF-8: {data_bytes.hex()}")
            
            self._diag_current_indent -= 1
            self._diag_pos = data_end
    
    def _diag_dump_array(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump array."""
        length, length_end = self._diag_read_uint(additional_info)
        
        header_bytes = self._diag_data[start_pos:length_end]
        self._diag_add_line(start_pos, header_bytes, f"{label}array({length})")
        
        if length > 0:
            self._diag_current_indent += 1
            for i in range(length):
                self._diag_dump_item(f"[{i}] ")
            self._diag_current_indent -= 1
    
    def _diag_dump_map(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump map."""
        length, length_end = self._diag_read_uint(additional_info)
        
        header_bytes = self._diag_data[start_pos:length_end]
        self._diag_add_line(start_pos, header_bytes, f"{label}map({length})")
        
        if length > 0:
            self._diag_current_indent += 1
            for i in range(length):
                self._diag_dump_item("key: ")
                self._diag_dump_item("val: ")
            self._diag_current_indent -= 1
    
    def _diag_dump_tag(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump tagged item."""
        tag_num, tag_end = self._diag_read_uint(additional_info)
        
        header_bytes = self._diag_data[start_pos:tag_end]
        self._diag_add_line(start_pos, header_bytes, f"{label}tag({tag_num})")
        
        self._diag_current_indent += 1
        self._diag_dump_item()
        self._diag_current_indent -= 1
    
    def _diag_dump_simple(self, start_pos: int, additional_info: int, label: str) -> None:
        """Dump simple values."""
        if additional_info == SIMPLE_FALSE:
            self._diag_add_line(start_pos, bytes([0xf4]), f"{label}false")
        elif additional_info == SIMPLE_TRUE:
            self._diag_add_line(start_pos, bytes([0xf5]), f"{label}true")
        elif additional_info == SIMPLE_NULL:
            self._diag_add_line(start_pos, bytes([0xf6]), f"{label}null")
        elif additional_info == SIMPLE_FLOAT16:
            float_bytes = self._diag_read_bytes(2)
            all_bytes = self._diag_data[start_pos:self._diag_pos]
            self._diag_add_line(start_pos, all_bytes, f"{label}float16")
        elif additional_info == SIMPLE_FLOAT32:
            float_bytes = self._diag_read_bytes(4)
            value = struct.unpack('>f', float_bytes)[0]
            all_bytes = self._diag_data[start_pos:self._diag_pos]
            self._diag_add_line(start_pos, all_bytes, f"{label}float32({value})")
        elif additional_info == SIMPLE_FLOAT64:
            float_bytes = self._diag_read_bytes(8)
            value = struct.unpack('>d', float_bytes)[0]
            all_bytes = self._diag_data[start_pos:self._diag_pos]
            self._diag_add_line(start_pos, all_bytes, f"{label}float64({value})")
        else:
            self._diag_add_line(start_pos, bytes([0xe0 | additional_info]), f"{label}simple({additional_info})")
    
    def _diag_read_uint(self, additional_info: int) -> Tuple[int, int]:
        """Read uint and return (value, end_position)."""
        if additional_info < 24:
            return additional_info, self._diag_pos
        elif additional_info == 24:
            value = self._diag_data[self._diag_pos]
            self._diag_pos += 1
            return value, self._diag_pos
        elif additional_info == 25:
            value = struct.unpack('>H', self._diag_data[self._diag_pos:self._diag_pos+2])[0]
            self._diag_pos += 2
            return value, self._diag_pos
        elif additional_info == 26:
            value = struct.unpack('>I', self._diag_data[self._diag_pos:self._diag_pos+4])[0]
            self._diag_pos += 4
            return value, self._diag_pos
        elif additional_info == 27:
            value = struct.unpack('>Q', self._diag_data[self._diag_pos:self._diag_pos+8])[0]
            self._diag_pos += 8
            return value, self._diag_pos
        else:
            return 0, self._diag_pos
    
    def _diag_read_bytes(self, n: int) -> bytes:
        """Read n bytes from diag data."""
        result = self._diag_data[self._diag_pos:self._diag_pos + n]
        self._diag_pos += n
        return result
    
    def _diag_add_line(self, offset: int, hex_bytes: bytes, comment: str) -> None:
        """Add formatted line to diagnostic output."""
        offset_str = f"{offset:04x}:"
        
        # Format hex bytes
        if hex_bytes == b'...':
            hex_str = "  ..."
        else:
            hex_parts = []
            for i in range(0, len(hex_bytes), 2):
                chunk = hex_bytes[i:i+2]
                hex_parts.append(chunk.hex())
            hex_str = ' '.join(hex_parts) if hex_parts else ""
        
        # Indent
        indent = self._diag_indent_str * self._diag_current_indent
        
        # Calculate padding for comment alignment
        content_width = len(indent) + len(hex_str)
        comment_column = 48
        padding_needed = max(1, comment_column - content_width)
        padding = ' ' * padding_needed
        
        # Combine
        line = f"{offset_str} {indent}{hex_str}{padding}# {comment}"
        self._diag_lines.append(line)
    
    # ========================================================================
    # PYTHON INTERFACE - Dictionary/List-like access
    # ========================================================================
    
    def __repr__(self) -> str:
        """String representation."""
        return f"CBOR({self.data!r})"
    
    def __str__(self) -> str:
        """String representation as diagnostic dump."""
        try:
            return self.diag()
        except:
            return repr(self.data)
    
    def __getitem__(self, key):
        """Get item from data."""
        return self.data[key]
    
    def __setitem__(self, key, value):
        """Set item in data."""
        self.data[key] = value
        self._cached_bytes = None  # Invalidate cache
    
    def __delitem__(self, key):
        """Delete item from data."""
        del self.data[key]
        self._cached_bytes = None
    
    def __contains__(self, key):
        """Check if key exists in data."""
        return key in self.data if hasattr(self.data, '__contains__') else False
    
    def __len__(self):
        """Get length of data."""
        return len(self.data) if hasattr(self.data, '__len__') else 0
    
    def __iter__(self):
        """Iterate over data."""
        return iter(self.data) if hasattr(self.data, '__iter__') else iter([])
    
    # ========================================================================
    # BUILDER PATTERN METHODS - Fluent API for construction
    # ========================================================================
    
    def set(self, key, value) -> 'CBOR':
        """
        Set a key-value pair (fluent interface).
        
        Args:
            key: Dictionary key or list index
            value: Value to set
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR({}).set("name", "Alice").set("age", 30)
            >>> cbor.data
            {'name': 'Alice', 'age': 30}
        """
        self.data[key] = value
        self._cached_bytes = None
        return self
    
    def append(self, value) -> 'CBOR':
        """
        Append value to array (fluent interface).
        
        Args:
            value: Value to append
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR([]).append(1).append(2).append(3)
            >>> cbor.data
            [1, 2, 3]
        """
        if not isinstance(self.data, list):
            raise TypeError("append() requires data to be a list")
        self.data.append(value)
        self._cached_bytes = None
        return self
    
    def extend(self, values) -> 'CBOR':
        """
        Extend array with multiple values (fluent interface).
        
        Args:
            values: Iterable of values to add
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR([1, 2]).extend([3, 4, 5])
            >>> cbor.data
            [1, 2, 3, 4, 5]
        """
        if not isinstance(self.data, list):
            raise TypeError("extend() requires data to be a list")
        self.data.extend(values)
        self._cached_bytes = None
        return self
    
    def update(self, other: dict = None, **kwargs) -> 'CBOR':
        """
        Update map with another dict (fluent interface).
        
        Args:
            other: Dictionary to merge
            **kwargs: Additional key-value pairs
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR({"a": 1})
            >>> cbor.update({"b": 2}, c=3)
            >>> cbor.data
            {'a': 1, 'b': 2, 'c': 3}
        """
        if not isinstance(self.data, dict):
            raise TypeError("update() requires data to be a dict")
        if other:
            self.data.update(other)
        if kwargs:
            self.data.update(kwargs)
        self._cached_bytes = None
        return self
    
    def delete(self, key) -> 'CBOR':
        """
        Delete a key (fluent interface).
        
        Args:
            key: Key to delete
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR({"a": 1, "b": 2})
            >>> cbor.delete("a")
            >>> cbor.data
            {'b': 2}
        """
        del self.data[key]
        self._cached_bytes = None
        return self
    
    def get(self, key, default=None):
        """
        Get value with default fallback.
        
        Args:
            key: Key to retrieve
            default: Default value if key not found
        
        Returns:
            Value at key or default
        
        Example:
            >>> cbor = CBOR({"name": "Alice"})
            >>> cbor.get("name")
            'Alice'
            >>> cbor.get("age", 0)
            0
        """
        if isinstance(self.data, dict):
            return self.data.get(key, default)
        elif isinstance(self.data, list) and isinstance(key, int):
            try:
                return self.data[key]
            except IndexError:
                return default
        return default
    
    def keys(self):
        """Get dictionary keys."""
        if isinstance(self.data, dict):
            return self.data.keys()
        return []
    
    def values(self):
        """Get dictionary values."""
        if isinstance(self.data, dict):
            return self.data.values()
        return []
    
    def items(self):
        """Get dictionary items."""
        if isinstance(self.data, dict):
            return self.data.items()
        return []
    
    # ========================================================================
    # NESTED ACCESS HELPERS
    # ========================================================================
    
    def get_nested(self, path: str, separator: str = ".", default=None):
        """
        Get nested value using path notation.
        
        Args:
            path: Dot-separated path (e.g., "user.address.city")
            separator: Path separator (default: ".")
            default: Default value if path not found
        
        Returns:
            Value at path or default
        
        Example:
            >>> cbor = CBOR({"user": {"name": "Alice", "address": {"city": "NYC"}}})
            >>> cbor.get_nested("user.address.city")
            'NYC'
            >>> cbor.get_nested("user.age", default=0)
            0
        """
        keys = path.split(separator)
        current = self.data
        
        for key in keys:
            if isinstance(current, dict):
                if key not in current:
                    return default
                current = current[key]
            elif isinstance(current, list):
                try:
                    index = int(key)
                    current = current[index]
                except (ValueError, IndexError):
                    return default
            else:
                return default
        
        return current
    
    def set_nested(self, path: str, value, separator: str = ".", create: bool = True) -> 'CBOR':
        """
        Set nested value using path notation.
        
        Args:
            path: Dot-separated path (e.g., "user.address.city")
            value: Value to set
            separator: Path separator (default: ".")
            create: If True, create intermediate dicts as needed
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR({})
            >>> cbor.set_nested("user.address.city", "NYC")
            >>> cbor.data
            {'user': {'address': {'city': 'NYC'}}}
        """
        keys = path.split(separator)
        current = self.data
        
        # Navigate to parent
        for key in keys[:-1]:
            if isinstance(current, dict):
                if key not in current:
                    if create:
                        current[key] = {}
                    else:
                        raise KeyError(f"Path not found: {key}")
                current = current[key]
            else:
                raise TypeError(f"Cannot navigate through non-dict: {type(current)}")
        
        # Set final value
        final_key = keys[-1]
        if isinstance(current, dict):
            current[final_key] = value
        else:
            raise TypeError(f"Cannot set key on non-dict: {type(current)}")
        
        self._cached_bytes = None
        return self
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def clear(self) -> 'CBOR':
        """
        Clear all data.
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor = CBOR({"a": 1, "b": 2})
            >>> cbor.clear()
            >>> cbor.data
            {}
        """
        if isinstance(self.data, dict):
            self.data.clear()
        elif isinstance(self.data, list):
            self.data.clear()
        self._cached_bytes = None
        return self
    
    def copy(self) -> 'CBOR':
        """
        Create a deep copy of this CBOR object.
        
        Returns:
            New CBOR object with copied data
        
        Example:
            >>> cbor1 = CBOR({"a": 1})
            >>> cbor2 = cbor1.copy()
            >>> cbor2.set("b", 2)
            >>> cbor1.data  # unchanged
            {'a': 1}
        """
        import copy
        return CBOR(copy.deepcopy(self.data))
    
    def merge(self, other: 'CBOR') -> 'CBOR':
        """
        Merge another CBOR object into this one.
        
        Args:
            other: CBOR object to merge
        
        Returns:
            self for method chaining
        
        Example:
            >>> cbor1 = CBOR({"a": 1})
            >>> cbor2 = CBOR({"b": 2})
            >>> cbor1.merge(cbor2)
            >>> cbor1.data
            {'a': 1, 'b': 2}
        """
        if isinstance(self.data, dict) and isinstance(other.data, dict):
            self.data.update(other.data)
        elif isinstance(self.data, list) and isinstance(other.data, list):
            self.data.extend(other.data)
        else:
            raise TypeError("Cannot merge incompatible types")
        self._cached_bytes = None
        return self
    
    def to_dict(self) -> dict:
        """
        Get data as dictionary (alias for .data if it's a dict).
        
        Returns:
            Dictionary representation
        """
        if not isinstance(self.data, dict):
            raise TypeError("Data is not a dictionary")
        return self.data
    
    def to_list(self) -> list:
        """
        Get data as list (alias for .data if it's a list).
        
        Returns:
            List representation
        """
        if not isinstance(self.data, list):
            raise TypeError("Data is not a list")
        return self.data


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def cbor_encode(obj: Any, canonical: bool = False) -> bytes:
    """
    Encode Python object to CBOR bytes.
    
    Args:
        obj: Python object to encode
        canonical: If True, use canonical (deterministic) encoding per RFC 8949 §4.2
    
    Returns:
        CBOR encoded bytes
    """
    return CBOR(obj).encode(canonical=canonical)


def cbor_decode(data: bytes) -> Any:
    """Decode CBOR bytes to Python object."""
    return CBOR.loads(data)


def cbor_diag_dump(data: bytes, indent: str = "  ") -> str:
    """
    Generate diagnostic dump of CBOR data.
    
    Args:
        data: CBOR encoded bytes
        indent: Indentation string for nested structures
    
    Returns:
        Pretty-printed diagnostic dump
    """
    cbor = CBOR.load(data)
    return cbor.diag(indent)


# For backward compatibility
def load_cbor_bytes(data: bytes) -> Any:
    """Decode CBOR bytes (compatibility function)."""
    return cbor_decode(data)
