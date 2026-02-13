# Simple CBOR Module - Diagnostic Dump Feature

## Overview

The `simple_cbor` module now includes a powerful diagnostic dump feature that provides a pretty-printed, tree-like hex view of CBOR data with type descriptions.

## Features

✅ **Hex View** - Byte-by-byte hexadecimal representation  
✅ **Offset Display** - Shows byte offsets for easy navigation  
✅ **Type Annotations** - Detailed CBOR type descriptions  
✅ **Tree Structure** - Indented view of nested data  
✅ **Value Display** - Shows decoded values inline  
✅ **Grouped Bytes** - Hex bytes grouped by 2 for readability

## Usage

### Basic Example

```python
from simple_cbor import cbor_encode, cbor_diag_dump

# Encode some data
data = {0: "test", 1: 42}
cbor_bytes = cbor_encode(data)

# Generate diagnostic dump
dump = cbor_diag_dump(cbor_bytes)
print(dump)
```

**Output:**
```
0000: a2                                       # map(2)
0001:   00                                       # key: uint(0)
0002:   64                                       # val: text(4)
0003:     7465 7374                                # "test"
0007:   01                                       # key: uint(1)
0008:   18 2a                                    # val: uint(42)
```

### Complex Nested Structure

```python
data = {
    0: "corim-id",
    1: [1, 2, 3],
    2: {
        0: True,
        1: False
    },
    3: (32, "http://example.com")  # Tagged value
}

print(cbor_diag_dump(cbor_encode(data)))
```

**Output:**
```
0000: a4                                       # map(4)
0001:   00                                       # key: uint(0)
0002:   68                                       # val: text(8)
0003:     636f 7269 6d2d 6964                      # "corim-id"
000b:   01                                       # key: uint(1)
000c:   83                                       # val: array(3)
000d:     01                                       # [0] uint(1)
000e:     02                                       # [1] uint(2)
000f:     03                                       # [2] uint(3)
0010:   02                                       # key: uint(2)
0011:   a2                                       # val: map(2)
0012:     00                                       # key: uint(0)
0013:     f5                                       # val: true
0014:     01                                       # key: uint(1)
0015:     f4                                       # val: false
0016:   03                                       # key: uint(3)
0017:   d8 20                                    # val: tag(32)
0019:     73                                       # text(19)
001a:       6874 7470 3a2f 2f65 7861 6d70 6c65 2e63 # "http://example.c"
           6f6d                                    # "om"
```

### Custom Indentation

```python
# Use 4 spaces for indentation instead of default 2
dump = cbor_diag_dump(cbor_bytes, indent="    ")
```

## Output Format

Each line follows this format:

```
<offset>: <indent><hex_bytes>                  # <comment>
```

**Components:**
- `<offset>` - 4-digit hex byte offset (e.g., `0000:`, `001a:`)
- `<indent>` - Spaces indicating nesting level
- `<hex_bytes>` - Hexadecimal bytes grouped by 2 (e.g., `a2`, `18 2a`)
- `<comment>` - Type description and decoded value

## Type Descriptions

### Integers
```
0000: 00                                       # uint(0)
0001: 01                                       # uint(1)
0002: 18 2a                                    # uint(42)
0005: 20                                       # nint(-1)
0006: 38 63                                    # nint(-100)
```

### Strings
```
0000: 60                                       # text(0)
0001: 65                                       # text(5)
0002:   6865 6c6c 6f                             # "hello"
```

### Byte Strings
```
0000: 43                                       # bytes(3)
0001:   0102 03                                  # h'010203'
```

### Arrays
```
0000: 83                                       # array(3)
0001:   01                                       # [0] uint(1)
0002:   02                                       # [1] uint(2)
0003:   03                                       # [2] uint(3)
```

### Maps
```
0000: a2                                       # map(2)
0001:   00                                       # key: uint(0)
0002:   01                                       # val: uint(1)
0003:   02                                       # key: uint(2)
0004:   03                                       # val: uint(3)
```

### Tagged Values
```
0000: d8 20                                    # tag(32)
0002:   65                                       # text(5)
0003:     6865 6c6c 6f                             # "hello"
```

### Booleans and Null
```
0000: f4                                       # false
0001: f5                                       # true
0002: f6                                       # null
```

### Floats
```
0000: fa                                       # float32(3.14)
      4048 f5c3
0005: fb                                       # float64(3.14159265359)
      4009 21fb 5444 2d18
```

## Special Handling

### Long Byte Strings

Byte strings longer than 32 bytes are truncated in the display:

```
0000: 58 64                                    # bytes(100)
0002:   0000 0000 0000 0000 0000 0000 0000 0000  # h'00000000000000000000000000000000'
0012:   ...                                      # ... (68 more bytes) ...
0062:   0000 0000 0000 0000 0000 0000 0000 0000  # h'00000000000000000000000000000000'
```

### Long Text Strings

Text strings longer than 64 characters are truncated:

```
0000: 78 c8                                    # text(200)
0002:   4c6f 7265 6d20 6970 7375 6d20 646f 6c6f  # "Lorem ipsum dolo"
        ...
```

### Invalid UTF-8

If a text string contains invalid UTF-8, it shows the hex:

```
0000: 62                                       # text(2)
0001:   ff fe                                    # Invalid UTF-8: fffe
```

## Use Cases

### 1. Debugging CBOR Data

Quickly inspect CBOR files to understand their structure:

```python
with open('data.cbor', 'rb') as f:
    cbor_data = f.read()

print(cbor_diag_dump(cbor_data))
```

### 2. Validating Encodings

Verify that your CBOR encoder produces the expected byte sequence:

```python
expected = b'\xa1\x00\x01'  # map with {0: 1}
actual = cbor_encode({0: 1})

print("Expected:")
print(cbor_diag_dump(expected))
print("\nActual:")
print(cbor_diag_dump(actual))
```

### 3. Learning CBOR Format

Understand how different data types are encoded:

```python
examples = {
    "small int": 0,
    "large int": 1000,
    "negative": -100,
    "text": "hello",
    "bytes": b'\x01\x02',
    "array": [1, 2, 3],
    "map": {0: "a", 1: "b"},
    "tagged": (32, "uri"),
    "bool": True,
    "null": None
}

for name, value in examples.items():
    print(f"\n{name}:")
    print(cbor_diag_dump(cbor_encode(value)))
```

### 4. Protocol Analysis

Analyze CBOR-based protocols like CoRIM, CoSWID, COSE:

```python
# CoRIM example
corim_data = cbor_encode((501, {  # Tag 501
    0: "corim-id",
    1: [(506, nested_cbor)],  # Tag 506
}))

print(cbor_diag_dump(corim_data))
```

## API Reference

### Function: `cbor_diag_dump`

```python
def cbor_diag_dump(data: bytes, indent: str = "  ") -> str
```

**Parameters:**
- `data` (bytes) - CBOR encoded data
- `indent` (str) - Indentation string for nested structures (default: `"  "`)

**Returns:**
- `str` - Pretty-printed diagnostic dump

**Raises:**
- May include error messages in output for malformed CBOR

### Class: `CBORDiagnosticDumper`

```python
class CBORDiagnosticDumper:
    def __init__(self, data: bytes, indent: str = "  ")
    def dump(self) -> str
```

**Usage:**
```python
dumper = CBORDiagnosticDumper(cbor_bytes, indent="    ")
output = dumper.dump()
```

## Comparison with Other Tools

### vs. `cbor2.loads()` with pretty print

**simple_cbor diagnostic dump:**
- Shows exact byte layout
- Includes byte offsets
- Shows hex representation
- Tree structure visualization
- Type annotations inline

**cbor2.loads() + pprint:**
- Shows decoded Python objects only
- No hex representation
- No byte offsets
- No CBOR type information

### vs. hex dump utilities

**simple_cbor diagnostic dump:**
- CBOR-aware structure
- Type descriptions
- Decoded values
- Proper nesting visualization

**hexdump:**
- Raw bytes only
- No CBOR awareness
- Manual interpretation needed

## Examples Gallery

### CoRIM Structure
```
0000: d9 01f5                                  # tag(501)
0003:   a2                                       # map(2)
0004:     00                                       # key: uint(0)
0005:     6d                                       # val: text(13)
0006:       7465 7374 2d63 6f72 696d 2d69 64       # "test-corim-id"
0013:     01                                       # key: uint(1)
0014:     81                                       # val: array(1)
0015:       d901 fa                                  # [0] tag(506)
0018:         44                                       # bytes(4)
0019:           a100 182a                                # h'a100182a'
```

### COSE Sign1
```
0000: d2                                       # tag(18)
0001:   84                                       # array(4)
0002:     43                                       # [0] bytes(3)
0003:       a100 01                                  # h'a10001'
0006:     a0                                       # [1] map(0)
0007:     58 20                                    # [2] bytes(32)
0009:       0000 0000 0000 0000 0000 0000 0000 0000  # h'00000000...'
           0000 0000 0000 0000 0000 0000 0000 0000
0029:     58 40                                    # [3] bytes(64)
002b:       0000 0000 0000 0000 0000 0000 0000 0000  # signature
           ...
```

## Tips

1. **Save to file** - For large dumps:
   ```python
   with open('dump.txt', 'w') as f:
       f.write(cbor_diag_dump(data))
   ```

2. **Compare dumps** - Use diff tools:
   ```bash
   diff <(python3 -c "from simple_cbor import *; print(cbor_diag_dump(...))")      <(python3 -c "from simple_cbor import *; print(cbor_diag_dump(...))")
   ```

3. **Pipe to less** - For interactive viewing:
   ```python
   import subprocess
   proc = subprocess.Popen(['less'], stdin=subprocess.PIPE)
   proc.communicate(cbor_diag_dump(data).encode())
   ```

## Summary

The diagnostic dump feature makes CBOR data:
- ✅ **Readable** - Human-friendly hex and type annotations
- ✅ **Debuggable** - Byte offsets for precise location
- ✅ **Educational** - Learn CBOR encoding by example
- ✅ **Comprehensive** - Handles all CBOR types
- ✅ **Practical** - Essential for protocol development

Perfect for debugging, learning, and analyzing CBOR-based protocols!
