# Python API reference

The toolkit has three importable modules:

| Module | Main entry points |
|--------|-------------------|
| [`cbor_cddl_analyzer`](#cbor_cddl_analyzer-validation-and-edn) | `CDDLParser`, `CBORAnalyzer`, `EDNGenerator` |
| [`simple_cbor`](#simple_cbor-encoding-and-decoding) | `CBOR`, `cbor_encode`, `cbor_decode`, `cbor_diag_dump`, `CBORDecodeError` |
| [`cbor_json`](#cbor_json-json-conversion) | `cbor_to_json`, `json_to_cbor`, file helpers |

The project version is available as `simple_cbor.__version__` (defined in `_version.py`).

---

## `cbor_cddl_analyzer`: validation and EDN

### Parsing and validating

```python
from cbor_cddl_analyzer import CDDLParser, CBORAnalyzer
from simple_cbor import CBOR

# Parse a CDDL schema
cddl = CDDLParser(open("schema.cddl").read())

# Decode a CBOR file (must hold exactly one CBOR item)
cbor_bytes = open("data.cbor", "rb").read()
data = CBOR.loads(cbor_bytes)

# Validate
analyzer = CBORAnalyzer(cddl)
if analyzer.validate(data, "person"):
    print("Valid")
else:
    for err in analyzer.get_errors():
        print(err)
```

The supported schema syntax and validation rules are described in
[CDDL_SUPPORT.md](CDDL_SUPPORT.md).

### Generating annotated EDN

```python
from cbor_cddl_analyzer import EDNGenerator

gen = EDNGenerator(cddl, edn_format="keyindex")   # or "keyname", "both"
print(gen.generate(data, "person"))
```

The output formats are shown in [CLI.md](CLI.md#edn-output-formats).

---

## `simple_cbor`: encoding and decoding

`simple_cbor` provides a single `CBOR` class for all CBOR operations, with no
external dependencies.

```python
from simple_cbor import CBOR, cbor_encode, cbor_decode

# Create from Python data
cbor = CBOR({0: "Alice", 1: 30})

# Encode to bytes
raw = cbor.encode()

# Canonical encoding (RFC 8949 §4.2: deterministic, sorted map keys)
canonical = cbor.encode(canonical=True)

# Decode from bytes
cbor = CBOR.load(raw)      # returns a CBOR object
data = CBOR.loads(raw)     # returns plain Python data

# Convenience functions
raw  = cbor_encode({0: "Alice"})
data = cbor_decode(raw)
```

Decoded values map to Python as follows: integers to `int`, byte strings to
`bytes`, text to `str`, arrays to `list`, maps to `dict` (array or map keys become
tuples so they are hashable), tags to `(tag_number, value)` tuples, and
`true` / `false` / `null` / floats to their Python equivalents.

### Strict single-item decoding

`CBOR.load()`, `CBOR.loads()`, `cbor_decode()` and `load_cbor_bytes()` accept
`bytes`, `bytearray` or `memoryview`, and must consume **exactly one complete
CBOR data item**. Anything else raises `CBORDecodeError`, a `ValueError`
subclass. Its `offset` attribute is the byte offset where the problem was
detected, and `reason` holds the message without the offset.

| Exception | Raised for | `offset` |
|-----------|------------|----------|
| `CBORTruncatedError` | Empty input; input ending mid-item | Where more bytes were needed (`0` for empty input) |
| `CBORTrailingDataError` | Bytes left after the item | First trailing byte |
| `CBORUnsupportedError` | Indefinite-length items, `undefined` (also a `NotImplementedError`) | Initial byte of the item |
| `CBORDecodeError` | Reserved additional info, invalid UTF-8, duplicate map keys, nesting too deep | Offending byte or key |

A non-bytes argument raises `TypeError`.

```python
from simple_cbor import CBORTrailingDataError, cbor_decode

try:
    cbor_decode(b"\x01\x02")          # two items: a CBOR sequence
except CBORTrailingDataError as e:
    print(e.offset)                   # 1
    print(e)                          # Trailing data after CBOR item: 1 extra byte(s) (at offset 1)
```

CBOR sequences (RFC 8742) are deliberately **not** accepted by these functions;
a separate sequence-decoding API (for `.cborseq`) is planned. Nested
`.cbor`-controlled byte strings in the analyzer are decoded with the same strict
rules.

### Builder API

```python
cbor = (CBOR({})
        .set(0, "corim-id")
        .set(1, [])
        .update({2: True}))
cbor[1].append(42)
raw = cbor.encode(canonical=True)
```

See [CBOR_BUILDER_QUICK_REF.md](CBOR_BUILDER_QUICK_REF.md) and
[ITERATIVE_CONSTRUCTION.md](ITERATIVE_CONSTRUCTION.md) for the full builder API,
including nested access (`get_nested` / `set_nested`), merge, copy, and dict/list
methods.

### Diagnostic dump

`CBOR.diag()` (or `cbor_diag_dump(raw)`) produces a hex view of the encoded bytes,
with each item's type, offset and decoded value shown inline. The comment column
adjusts to the widest line, and long strings wrap rather than being truncated.
Unlike the decoder, the dump is lenient: malformed input is shown with
`# ERROR:` lines instead of raising.

```python
print(CBOR({0: "test", 1: 42}).diag())
```

```
0000: a2                                # map(2)
0001:   00                              # key: uint(0)
0002:   64                              # val: text(4)
0003:     7465 7374                     # "test"
0007:   01                              # key: uint(1)
0008:   182a                            # val: uint(42)
```

See [CBOR_DIAGNOSTIC_DUMP.md](CBOR_DIAGNOSTIC_DUMP.md) for the full output format.

### Canonical encoding

```python
import hashlib
h1 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
h2 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
assert h1 == h2   # always identical
```

Canonical encoding is needed for CoRIM signing and anywhere CBOR bytes are hashed
or compared. See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md).

---

## `cbor_json`: JSON conversion

```python
from cbor_json import cbor_to_json, json_to_cbor

# CBOR → JSON
json_str = cbor_to_json(raw, pretty=True)

# With CBOR-type annotations (required for lossless round-trips)
json_str = cbor_to_json(raw, typed=True, pretty=True)

# JSON → CBOR
raw = json_to_cbor(json_str)
raw = json_to_cbor(json_str, canonical=True)
```

`cbor_to_json` signature:

```python
cbor_to_json(
    cbor_bytes: bytes,
    typed: bool = False,    # preserve bytes/tag types as JSON annotations
    pretty: bool = False,   # pretty-print with indentation
    indent: int = 2,
    sort_keys: bool = False # sort JSON keys (int keys become strings,
                            # so sorting is lexicographic, not numeric)
) -> str
```

`cbor_bytes` is decoded with the strict decoder above, so it must hold exactly
one CBOR item.

> **Lossless round-trips require `typed=True`.** Without it, `bytes` values are
> reduced to Base64 strings and CBOR tag numbers are discarded.

### Type annotations in JSON

With `typed=True`, CBOR values that have no JSON equivalent are kept as objects:

| CBOR value | JSON representation |
|-----------|---------------------|
| `b'\x01\x02'` | `{"$cbor": "bytes", "$value": "AQI="}` |
| `(32, "https://…")` | `{"$cbor": "tag", "$tag": 32, "$value": "https://…"}` |
| `float('nan')` | `{"$cbor": "NaN"}` |
| `float('inf')` | `{"$cbor": "Infinity"}` |

### File conversion

```python
from cbor_json import cbor_file_to_json_file, json_file_to_cbor_file

cbor_file_to_json_file("data.cbor", "data.json", pretty=True, typed=True)
json_file_to_cbor_file("data.json", "data.cbor", canonical=True)
```

See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md) for round-trip examples.
