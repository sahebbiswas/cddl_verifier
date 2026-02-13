#!/usr/bin/env python3
"""
Simple CBOR Encoder and Decoder with Diagnostic Dump

A minimal implementation of CBOR encoding and decoding for basic data types.
Supports the core CBOR major types needed for CDDL validation and EDN generation.
Includes diagnostic dump capability for detailed CBOR inspection.

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
from typing import Any, Union, Tuple, Dict, List, Optional


class SimpleCBORDecoder:
    """
    Simple CBOR decoder for basic data types.
    
    Supports:
    - Integers (uint, int)
    - Byte strings
    - Text strings
    - Arrays
    - Maps
    - CBOR tags
    - Booleans, null
    - Floats (16, 32, 64 bit)
    """
    
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.breadcrumb = ""
    
    def decode(self, breadcrumb: str = "") -> Any:
        """Decode CBOR data starting from current position"""
        self.breadcrumb = breadcrumb
        self.pos = 0
        if not self.data:
            raise ValueError("Empty CBOR data")
        return self._decode_item()
    
    def _decode_item(self) -> Any:
        """Decode a single CBOR item"""
        if self.pos >= len(self.data):
            raise ValueError("Unexpected end of data")
        
        initial_byte = self.data[self.pos]
        self.pos += 1
        
        major_type = (initial_byte >> 5) & 0x07
        additional_info = initial_byte & 0x1f
        
        # Decode based on major type
        if major_type == 0:  # Unsigned integer
            return self._decode_uint(additional_info)
        elif major_type == 1:  # Negative integer
            value = self._decode_uint(additional_info)
            return -1 - value
        elif major_type == 2:  # Byte string
            length = self._decode_length(additional_info)
            return self._read_bytes(length)
        elif major_type == 3:  # Text string
            length = self._decode_length(additional_info)
            bytes_data = self._read_bytes(length)
            return bytes_data.decode('utf-8')
        elif major_type == 4:  # Array
            return self._decode_array(additional_info)
        elif major_type == 5:  # Map
            return self._decode_map(additional_info)
        elif major_type == 6:  # Tagged item
            tag_num = self._decode_uint(additional_info)
            tagged_value = self._decode_item()
            return (tag_num, tagged_value)
        elif major_type == 7:  # Simple values
            return self._decode_simple(additional_info)
        else:
            raise ValueError(f"Unknown major type: {major_type}")
    
    def _decode_uint(self, additional_info: int) -> int:
        """Decode an unsigned integer"""
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
        """Decode a length value (for strings and arrays)"""
        if additional_info == 31:
            raise NotImplementedError("Indefinite-length items not supported")
        return self._decode_uint(additional_info)
    
    def _decode_array(self, additional_info: int) -> List[Any]:
        """Decode a CBOR array"""
        length = self._decode_length(additional_info)
        array = []
        for i in range(length):
            array.append(self._decode_item())
        return array
    
    def _decode_map(self, additional_info: int) -> Dict[Any, Any]:
        """Decode a CBOR map"""
        length = self._decode_length(additional_info)
        map_dict = {}
        for i in range(length):
            key = self._decode_item()
            value = self._decode_item()
            map_dict[key] = value
        return map_dict
    
    def _decode_simple(self, additional_info: int) -> Any:
        """Decode simple values (bool, null, float, etc.)"""
        if additional_info == 20:
            return False
        elif additional_info == 21:
            return True
        elif additional_info == 22:
            return None
        elif additional_info == 23:
            raise NotImplementedError("Undefined value not supported")
        elif additional_info == 25:  # Float 16
            bytes_data = self._read_bytes(2)
            # Simple float16 to float32 conversion
            return struct.unpack('>e', bytes_data)[0] if hasattr(struct, 'unpack') else 0.0
        elif additional_info == 26:  # Float 32
            bytes_data = self._read_bytes(4)
            return struct.unpack('>f', bytes_data)[0]
        elif additional_info == 27:  # Float 64
            bytes_data = self._read_bytes(8)
            return struct.unpack('>d', bytes_data)[0]
        else:
            raise ValueError(f"Unknown simple value: {additional_info}")
    
    def _read_bytes(self, n: int) -> bytes:
        """Read n bytes from data"""
        if self.pos + n > len(self.data):
            raise ValueError("Unexpected end of data")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result


class SimpleCBOREncoder:
    """
    Simple CBOR encoder for basic data types.
    
    Supports:
    - Integers (uint, int)
    - Byte strings
    - Text strings
    - Arrays (list, tuple)
    - Maps (dict)
    - CBOR tags (tuple of (tag_num, value))
    - Booleans, None
    """
    
    def encode(self, obj: Any) -> bytes:
        """Encode a Python object to CBOR bytes"""
        return self._encode_item(obj)
    
    def _encode_item(self, obj: Any) -> bytes:
        """Encode a single item"""
        # Handle tagged values (tag_num, value)
        if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], int) and obj[0] >= 0:
            # Could be a tagged value, check if it looks like one
            tag_num, value = obj
            if tag_num < 2**64:  # Valid tag range
                return self._encode_tag(tag_num, value)
        
        # Handle by type
        if obj is None:
            return bytes([0xf6])  # Major type 7, value 22
        elif obj is False:
            return bytes([0xf4])  # Major type 7, value 20
        elif obj is True:
            return bytes([0xf5])  # Major type 7, value 21
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
        """Encode an integer"""
        if value >= 0:
            # Major type 0: unsigned integer
            return self._encode_uint(0, value)
        else:
            # Major type 1: negative integer
            return self._encode_uint(1, -1 - value)
    
    def _encode_uint(self, major_type: int, value: int) -> bytes:
        """Encode an unsigned integer with given major type"""
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
        """Encode a byte string (major type 2)"""
        result = self._encode_uint(2, len(value))
        return result + value
    
    def _encode_string(self, value: str) -> bytes:
        """Encode a text string (major type 3)"""
        utf8_bytes = value.encode('utf-8')
        result = self._encode_uint(3, len(utf8_bytes))
        return result + utf8_bytes
    
    def _encode_array(self, value: Union[list, tuple]) -> bytes:
        """Encode an array (major type 4)"""
        result = self._encode_uint(4, len(value))
        for item in value:
            result += self._encode_item(item)
        return result
    
    def _encode_map(self, value: dict) -> bytes:
        """Encode a map (major type 5)"""
        result = self._encode_uint(5, len(value))
        for key, val in value.items():
            result += self._encode_item(key)
            result += self._encode_item(val)
        return result
    
    def _encode_tag(self, tag_num: int, value: Any) -> bytes:
        """Encode a tagged value (major type 6)"""
        result = self._encode_uint(6, tag_num)
        result += self._encode_item(value)
        return result
    
    def _encode_float(self, value: float) -> bytes:
        """Encode a float (major type 7)"""
        # Use float64 for simplicity
        return bytes([0xfb]) + struct.pack('>d', value)


# Convenience functions
def cbor_encode(obj: Any) -> bytes:
    """Encode Python object to CBOR bytes"""
    encoder = SimpleCBOREncoder()
    return encoder.encode(obj)


def cbor_decode(data: bytes) -> Any:
    """Decode CBOR bytes to Python object"""
    decoder = SimpleCBORDecoder(data)
    return decoder.decode()


# For backward compatibility
def load_cbor_bytes(data: bytes) -> Any:
    """Decode CBOR bytes (compatibility function)"""
    return cbor_decode(data)


class CBORDiagnosticDumper:
    """
    CBOR Diagnostic Dumper - Pretty-printed hex view with type descriptions.
    
    Generates a tree-like hexadecimal view of CBOR data with:
    - Byte offsets
    - Hex bytes with grouping
    - Type descriptions and comments
    - Nested structure visualization
    - Decoded values
    
    Example output:
        0000: a2                              # map(2)
        0001:    00                           #   uint(0) = 
        0002:    63                           #   text(3)
        0003:       626f62                    #     "bob"
        0006:    01                           #   uint(1) = 
        0007:    18 1e                        #   uint(30)
    """
    
    def __init__(self, data: bytes, indent: str = "  "):
        self.data = data
        self.pos = 0
        self.indent_str = indent
        self.lines = []
        self.current_indent = 0
    
    def dump(self) -> str:
        """Generate diagnostic dump of CBOR data"""
        self.pos = 0
        self.lines = []
        self.current_indent = 0
        
        if not self.data:
            return "# Empty CBOR data"
        
        self._dump_item()
        return '\n'.join(self.lines)
    
    def _dump_item(self, label: str = "") -> None:
        """Dump a single CBOR item"""
        if self.pos >= len(self.data):
            self._add_line(self.pos, b'', "# ERROR: Unexpected end of data")
            return
        
        start_pos = self.pos
        initial_byte = self.data[self.pos]
        self.pos += 1
        
        major_type = (initial_byte >> 5) & 0x07
        additional_info = initial_byte & 0x1f
        
        # Dispatch based on major type
        if major_type == 0:  # Unsigned integer
            self._dump_uint(start_pos, initial_byte, additional_info, label)
        elif major_type == 1:  # Negative integer
            self._dump_nint(start_pos, initial_byte, additional_info, label)
        elif major_type == 2:  # Byte string
            self._dump_bstr(start_pos, initial_byte, additional_info, label)
        elif major_type == 3:  # Text string
            self._dump_tstr(start_pos, initial_byte, additional_info, label)
        elif major_type == 4:  # Array
            self._dump_array(start_pos, initial_byte, additional_info, label)
        elif major_type == 5:  # Map
            self._dump_map(start_pos, initial_byte, additional_info, label)
        elif major_type == 6:  # Tagged item
            self._dump_tag(start_pos, initial_byte, additional_info, label)
        elif major_type == 7:  # Simple values
            self._dump_simple(start_pos, initial_byte, additional_info, label)
    
    def _dump_uint(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump unsigned integer"""
        value, end_pos = self._read_uint(additional_info)
        hex_bytes = self.data[start_pos:end_pos]
        comment = f"{label}uint({value})"
        self._add_line(start_pos, hex_bytes, comment)
    
    def _dump_nint(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump negative integer"""
        uint_value, end_pos = self._read_uint(additional_info)
        value = -1 - uint_value
        hex_bytes = self.data[start_pos:end_pos]
        comment = f"{label}nint({value})"
        self._add_line(start_pos, hex_bytes, comment)
    
    def _dump_bstr(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump byte string"""
        length, length_end = self._read_uint(additional_info)
        
        # Header
        header_bytes = self.data[start_pos:length_end]
        self._add_line(start_pos, header_bytes, f"{label}bytes({length})")
        
        # Data (show first 32 bytes if long)
        if length > 0:
            self.current_indent += 1
            data_start = length_end
            data_end = length_end + length
            data_bytes = self.data[data_start:data_end]
            
            if length <= 32:
                self._add_line(data_start, data_bytes, f"h'{data_bytes.hex()}'")
            else:
                # Show first 16 and last 16 bytes
                self._add_line(data_start, data_bytes[:16], f"h'{data_bytes[:16].hex()}'")
                self._add_line(data_start + 16, b'...', f"# ... ({length - 32} more bytes) ...")
                self._add_line(data_end - 16, data_bytes[-16:], f"h'{data_bytes[-16:].hex()}'")
            
            self.current_indent -= 1
            self.pos = data_end
    
    def _dump_tstr(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump text string"""
        length, length_end = self._read_uint(additional_info)
        
        # Header
        header_bytes = self.data[start_pos:length_end]
        self._add_line(start_pos, header_bytes, f"{label}text({length})")
        
        # Data
        if length > 0:
            self.current_indent += 1
            data_start = length_end
            data_end = length_end + length
            data_bytes = self.data[data_start:data_end]
            
            try:
                text = data_bytes.decode('utf-8')
                if length <= 64:
                    self._add_line(data_start, data_bytes, f'"{text}"')
                else:
                    # Truncate long strings
                    self._add_line(data_start, data_bytes[:32], f'"{text[:32]}..."')
            except UnicodeDecodeError:
                self._add_line(data_start, data_bytes, f"# Invalid UTF-8: {data_bytes.hex()}")
            
            self.current_indent -= 1
            self.pos = data_end
    
    def _dump_array(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump array"""
        length, length_end = self._read_uint(additional_info)
        
        # Header
        header_bytes = self.data[start_pos:length_end]
        self._add_line(start_pos, header_bytes, f"{label}array({length})")
        
        # Elements
        if length > 0:
            self.current_indent += 1
            for i in range(length):
                self._dump_item(f"[{i}] ")
            self.current_indent -= 1
    
    def _dump_map(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump map"""
        length, length_end = self._read_uint(additional_info)
        
        # Header
        header_bytes = self.data[start_pos:length_end]
        self._add_line(start_pos, header_bytes, f"{label}map({length})")
        
        # Key-value pairs
        if length > 0:
            self.current_indent += 1
            for i in range(length):
                # Key
                self._dump_item(f"key: ")
                # Value  
                self._dump_item(f"val: ")
            self.current_indent -= 1
    
    def _dump_tag(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump tagged item"""
        tag_num, tag_end = self._read_uint(additional_info)
        
        # Tag header
        header_bytes = self.data[start_pos:tag_end]
        self._add_line(start_pos, header_bytes, f"{label}tag({tag_num})")
        
        # Tagged value
        self.current_indent += 1
        self._dump_item()
        self.current_indent -= 1
    
    def _dump_simple(self, start_pos: int, initial_byte: int, additional_info: int, label: str) -> None:
        """Dump simple values (bool, null, float)"""
        if additional_info == 20:
            self._add_line(start_pos, bytes([initial_byte]), f"{label}false")
        elif additional_info == 21:
            self._add_line(start_pos, bytes([initial_byte]), f"{label}true")
        elif additional_info == 22:
            self._add_line(start_pos, bytes([initial_byte]), f"{label}null")
        elif additional_info == 25:  # Float 16
            float_bytes = self._read_bytes(2)
            all_bytes = self.data[start_pos:self.pos]
            self._add_line(start_pos, all_bytes, f"{label}float16")
        elif additional_info == 26:  # Float 32
            float_bytes = self._read_bytes(4)
            value = struct.unpack('>f', float_bytes)[0]
            all_bytes = self.data[start_pos:self.pos]
            self._add_line(start_pos, all_bytes, f"{label}float32({value})")
        elif additional_info == 27:  # Float 64
            float_bytes = self._read_bytes(8)
            value = struct.unpack('>d', float_bytes)[0]
            all_bytes = self.data[start_pos:self.pos]
            self._add_line(start_pos, all_bytes, f"{label}float64({value})")
        else:
            self._add_line(start_pos, bytes([initial_byte]), f"{label}simple({additional_info})")
    
    def _read_uint(self, additional_info: int) -> Tuple[int, int]:
        """Read unsigned integer and return (value, end_position)"""
        start = self.pos - 1  # Initial byte already consumed
        
        if additional_info < 24:
            return additional_info, self.pos
        elif additional_info == 24:
            value = self.data[self.pos]
            self.pos += 1
            return value, self.pos
        elif additional_info == 25:
            value = struct.unpack('>H', self.data[self.pos:self.pos+2])[0]
            self.pos += 2
            return value, self.pos
        elif additional_info == 26:
            value = struct.unpack('>I', self.data[self.pos:self.pos+4])[0]
            self.pos += 4
            return value, self.pos
        elif additional_info == 27:
            value = struct.unpack('>Q', self.data[self.pos:self.pos+8])[0]
            self.pos += 8
            return value, self.pos
        else:
            return 0, self.pos
    
    def _read_bytes(self, n: int) -> bytes:
        """Read n bytes from data"""
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result
    
    def _add_line(self, offset: int, hex_bytes: bytes, comment: str) -> None:
        """Add a formatted line to output"""
        # Format offset
        offset_str = f"{offset:04x}:"
        
        # Format hex bytes (group by 2)
        if hex_bytes == b'...':
            hex_str = "  ..."
        else:
            hex_parts = []
            for i in range(0, len(hex_bytes), 2):
                chunk = hex_bytes[i:i+2]
                hex_parts.append(chunk.hex())
            hex_str = ' '.join(hex_parts)
        
        # Indent
        indent = self.indent_str * self.current_indent
        
        # Combine
        line = f"{offset_str} {indent}{hex_str:40s} # {comment}"
        self.lines.append(line)


def cbor_diag_dump(data: bytes, indent: str = "  ") -> str:
    """
    Generate a diagnostic dump of CBOR data.
    
    Args:
        data: CBOR encoded bytes
        indent: Indentation string for nested structures (default: "  ")
    
    Returns:
        Pretty-printed diagnostic dump showing hex bytes and structure
    
    Example:
        >>> data = cbor_encode({0: "test", 1: 42})
        >>> print(cbor_diag_dump(data))
        0000: a2                                       # map(2)
        0001:   00                                     #   key: uint(0)
        0002:   64                                     #   val: text(4)
        0003:     74657374                             #     "test"
        0007:   01                                     #   key: uint(1)
        0008:   18 2a                                  #   val: uint(42)
    """
    dumper = CBORDiagnosticDumper(data, indent)
    return dumper.dump()

