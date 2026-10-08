# Python API reference

Install with `pip install cddl-verifier` and import `cddl_verifier`.

| Module | Status | Main entry points |
|--------|--------|-------------------|
| [`cddl_verifier`](#cddl_verifier-validation) | Public | `validate`, `Validator`, `ValidationResult`, `Diagnostic`, `SchemaError`, `CBORDecodeError`, `__version__` |
| [`cddl_verifier.cbor`](#cddl_verifiercbor-encoding-and-decoding) | Public | `encode`, `decode`, `diag_dump`, `CBOR`, decode errors |
| [`cddl_verifier.json_codec`](#cddl_verifierjson_codec-json-conversion) | Public | `cbor_to_json`, `json_to_cbor`, file helpers |
| [`cddl_verifier._analyzer`](#internal-parser-validator-and-edn-generator) | Internal | `CDDLParser`, `CBORAnalyzer`, `EDNGenerator` |

Modules whose names start with an underscore are internal: they can change in
any release without notice. The public names above follow the versioning policy
in the [README](../README.md#versioning).

> **Provisional value types.** Decoded CBOR values currently represent tags as
> `(tag, value)` tuples and arrays or maps used as map keys as tuples. These
> types are expected to change ([#78](https://github.com/sahebbiswas/cddl_verifier/issues/78), [#88](https://github.com/sahebbiswas/cddl_verifier/issues/88)).

---

## `cddl_verifier`: validation

```python
from pathlib import Path
from cddl_verifier import validate

result = validate(Path("schema.cddl"), Path("data.cbor").read_bytes(),
                  root_type="corim-map")
if result.valid:
    print("valid")
else:
    for diagnostic in result.diagnostics:
        print(diagnostic.code, diagnostic.message)
```

### `validate(schema, data, root_type=None) -> ValidationResult`

Shorthand for `Validator(schema).validate(data, root_type)`.

### `Validator(schema)`

Parses a schema once so it can check many items.

- `schema`: CDDL text as a `str`, or a path as a `pathlib.Path` (any
  `os.PathLike`). A plain `str` is always schema text, never a file name.
- Raises `SchemaError` (a `ValueError`) when a schema file cannot be read, and
  `TypeError` for any other argument type. The current parser is lenient:
  malformed CDDL is usually not rejected, so check results against known-good data.

Members:

| Member | Description |
|--------|-------------|
| `validate(data, root_type=None)` | Validate and return a `ValidationResult` |
| `to_edn(data, root_type=None, *, annotate=True, edn_format="keyindex")` | Render EDN (`"keyindex"`, `"keyname"` or `"both"`); raises `CBORDecodeError` for undecodable bytes |
| `default_root_type` | The first rule in the schema, used when `root_type` is `None` |

`data` is either CBOR bytes (`bytes`, `bytearray`, `memoryview`), which are
decoded with the strict single-item decoder, or an already-decoded Python value.
If `root_type` is `None` and the schema has no rules, `SchemaError` is raised.

### `ValidationResult`

| Attribute | Description |
|-----------|-------------|
| `valid` | `True` if the data matches the schema; `bool(result)` is the same |
| `root_type` | The rule that was checked |
| `diagnostics` | Tuple of `Diagnostic` |
| `errors` | Tuple of error messages (shortcut over `diagnostics`) |
| `data` | The decoded value, or `None` if decoding failed |

CBOR that cannot be decoded does **not** raise: the result is invalid and holds
one `Diagnostic` with `code == "decode"` and the byte `offset` of the problem.

### `Diagnostic`

| Attribute | Description |
|-----------|-------------|
| `message` | Human-readable text (also `str(diagnostic)`) |
| `code` | `"decode"` or `"validation"` |
| `severity` | `"error"` |
| `offset` | Byte offset into the CBOR input for decode errors, otherwise `None` |

The library does not log anything by default. The `cddl-verify` CLI prints
progress and errors to stderr.

---

## Internal: parser, validator and EDN generator

These classes back the public API. They are documented for contributors and
will change during the CDDL AST and semantic-resolution work
([#69](https://github.com/sahebbiswas/cddl_verifier/issues/69), [#70](https://github.com/sahebbiswas/cddl_verifier/issues/70)); use `Validator` instead.

### Parsing and validating

```python
from cddl_verifier._analyzer import CDDLParser, CBORAnalyzer
from cddl_verifier.cbor import CBOR

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
from cddl_verifier._analyzer import EDNGenerator

gen = EDNGenerator(cddl, edn_format="keyindex")   # or "keyname", "both"
print(gen.generate(data, "person"))
```

The output formats are shown in [CLI.md](CLI.md#edn-output-formats).

---

## `cddl_verifier.cbor`: encoding and decoding

`cddl_verifier.cbor` provides `encode`, `decode` and `diag_dump`, and a `CBOR`
class for all CBOR operations, with no external dependencies. The functions are
also available as `cbor_encode`, `cbor_decode` and `cbor_diag_dump`.

```python
from cddl_verifier.cbor import CBOR, cbor_encode, cbor_decode

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
tuples so they are hashable), tags to `(tag_number, value)` tuples (except
bignums, tags 2/3, whose value is outside the 64-bit range: those decode to
`int`), and
`true` / `false` / `null` / floats to their Python equivalents.

### Strict single-item decoding

`decode()`, `CBOR.load()`, `CBOR.loads()` and `cbor_decode()` accept
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
from cddl_verifier.cbor import CBORTrailingDataError, cbor_decode

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

Canonical mode follows the RFC 8949 §4.2.1 core deterministic encoding
requirements: shortest integer, length and tag arguments; floats in the
shortest exact width (`1.5` is `f9 3e00`); one NaN (`f9 7e00`); map keys sorted
bytewise by their encoding. Keys that encode identically (for example two NaNs)
raise `ValueError`. Without `canonical=True`, floats are written as 8-byte
doubles and maps keep insertion order.

---

## `cddl_verifier.json_codec`: JSON conversion

```python
from cddl_verifier.json_codec import cbor_to_json, json_to_cbor

# CBOR → JSON
json_str = cbor_to_json(raw, pretty=True)

# With CBOR-type annotations (keeps bytes and tags for round-trips)
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

> **Round-trips need `typed=True`.** Without it, `bytes` values are reduced to
> Base64 strings and CBOR tag numbers are discarded. Even with it, non-string map
> keys become JSON strings (`{0: "a"}` comes back as `{"0": "a"}`), so maps with
> integer keys do not round-trip exactly.

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
from cddl_verifier.json_codec import cbor_file_to_json_file, json_file_to_cbor_file

cbor_file_to_json_file("data.cbor", "data.json", pretty=True, typed=True)
json_file_to_cbor_file("data.json", "data.cbor", canonical=True)
```

See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md) for round-trip examples.
