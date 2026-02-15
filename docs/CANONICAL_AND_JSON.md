# Canonical Encoding & JSON Conversion Guide

## Overview

This guide covers two critical features added to the CBOR-CDDL Analyzer toolkit:
1. **Canonical CBOR Encoding** (RFC 8949 §4.2) - Deterministic encoding for security
2. **JSON ↔ CBOR Conversion** - Bidirectional conversion with type preservation

## Canonical CBOR Encoding

### What is Canonical Encoding?

Canonical encoding produces deterministic CBOR output - the same data always encodes to the same bytes. This is essential for:
- **Digital signatures** - Sign consistent bytes
- **Hash verification** - Compute reliable hashes  
- **Blockchain/DLT** - Ensure data integrity
- **CoRIM signing** - Standards compliance

### RFC 8949 Rules

Canonical encoding follows these rules:
1. **Shortest encoding** - Integers use minimal bytes
2. **Sorted map keys** - Keys sorted by encoded byte comparison
3. **Definite-length** - No indefinite-length items
4. **No duplicates** - Map keys must be unique

### Usage

```python
from simple_cbor import CBOR, cbor_encode

# Standard encoding
data = {"z": 1, "a": 2, "m": 3}
normal = cbor_encode(data)

# Canonical encoding (sorted keys)
canonical = cbor_encode(data, canonical=True)

# For signing
import hashlib
hash_value = hashlib.sha256(canonical).hexdigest()
```

### Using the CBOR Class

```python
cbor = CBOR(data)

# Standard encoding
standard_bytes = cbor.encode()

# Canonical encoding
canonical_bytes = cbor.encode(canonical=True)

# Always produces same hash
hash1 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
hash2 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
assert hash1 == hash2  # ✅ Deterministic!
```

### Key Sorting Example

```python
data = {
    "zebra": 1,
    "apple": 2,
    "mango": 3,
}

# Canonical encoding sorts keys by their encoded representation
canonical = cbor_encode(data, canonical=True)

# Keys will be ordered: "apple" < "mango" < "zebra"
# Based on encoded bytes: 0x61 0x61... < 0x61 0x6d... < 0x61 0x7a...
```

### Use Cases

#### 1. Digital Signatures (CoRIM)
```python
# Encode CoRIM data canonically
corim_data = {
    "id": "corim-12345",
    "tags": [...],
    ...
}

# Canonical encoding for signing
canonical_cbor = cbor_encode(corim_data, canonical=True)

# Sign the canonical bytes
signature = sign(canonical_cbor, private_key)

# Verification always works because encoding is deterministic
assert verify(canonical_cbor, signature, public_key)
```

#### 2. Content Addressing
```python
# Create content hash for deduplication
canonical_bytes = cbor_encode(document, canonical=True)
content_id = hashlib.sha256(canonical_bytes).hexdigest()

# Same document always produces same ID
```

#### 3. Merkle Trees
```python
# Build Merkle tree with canonical encoding
def hash_node(data):
    canonical = cbor_encode(data, canonical=True)
    return hashlib.sha256(canonical).digest()

# Deterministic tree construction
```

---

## JSON ↔ CBOR Conversion

### Overview

Convert between JSON and CBOR formats with support for CBOR-specific types.

**Features:**
- Bidirectional conversion
- Type preservation (bytes, tags)
- Pretty-printing
- CLI tool included
- Handles special values (NaN, Infinity)

### JSON to CBOR

#### Basic Conversion

```python
from cbor_json import json_to_cbor

json_str = '{"name": "test", "value": 42}'
cbor_bytes = json_to_cbor(json_str)

# With canonical encoding
cbor_bytes = json_to_cbor(json_str, canonical=True)
```

#### With Type Annotations

JSON doesn't have bytes or tag types. Use annotations:

```python
json_str = '''{
    "data": {
        "$cbor": "bytes",
        "$value": "AQIDBA=="
    },
    "uri": {
        "$cbor": "tag",
        "$tag": 32,
        "$value": "http://example.com"
    }
}'''

cbor_bytes = json_to_cbor(json_str)
# Converts to: {"data": b'\x01\x02\x03\x04', "uri": (32, "http://example.com")}
```

### CBOR to JSON

#### Basic Conversion

```python
from cbor_json import cbor_to_json
from simple_cbor import cbor_encode

data = {"name": "test", "items": [1, 2, 3]}
cbor_bytes = cbor_encode(data)

# Convert to JSON
json_str = cbor_to_json(cbor_bytes)
print(json_str)
# Output: {"name": "test", "items": [1, 2, 3]}
```

#### With Type Preservation

```python
data = {
    "text": "hello",
    "bytes": b'\x01\x02\x03',
    "uri": (32, "http://example.com")
}

cbor_bytes = cbor_encode(data)

# Without type annotations (lossy)
json_str = cbor_to_json(cbor_bytes, typed=False)
# bytes → base64 string
# tags → just the value

# With type annotations (preserves types)
json_str = cbor_to_json(cbor_bytes, typed=True)
# bytes → {"$cbor": "bytes", "$value": "AQID"}
# tags → {"$cbor": "tag", "$tag": 32, "$value": "..."}
```

#### Pretty Printing

```python
# Pretty-printed JSON
json_str = cbor_to_json(cbor_bytes, pretty=True, indent=4)
print(json_str)
```

Output:
```json
{
    "name": "test",
    "items": [
        1,
        2,
        3
    ]
}
```

### File Conversion

```python
from cbor_json import cbor_file_to_json_file, json_file_to_cbor_file

# CBOR file to JSON file
cbor_file_to_json_file('data.cbor', 'data.json', pretty=True)

# JSON file to CBOR file
json_file_to_cbor_file('data.json', 'data.cbor', canonical=True)
```

### CLI Usage

#### Convert CBOR to JSON

```bash
# Basic conversion
python cbor_json.py to-json input.cbor output.json

# With pretty printing
python cbor_json.py to-json input.cbor output.json --pretty

# With type preservation
python cbor_json.py to-json input.cbor output.json --pretty --typed

# Custom indentation
python cbor_json.py to-json input.cbor output.json --pretty --indent 4

# Using stdin/stdout
cat data.cbor | python cbor_json.py to-json - -
```

#### Convert JSON to CBOR

```bash
# Basic conversion
python cbor_json.py to-cbor input.json output.cbor

# With canonical encoding
python cbor_json.py to-cbor input.json output.cbor --canonical

# Using stdin/stdout
echo '{"test": 1}' | python cbor_json.py to-cbor - output.cbor
```

### Type Annotations Reference

#### Bytes Type

```json
{
    "$cbor": "bytes",
    "$value": "base64-encoded-data"
}
```

Converts to Python `bytes` object.

#### Tagged Value Type

```json
{
    "$cbor": "tag",
    "$tag": 32,
    "$value": "http://example.com"
}
```

Converts to Python tuple `(32, "http://example.com")`.

#### Special Float Values

```json
{"$cbor": "NaN"}          // float('nan')
{"$cbor": "Infinity"}     // float('inf')
{"$cbor": "-Infinity"}    // float('-inf')
```

### Round-Trip Conversion

```python
from simple_cbor import cbor_encode, cbor_decode
from cbor_json import cbor_to_json, json_to_cbor

# Original data with CBOR types
original = {
    "text": "hello",
    "bytes": b'\x01\x02\x03',
    "tag": (32, "uri")
}

# CBOR → JSON (typed) → CBOR
cbor1 = cbor_encode(original)
json_str = cbor_to_json(cbor1, typed=True)  # ⚠️ typed=True required!
cbor2 = json_to_cbor(json_str)

# Verify round-trip
assert cbor_decode(cbor1) == cbor_decode(cbor2)
```

**Important:** Use `typed=True` when converting CBOR to JSON if you plan to convert back!

### Use Cases

#### 1. API Development

```python
# REST API that uses CBOR internally
def api_endpoint(request):
    # Receive JSON from client
    json_data = request.body
    cbor_bytes = json_to_cbor(json_data)
    
    # Process as CBOR internally
    data = cbor_decode(cbor_bytes)
    result = process(data)
    
    # Return JSON to client
    response_cbor = cbor_encode(result)
    response_json = cbor_to_json(response_cbor, pretty=True)
    return response_json
```

#### 2. Debugging CBOR Files

```bash
# Inspect CBOR file as JSON
python cbor_json.py to-json unknown.cbor - --pretty | less

# Edit and convert back
python cbor_json.py to-json data.cbor data.json --pretty --typed
# Edit data.json
python cbor_json.py to-cbor data.json modified.cbor
```

#### 3. Testing

```python
# Generate test data in JSON, use in CBOR
test_cases = json.load(open('test_cases.json'))
for case in test_cases:
    cbor_bytes = json_to_cbor(json.dumps(case))
    result = validate_cbor(cbor_bytes)
    assert result.valid
```

#### 4. Documentation

```markdown
# API Documentation

## Request Format (CBOR)

JSON equivalent:
```json
{
    "method": "create",
    "params": {
        "name": "test"
    }
}
```

Actual CBOR: (send above JSON converted to CBOR)
```

### Limitations

1. **JSON has no bytes type** - Must use annotations or lose type info
2. **JSON has no tuple type** - Tagged values become objects  
3. **JSON has limited numbers** - Very large integers may lose precision
4. **Indefinite-length not supported** - Our CBOR library doesn't support it

### Best Practices

1. **Use `typed=True` for round-trips** - Preserves CBOR types
2. **Use `canonical=True` for security** - Deterministic encoding
3. **Pretty-print for humans** - Makes JSON readable
4. **Validate after conversion** - Check data integrity
5. **Document type annotations** - Make schemas clear

---

## Examples

### Complete Example: Secure Data Exchange

```python
from simple_cbor import CBOR, cbor_encode, cbor_decode
from cbor_json import cbor_to_json, json_to_cbor
import hashlib
import hmac

# 1. Create data
data = {
    "user": "alice",
    "action": "transfer",
    "amount": 100.50,
    "timestamp": 1234567890
}

# 2. Encode canonically (for security)
canonical_cbor = cbor_encode(data, canonical=True)

# 3. Compute MAC for integrity
secret_key = b"secret"
mac = hmac.new(secret_key, canonical_cbor, hashlib.sha256).digest()

# 4. Package with MAC
package = {
    "data": data,
    "mac": mac
}

# 5. Convert to JSON for transport
json_str = cbor_to_json(cbor_encode(package), typed=True, pretty=True)

# Send json_str over network...

# 6. Receive and verify
received_package = cbor_decode(json_to_cbor(json_str))
received_data = received_package["data"]
received_mac = received_package["mac"]

# 7. Recompute MAC
recomputed_cbor = cbor_encode(received_data, canonical=True)
expected_mac = hmac.new(secret_key, recomputed_cbor, hashlib.sha256).digest()

# 8. Verify integrity
assert received_mac == expected_mac, "MAC verification failed!"
print("✅ Data verified!")
```

### Complete Example: CoRIM Workflow

```python
# Create CoRIM data
corim = {
    "id": "example.corim.001",
    "tags": [
        (506, {  # Tag 506 = CoMID
            0: "comid-001",
            1: [...],
        })
    ]
}

# 1. Encode canonically for signing
canonical = cbor_encode(corim, canonical=True)

# 2. Sign
signature = sign_data(canonical, private_key)

# 3. Create signed CoRIM
signed_corim = (501, corim)  # Tag 501 = unsigned-corim-map

# 4. Save as CBOR
with open('corim.cbor', 'wb') as f:
    f.write(cbor_encode(signed_corim))

# 5. Also export as JSON for inspection
json_str = cbor_to_json(
    cbor_encode(signed_corim),
    typed=True,
    pretty=True
)
with open('corim.json', 'w') as f:
    f.write(json_str)

print("✅ CoRIM created and exported!")
```

---

## Testing

Both features have comprehensive test coverage:

```bash
# Run tests
python test_canonical_and_json.py

# Test results:
# ✅ 25 tests passing
# - 6 canonical encoding tests
# - 6 JSON to CBOR tests
# - 6 CBOR to JSON tests
# - 4 round-trip tests
# - 4 edge case tests
```

---

## API Reference

### Canonical Encoding

```python
# Function
cbor_encode(obj, canonical=False) -> bytes

# Method
CBOR(data).encode(canonical=False) -> bytes
```

### JSON Conversion

```python
# CBOR to JSON
cbor_to_json(cbor_bytes, typed=False, pretty=False, indent=2) -> str

# JSON to CBOR
json_to_cbor(json_str, canonical=False) -> bytes

# File conversion
cbor_file_to_json_file(cbor_path, json_path, typed=False, pretty=True)
json_file_to_cbor_file(json_path, cbor_path, canonical=False)
```

---

## Summary

**Canonical Encoding:**
- ✅ RFC 8949 compliant
- ✅ Deterministic output
- ✅ Perfect for security applications
- ✅ Essential for signing and hashing

**JSON Conversion:**
- ✅ Bidirectional JSON ↔ CBOR
- ✅ Type preservation with annotations
- ✅ CLI tool included
- ✅ Perfect for API development and debugging

Both features are production-ready with comprehensive tests!