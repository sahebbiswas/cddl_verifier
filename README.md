# CBOR-CDDL Analyzer and EDN Generator

A Python toolkit for working with CBOR (Concise Binary Object Representation) data and
CDDL (Concise Data Definition Language) schemas. The toolkit covers the full workflow:
encoding and decoding CBOR, validating data against a CDDL schema, generating annotated
EDN (Extended Diagnostic Notation) output, and converting between CBOR and JSON.

## Modules

| Module | Purpose |
|--------|---------|
| `cbor_cddl_analyzer.py` | CDDL schema parser, CBOR validator, annotated EDN generator, CLI tool |
| `simple_cbor.py` | Unified CBOR encoder, decoder, diagnostic dumper, and builder |
| `cbor_json.py` | Bidirectional CBOR ↔ JSON conversion with type preservation |

---

## Installation

```bash
pip install cbor2   # optional — broader CBOR compatibility
```

`cbor2` is optional. When present it is used as the primary decoder; otherwise the
bundled `simple_cbor` module handles everything without external dependencies.

---

## Command-line usage

```bash
# Decode CBOR and print annotated EDN on stdout
python cbor_cddl_analyzer.py schema.cddl data.cbor

# Validate against a named root type, then print annotated EDN
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim-map

# Write EDN to a file
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim-map --output data.edn

# Use readable key names instead of integer indices
python cbor_cddl_analyzer.py schema.cddl data.cbor --edn-format keyname

# Suppress field-name annotations
python cbor_cddl_analyzer.py schema.cddl data.cbor --no-annotate

# Print all parsed CDDL types and exit (useful for debugging schemas)
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types

# Enable verbose logging of type resolution and validation steps
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim-map --verbose
```

### CLI options

| Flag | Description |
|------|-------------|
| `cddl_file` | Path to the CDDL schema file (positional) |
| `cbor_file` | Path to the CBOR binary file (positional) |
| `-o / --output PATH` | Write EDN to a file instead of stdout |
| `-t / --type TYPE` | Root CDDL type name; triggers validation when supplied |
| `--no-annotate` | Suppress field-name comments in EDN output |
| `--edn-format {keyindex,keyname,both}` | EDN key format (default: `keyindex`) |
| `--show-types` | Print all parsed CDDL types and exit |
| `--verbose` | Enable detailed logging of validation and type resolution |

---

## Python API

### Parsing and validating

```python
from cbor_cddl_analyzer import CDDLParser, CBORAnalyzer, EDNGenerator
from simple_cbor import CBOR

# Parse a CDDL schema
cddl = CDDLParser(open("schema.cddl").read())

# Decode a CBOR file
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

### Generating annotated EDN

```python
gen = EDNGenerator(cddl, edn_format="keyindex")
print(gen.generate(data, "person"))
```

### EDN output formats

**`keyindex`** (default) — integer keys with field-name comments on the left:
```edn
/ person / {
  / name / 0: "Alice",
  / age  / 1: 30,
}
```

**`keyname`** — keys replaced by quoted names (IANA registered fields only):
```edn
{
  "name": "Alice",
  "age": 30,
}
```

**`both`** — integer key and name together:
```edn
{
  0 / name /: "Alice",
  1 / age  /: 30,
}
```

See [EDN_FORMATTING_IMPROVEMENTS.md](docs/EDN_FORMATTING_IMPROVEMENTS.md) for details on
annotation placement and the `bytes<N>(...)` wrapper used for nested CBOR fields.

---

## CDDL schema format

The tool supports the IANA registered-parameter syntax used in CoRIM and CoSWID:

```cddl
person = {
  &( name  : 0 ) => tstr,               ; required text field
  &( age   : 1 ) => uint,               ; required unsigned integer
  ? &( email : 2 ) => tstr,             ; optional text field
  &( uuid  : 3 ) => bstr .size 16,      ; exactly 16 bytes
  &( label : 4 ) => tstr .size (1..64), ; 1–64 characters
}
```

### Supported primitive types

| CDDL type | Python type | Notes |
|-----------|-------------|-------|
| `tstr` / `text` | `str` | Optional `.size` constraint |
| `bstr` / `bytes` | `bytes` | Optional `.size` constraint |
| `uint` | `int >= 0` | `bool` rejected (distinct CBOR major type) |
| `int` | `int` | `bool` rejected |
| `bool` | `bool` | `int` rejected |
| `float` / `float16` / `float32` / `float64` | `float` | `int` rejected |
| `null` / `nil` | `None` | |
| `any` | anything | No type check performed |

### Array types

```cddl
tags    = [ * tstr ]   ; zero or more text strings
aliases = [ + tstr ]   ; one or more (empty array fails validation)
```

### CBOR tags

```cddl
tagged-corim = #6.501(unsigned-corim-map)
```

### Nested CBOR (`.cbor` control operator)

When a field is typed as `bytes .cbor inner-type`, the EDN generator automatically
decodes the nested CBOR and renders it inline, wrapped in `bytes<N>(...)` where `N` is
the byte length of the encoded inner value:

```cddl
payload = #6.506(bytes .cbor inner-type)
inner-type = {
  &( value : 0 ) => uint,
}
```

```edn
/ outer-type / 506(
  bytes<4>(
    / inner-type / {
      / value / 0: 42
    }
  )
)
```

### Type choices

```cddl
$kind /= option-a
$kind /= option-b
```

When `--type` resolves to a type choice the tool attempts to auto-select the matching
alternative based on the data structure.

### IANA registered parameters and CoRIM

The analyzer is tested against real CoRIM and CoSWID CDDL schemas.  See
[CORIM_SUPPORT.md](docs/CORIM_SUPPORT.md) for the full list of supported features and
[IANA_ANNOTATIONS_STATUS.md](docs/IANA_ANNOTATIONS_STATUS.md) for annotation behaviour at
every nesting level.

---

## Validation behaviour

| Condition | Result |
|-----------|--------|
| Required field missing | ❌ fail |
| Optional field absent | ✅ pass |
| Optional field present with wrong type | ❌ fail |
| Field value wrong primitive type | ❌ fail |
| `.size` constraint violated | ❌ fail |
| Unknown key not in schema | ❌ fail |
| `[ + type ]` with empty array | ❌ fail |
| Array element wrong type | ❌ fail |

---

## CBOR encoding and decoding — `simple_cbor`

The `simple_cbor` module provides a single `CBOR` class that handles all CBOR
operations without external dependencies.

```python
from simple_cbor import CBOR, cbor_encode, cbor_decode

# Create from Python data
cbor = CBOR({0: "Alice", 1: 30})

# Encode to bytes
raw = cbor.encode()

# Canonical encoding (RFC 8949 §4.2 — deterministic, sorted map keys)
canonical = cbor.encode(canonical=True)

# Decode from bytes
cbor = CBOR.load(raw)      # returns a CBOR object
data = CBOR.loads(raw)     # returns raw Python data

# Convenience functions
raw    = cbor_encode({0: "Alice"})
data   = cbor_decode(raw)
```

### Fluent builder API

```python
cbor = (CBOR({})
        .set(0, "corim-id")
        .set(1, [])
        .update({2: True}))
cbor[1].append(42)
raw = cbor.encode(canonical=True)
```

See [CBOR_BUILDER_QUICK_REF.md](docs/CBOR_BUILDER_QUICK_REF.md) and
[ITERATIVE_CONSTRUCTION.md](docs/ITERATIVE_CONSTRUCTION.md) for the full builder API
including nested access (`get_nested` / `set_nested`), merge, copy, and dict/list
methods.

### Diagnostic dump

`CBOR.diag()` generates a hex-annotated view of the encoded bytes, with each field's
type, offset, and decoded value shown inline. The comment column auto-adjusts to the
widest line in the output, and long strings wrap across multiple lines rather than
being truncated.

```python
print(CBOR({0: "test", 1: 42}).diag())
```

```
0000: a2        # map(2)
0001:   00      # key: uint(0)
0002:   64      # val: text(4)
0003:     74657374  # "test"
0007:   01      # key: uint(1)
0008:   182a    # val: uint(42)
```

See [CBOR_DIAGNOSTIC_DUMP.md](docs/CBOR_DIAGNOSTIC_DUMP.md) for the full output format
reference including byte strings, tags, and special values.

### Canonical encoding

```python
import hashlib
h1 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
h2 = hashlib.sha256(cbor.encode(canonical=True)).hexdigest()
assert h1 == h2   # always identical
```

Canonical encoding is required for CoRIM signing and any application that hashes or
compares CBOR bytes. See [CANONICAL_AND_JSON.md](docs/CANONICAL_AND_JSON.md) for details.

---

## JSON conversion — `cbor_json`

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
    sort_keys: bool = False # sort JSON keys (note: int keys become strings,
                            # so sorting is lexicographic, not numeric)
) -> str
```

> **Lossless round-trips require `typed=True`.**  Without it, `bytes` values are
> reduced to Base64 strings and CBOR tag numbers are discarded.

### Type annotations in JSON

When `typed=True`, CBOR types that have no JSON equivalent are preserved as objects:

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

### CLI

```bash
python cbor_json.py to-json  input.cbor output.json --pretty --typed
python cbor_json.py to-cbor  input.json output.cbor --canonical
```

See [CANONICAL_AND_JSON.md](docs/CANONICAL_AND_JSON.md) for the full API reference and
round-trip examples.

---

## Test suite

162 tests across four files in `tests/`; all pass:

```bash
pytest                                       # requires: pip install pytest
python3 -m unittest discover -s tests -t .  # no extra dependencies
```

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_simple_cbor.py` | 63 | CBOR encode/decode, diagnostics, builder |
| `tests/test_cbor_cddl_analyzer.py` | 48 | CDDL parsing, validation, EDN generation, CoRIM |
| `tests/test_canonical_and_json.py` | 25 | Canonical encoding, JSON conversion, round-trips |
| `tests/test_cbor_builder.py` | 26 | Iterative construction, nested access, merge |

See [TESTING.md](TESTING.md) for individual class/test commands, pytest
configuration, CI/CD workflow examples, and contribution guidelines.

---

## Limitations

- The supported CDDL subset covers practical attestation schemas (CoRIM, CoSWID);
  it does not implement the full RFC 8610 grammar.
- Indefinite-length CBOR items are not supported by the bundled encoder/decoder.