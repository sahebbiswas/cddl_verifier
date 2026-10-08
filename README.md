[![Python CI](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml/badge.svg)](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml)

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
| `_version.py` | Single source of truth for the project version |

**Current version: 0.2.0** — see [CHANGELOG.md](CHANGELOG.md) and
[Versioning](#versioning).

---

## Installation

```bash
pip install cbor2   # optional — broader CBOR compatibility
```

`cbor2` is optional. The analyzer CLI and the `simple_cbor` API always decode
with the bundled strict decoder; `cbor2` is only tried by the `load_cbor_file()`
helper. No other external dependencies are needed.

The project can also be installed as a package, which adds a
`cbor-cddl-analyzer` console script:

```bash
pip install .            # or: pip install ".[cbor2]"
cbor-cddl-analyzer --version
```

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

# Print the tool version
python cbor_cddl_analyzer.py --version
```

The CBOR file must contain **exactly one** CBOR data item. An empty file, a
truncated item, or extra bytes after the item (for example a concatenated CBOR
sequence) makes the CLI exit with status 1 and an error that names the byte
offset, e.g. `Error decoding CBOR: Trailing data after CBOR item: 1 extra byte(s) (at offset 1)`.

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
| `--version` | Print the version and exit |

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

### Strict single-item decoding

`CBOR.load()`, `CBOR.loads()`, `cbor_decode()` and `load_cbor_bytes()` accept
`bytes`, `bytearray` or `memoryview` and must consume **exactly one complete
CBOR data item**. Anything else raises `CBORDecodeError`, a `ValueError`
subclass whose `offset` attribute is the byte offset where the problem was
detected (`reason` holds the message without the offset):

| Exception | Raised for | `offset` |
|-----------|------------|----------|
| `CBORTruncatedError` | Empty input; input ending mid-item | Where more bytes were needed (`0` for empty input) |
| `CBORTrailingDataError` | Bytes left after the item | First trailing byte |
| `CBORUnsupportedError` | Indefinite-length items, `undefined` (also a `NotImplementedError`) | Initial byte of the item |
| `CBORDecodeError` | Reserved additional info, invalid UTF-8, duplicate map keys, nesting too deep | Offending byte / key |

A non-bytes argument raises `TypeError`.

```python
from simple_cbor import CBORDecodeError, CBORTrailingDataError, cbor_decode

try:
    cbor_decode(b"\x01\x02")          # two items: a CBOR sequence
except CBORTrailingDataError as e:
    print(e.offset)                   # 1
    print(e)                          # Trailing data after CBOR item: 1 extra byte(s) (at offset 1)
```

CBOR sequences (RFC 8742) are deliberately **not** accepted by these functions;
a separate sequence-decoding API (for `.cborseq`) is planned.
Nested `.cbor`-controlled byte strings in the analyzer are decoded with the same
strict rules.

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

276 tests across seven files in `tests/`; all pass:

```bash
pytest                                       # requires: pip install pytest
python3 -m unittest discover -s tests -t .  # no extra dependencies
```

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_cbor_cddl_analyzer.py` | 110 | CDDL parsing, validation, EDN generation, CoRIM |
| `tests/test_simple_cbor.py` | 70 | CBOR encode/decode, diagnostics, builder |
| `tests/test_cbor_builder.py` | 29 | Iterative construction, nested access, merge |
| `tests/test_canonical_and_json.py` | 25 | Canonical encoding, JSON conversion, round-trips |
| `tests/test_strict_decoding.py` | 18 | Strict single-item decoding: trailing bytes, truncation, error offsets, CLI |
| `tests/test_set_nested.py` | 15 | `set_nested` path creation and errors |
| `tests/test_cbor_diag_dump_extra.py` | 9 | Diagnostic dump edge cases and truncated input |

See [TESTING.md](TESTING.md) for individual class/test commands, pytest
configuration, CI/CD workflow examples, and contribution guidelines.

---

## Limitations

- The supported CDDL subset covers practical attestation schemas (CoRIM, CoSWID);
  it does not implement the full RFC 8610 grammar.
- Indefinite-length CBOR items and the `undefined` simple value are not supported by
  the bundled encoder/decoder (decoding them raises `CBORUnsupportedError`).
- CBOR sequences (multiple concatenated items) are rejected by the decoder; a
  dedicated sequence API is not yet available.

---

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). While the
major version is `0`, a minor bump (`0.x.0`) may contain breaking changes and a
patch bump (`0.x.y`) is for backward-compatible fixes.

- The version lives in one place: `__version__` in [`_version.py`](_version.py).
  `pyproject.toml` reads it dynamically, `simple_cbor.__version__` re-exports it,
  and `cbor_cddl_analyzer.py --version` prints it.
- **Every change must bump the version** and add an entry to
  [CHANGELOG.md](CHANGELOG.md) (format: [Keep a Changelog](https://keepachangelog.com/)).
- Keep this README and the files in `docs/` in sync with the code in the same change.