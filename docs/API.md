# Python API reference

Install with `pip install cddl-verifier` and import `cddl_verifier`. For end-to-end
consumer workflows, see [Recipes](RECIPES.md).

| Module | Status | Main entry points |
|--------|--------|-------------------|
| [`cddl_verifier`](#cddl_verifier-validation) | Public | `validate`, `Validator`, `ValidationResult`, `Diagnostic`, `render_text`, `render_json`, `schema_diagnostics`, `SchemaError`, `CBORDecodeError`, `__version__` |
| [`cddl_verifier.cbor`](#cddl_verifiercbor-encoding-and-decoding) | Public | `encode`, `decode`, `diag_dump`, `CBOR`, `CBORKey`, decode errors |
| [`cddl_verifier.json_codec`](#cddl_verifierjson_codec-json-conversion) | Public | `cbor_to_json`, `json_to_cbor`, file helpers |
| [`cddl_verifier._analyzer`](#internal-parser-validator-and-edn-generator) | Internal | `CBORAnalyzer` (composed from the mixins in `cddl_verifier._validator`), and `CDDLParser` (now in `cddl_verifier._schema`) and `EDNGenerator` (now in `cddl_verifier._edn`); the old import paths still work |

Modules whose names start with an underscore are internal: they can change in
any release without notice. The public names above follow the versioning policy
in the [README](../README.md#versioning).

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

### `validate(schema, data, root_type=None, *, modules=None, module_path=(), unique_choices=False, require_deterministic=False) -> ValidationResult`

Shorthand for `Validator(schema, modules=modules, module_path=module_path, unique_choices=unique_choices).validate(data, root_type, require_deterministic=require_deterministic)`.

With `require_deterministic=True` the CBOR bytes must also be deterministically
encoded ([below](#requiring-deterministic-encoding)): bytes that are not give an
invalid result with one `decode` diagnostic at the offset of the first
violation. It needs bytes: a decoded Python value has no encoding to check, so
passing one raises `TypeError`.

### `Validator(schema, *, modules=None, module_path=(), unique_choices=False)`

Parses a schema once so it can check many items.

- `schema`: CDDL text as a `str`, or a path as a `pathlib.Path` (any
  `os.PathLike`). A plain `str` is always schema text, never a file name.
- `modules`: the modules the schema imports with `;# import NAME as PREFIX` or
  `;# include NAME` (draft-ietf-cbor-cddl-modules), as a mapping of module name
  to its CDDL text or to a path. Their rules are checked and join the schema:
  `PREFIX.<rule>` for an import with `as`, the rule's own name for an include.
- `module_path`: directories searched for `NAME.cddl` when `modules` has no
  entry for `NAME`. When `schema` is a path, its directory is searched last.
- `unique_choices`: reject data that matches more than one map, array or tag
  alternative of a choice. By default any matching alternative is enough, as in
  CDDL; EDN labels such data with the first match in schema order
  ([CDDL_SUPPORT.md](CDDL_SUPPORT.md#type-choices)).
- A module that is not found is not an error: the names used from it are not
  checked and match any value. `Validator.warnings` lists these imports (and
  each is also issued as a `UserWarning`).
- Raises `SchemaError` (a `ValueError`) when a schema file cannot be read, is
  not valid CDDL, or does not make sense (an undefined name, a generic given
  the wrong number of arguments, an unknown control operator; see
  [CDDL_SUPPORT.md](CDDL_SUPPORT.md#schema-semantics)), and `TypeError` for any
  other argument type. For a problem in the schema text the message is
  `file:line:column: problem` (`<schema>` for text) and the exception has
  `line` and `column` attributes:

  ```python
  >>> Validator("r = { a: uint ?, }")
  SchemaError: <schema>:1:16: expected a type, found ','
  >>> Validator("r = { a: unit }")
  SchemaError: <schema>:1:10: undefined name 'unit'; did you mean 'uint'?
  ```

  When the schema parses, the exception is for the first problem in it; for a
  problem in what a rule contains (an undefined name, a control that does not
  apply), `exception.errors` holds every problem the check found, the first
  one first. [`schema_diagnostics`](#schema_diagnostics) returns them as diagnostics.

Members:

| Member | Description |
|--------|-------------|
| `validate(data, root_type=None)` | Validate and return a `ValidationResult` |
| `to_edn(data, root_type=None, *, annotate=True, edn_format="keyindex")` | Render EDN (`"keyindex"`, `"keyname"` or `"both"`); raises `CBORDecodeError` for undecodable bytes |
| `default_root_type` | The first rule in the schema, used when `root_type` is `None` |

`data` is either CBOR bytes (`bytes`, `bytearray`, `memoryview`), which are
decoded with the strict single-item decoder, or an already-decoded Python value.
If `root_type` is `None` and the schema has no rules, `SchemaError` is raised.

### `schema_diagnostics`

`schema_diagnostics(schema, *, modules=None, module_path=()) -> tuple[Diagnostic, ...]`

The problems in a schema as `Diagnostic`s, instead of the `SchemaError` that
`Validator` raises: an empty tuple for a schema that is fine. They render with
`render_text` and `render_json` like any other finding, and a schema with several
problems gives all of them. The arguments are those of `Validator`; an unreadable
schema file is a diagnostic too, and only a `schema` of the wrong type raises
(`TypeError`).

```python
>>> found = schema_diagnostics("r = { a: nope, b: tstr .size \"x\" }\nq = { z: missing }")
>>> print(render_text(found))
<schema>:1:10: undefined name 'nope'
<schema>:1:30: the argument of '.size' must be an unsigned integer, not a text string
<schema>:2:10: undefined name 'missing'
>>> print(render_text(found[:1], verbose=True))
error[schema]: undefined name 'nope'
  at schema <schema>:1:10, offset 9
  in rule r
```

Each has `code == "schema"` and `category == "schema"`, the `rule` it is in, the
position as `offset` / `end_offset` (character offsets into the schema text) and
as `line`, `column` and `schema_location` (`file:line:column`), and, for a syntax
error, `expected` (the tokens that would have been accepted). Their `message` has
no location in it; `render_text` puts `schema_location` in front.

Text that is not valid CDDL gives that one syntax error. A schema that parses is
checked in full: a problem in a rule's contents does not stop the check of the
other members and rules, so each is reported, in the order the schema is written.
A problem in the names (a rule defined twice, a rule that is its own definition)
is reported alone, because nothing else can be checked without them.

### `ValidationResult`

| Attribute | Description |
|-----------|-------------|
| `valid` | `True` if the data matches the schema; `bool(result)` is the same |
| `root_type` | The rule that was checked |
| `diagnostics` | Tuple of `Diagnostic` |
| `errors` | Tuple of error messages (shortcut over `diagnostics`) |
| `data` | The decoded value, or `None` if decoding failed |

| Method | Description |
|--------|-------------|
| `to_dict()` | `format_version`, `valid`, `root_type` and `diagnostics` as JSON-ready data |
| `to_json(indent=2)` | The same as a JSON document |
| `format(verbose=False)` | The diagnostics as text (see [`render_text`](#rendering-diagnostics)) |

CBOR that cannot be decoded does **not** raise: the result is invalid and holds
one `Diagnostic` with `code == "decode"` and the byte `offset` of the problem.

### `Diagnostic`

| Attribute | Description |
|-----------|-------------|
| `message` | Human-readable text (also `str(diagnostic)`) |
| `code` | `"decode"`, `"validation"`, or `"schema"` for a problem in the schema text (see [`schema_diagnostics`](#schema_diagnostics)) |
| `severity` | `"error"`, `"warning"` or `"info"`; a diagnostic is an `"error"`, except that `cddl-verify --diagnostics json` reports the failures of an inferred root type as `"warning"` |
| `category` | The kind of problem: `decode`, `schema`, `tag`, `type`, `value`, `missing`, `unknown`, `occurrence`, `nested` or `other` |
| `path` | Where the item is in the data: the map keys and array indexes from the root, as the decoder returns them (`(1, 2)`); `()` is the root. An integer map key and an array index look alike here; use `path_expr` to tell them apart |
| `offset`, `end_offset` | Byte span of the offending item in the CBOR input, or `None` (see below) |
| `hex`, `hex_truncated` | Hex of the first 16 bytes of that item (`"18 c8"`); `hex_truncated` is `True` when the item is longer |
| `rule` | The CDDL rule being checked |
| `schema_location` | `file:line:column` of that rule (`<schema>` for schema text) |
| `schema_path` | The rules and members the check went through, root first |
| `expected`, `actual` | What the schema asks for and what the data has, as short text, when known: `at most 100` and `200` for `uint .le 100`, `uint / tstr` and `bstr` for a choice |
| `line`, `column` | For `code == "schema"`: the 1-based position of the problem in the schema. `None` otherwise |
| `alternatives` | For a value that matches none of a type choice (`uint / tstr`): one `Diagnostic` for each alternative tried, with its own `path`, offsets, `expected` and `actual`. At most 16 (`MAX_ALTERNATIVES`); empty for any other finding |

`path_text` and `schema_path_text` give the paths as `/1/2` and `/measurement/1/[2]`.
`path_expr` gives the data path in `$`-notation, which tells array indexes from map keys:
`$[1][2]` is element 2 of element 1, `$.name` the text key `name`, `$["a b"]` a text key
that is not a name, and `$<key 1>[1]` element 1 of the array at integer key 1
(`<key h'ab'>` and `<key true>` are other key types). It is also the `path_expr` member of
the JSON form, next to `path`.
`to_dict()` and `to_json()` give the JSON form, and `format()` several lines of text.

A field is `None` or empty when it is not known. Read the fields instead of
parsing `message`: the wording of messages can improve between versions, while
the fields and the JSON layout (`format_version`) stay stable.

**Offsets.** CBOR that cannot be decoded gives the offset of the problem. For
validation errors the offset is the position of the item in the CBOR bytes, found
by the decoder, and only when the data was given as bytes: validating an
already-decoded Python value gives no offsets. A missing member points at the map
that lacks it, and an unknown member at its key. Each unknown member is its own
diagnostic, in the order the keys appear in the map, and its `path` ends at that
key. Inside CBOR embedded in a byte
string (`bstr .cbor`, `bstr .cborseq`), `path` continues below the byte string
(the items of a sequence are its array indexes) and the offsets are
those of the inner item, counted from the start of the input as for any other item
(so `raw[offset:end_offset]` is the inner item's bytes). This holds for CBOR
embedded in CBOR embedded in a byte string too, wherever the type is written: as
the type of a member, of an array element or of the root. An error about the
embedded item as a whole (`bstr .cbor uint` with text inside) is at all of its
bytes. The offsets are those of the byte
string itself when the inner item has no position: a chunked (indefinite-length)
byte string, or input too large for the location index.

### Rendering diagnostics

```python
from cddl_verifier import render_json, render_text, validate

result = validate(schema, cbor_bytes)
print(render_text(result.diagnostics))                # one message per line
print(render_text(result.diagnostics, verbose=True))  # with path, offset, rule, bytes
print(render_json(result.diagnostics, valid=result.valid))
```

`render_text` prints each message on its own line, as `cddl-verify` does. With
`verbose=True` each diagnostic also shows where it is:

```text
error[type]: Element [1] of field '2' has wrong type: expected tstr, got int
  at data $<key 2>[1], offset 11
  in rule person (<schema>:1:1)
  expected tstr, found int
  bytes 05
```

`render_json` writes `{"format_version": 1, "diagnostics": [...]}`, with each
diagnostic as `Diagnostic.to_dict()`. Keyword arguments (`valid=`, `root_type=`)
become extra top-level members. Every field is always present, and unknown values
are `null`. A path step is a JSON integer or string; byte-string, boolean, float
and other keys are written as text (`"h'aa'"`, `"true"`).

The library does not log anything by default. The `cddl-verify` CLI prints
progress and errors to stderr.

---

## Internal: parser, validator and EDN generator

These classes back the public API. They are documented for contributors and
will change during the CDDL AST and semantic-resolution work
([#69](https://github.com/sahebbiswas/cddl_verifier/issues/69), [#70](https://github.com/sahebbiswas/cddl_verifier/issues/70)); use `Validator` instead.

### Parsing and validating

```python
from cddl_verifier._schema import CDDLParser
from cddl_verifier._analyzer import CBORAnalyzer
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
    diagnostics = analyzer.get_diagnostics()   # the same findings as `Diagnostic`s
```

The supported schema syntax and validation rules are described in
[CDDL_SUPPORT.md](CDDL_SUPPORT.md).

### Generating annotated EDN

```python
from cddl_verifier._edn import EDNGenerator

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
`bytes`, text to `str`, arrays to `list`, maps to `dict` (see
[Map keys](#map-keys) for their keys), tags to [`CBORTag`](#tags)
(except bignums, tags 2/3, whose value is outside the 64-bit range: those
decode to `int`), and `true` / `false` / `null` / floats to their Python
equivalents.

### Tags

A tag decodes to a `CBORTag` with the tag number in `.tag` and the tagged item
in `.value`, and the encoder writes a `CBORTag` as a tag. It is immutable, equal
only to another `CBORTag` with the same number and an equal value, and it
unpacks (`tag, value = item`).

```python
from cddl_verifier.cbor import CBORTag, decode, encode

item = decode(bytes.fromhex("c11a514b67b0"))   # 1(1363896240)
item                                           # CBORTag(1, 1363896240)
item.tag, item.value                           # (1, 1363896240)
encode(CBORTag(32, "https://example.com"))     # d820...
```

A `(tag, value)` tuple is not a tag (it was before 0.24.0). Code that wrote
`encode((1, 100))` for a tag, or read a decoded tag as `item[0]` and `item[1]`,
changes to `CBORTag(1, 100)` and `item.tag` / `item.value`. The tuple `(1, 100)`
could also mean an array of two integers, so the encoder and the typed JSON
conversion raise `TypeError` for a tuple shaped `(int >= 0, x)` and ask which
was meant: `CBORTag(1, 100)` for the tag, `[1, 100]` for the array. Other
tuples are arrays as before. `CBORTag(tag, value)` raises `TypeError` for a tag
number that is not an `int` and `ValueError` outside 0 to 2**64 - 1. The
tagged value itself can be a mutable list or dict, as in any Python container.

### Map keys

Integer, text, byte string and `null` map keys decode to plain `int`, `str`,
`bytes` and `None`. Every other key (a boolean, float, array, map or tag) decodes
to a `CBORKey`, which wraps the key and keeps its CBOR type. As plain Python
values these keys would be unhashable or would merge with a different key:
Python treats `True`, `1` and `1.0` as one dict key, but they are three CBOR keys.

```python
from cddl_verifier.cbor import CBORKey, decode, encode

data = decode(bytes.fromhex("a201f5f5f4"))   # {1: true, true: false}
data                    # {1: True, CBORKey(True): False}
data[CBORKey(True)]     # False
key = next(iter(decode(bytes.fromhex("a1820102f6"))))   # {[1, 2]: null}
key.value               # [1, 2] (a copy)
encode(data).hex()      # 'a201f5f5f4': keys keep their type
```

Two `CBORKey` objects are equal when they stand for the same CBOR value: the
types must match (`CBORKey(True) != CBORKey(1) != CBORKey(1.0)`), floats compare
by value whatever their encoded width (`-0.0` and `0.0` differ), and maps
compare without regard to entry order. A `CBORKey` never equals a plain value.
The encoder writes a `CBORKey` as the value it wraps, and `CBORKey` objects are
immutable. The decoder rejects a map with two keys that are equal in this sense
(including two NaN keys with the same encoding).

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
| `CBORDecodeError` | Reserved additional info, invalid UTF-8, duplicate map keys, nesting too deep, and the malformed forms of indefinite-length items and simple values (below) | Offending byte or key |

`CBORUnsupportedError` (a `CBORDecodeError` and a `NotImplementedError`) is kept
for code that catches it, but nothing raises it now.

### Requiring deterministic encoding

`decode`, `decode_sequence`, `CBOR.load`, `CBOR.loads` and `CBOR.loads_sequence`
take `require_deterministic=True` (off by default) for input that must be
deterministically encoded (RFC 8949 section 4.2.1), such as a signed payload
that is hashed. Without it the decoder accepts every well-formed encoding of a
value.

```python
from cddl_verifier.cbor import CBORNotDeterministicError, decode

decode(bytes.fromhex("a2 01 05 02 06"), require_deterministic=True)   # {1: 5, 2: 6}
try:
    decode(bytes.fromhex("a2 01 18 05 02 06"), require_deterministic=True)
except CBORNotDeterministicError as error:
    print(error.offset, error.reason)
    # 2 Not deterministically encoded: integer is not in its shortest form (additional information 24)
```

The check raises `CBORNotDeterministicError`, a `CBORDecodeError` (so a handler for
decode errors catches it), with the `offset` of the **first** item that breaks a
rule, in the order the bytes are read. Malformed input is still reported as
malformed, not as non-deterministic. The rules:

| Rule | Not deterministic | Deterministic |
|------|-------------------|---------------|
| Heads as short as they can be: integers, string, array and map lengths, tag numbers | `18 05`, `58 00`, `98 00`, `d8 01 ..` | `05`, `40`, `80`, `c1 ..` |
| Definite lengths only | `9f 01 ff`, `bf .. ff`, `5f .. ff`, `7f .. ff` | `81 01` |
| Floats in the shortest width that keeps the value | `fb 3ff8000000000000` and `fa 3fc00000` for 1.5 | `f9 3e00` |
| One NaN, `f9 7e00` (any other NaN, in any width, is refused) | `f9 7e01`, `fa 7fc00000` | `f9 7e00` |
| Map keys in bytewise order of their encodings (not by value or length): `0x01 < 0x0a < 0x1818 < 0x61 61` | `a2 02 00 01 00` | `a2 01 00 02 00` |
| Bignums (tags 2 and 3) only for values that do not fit in 64 bits, without leading zero bytes | `c2 41 00`, `c2 48 01 00 00 00 00 00 00 00` | `c2 49 01 00 00 00 00 00 00 00 00` |

The check is the inverse of `encode(canonical=True)`: bytes that pass are exactly
the bytes `encode(decode(data), canonical=True)` writes, and what the canonical
encoder writes always passes. Two notes on strictness: the RFC leaves the
treatment of NaN to the protocol, and this check takes the single quiet NaN the
canonical encoder writes; and it does not require text to be in any Unicode
normal form, which the RFC does not either.

### Indefinite-length items, `undefined` and simple values

Indefinite-length byte strings, text strings, arrays and maps decode to the same
`bytes`, `str`, `list` and `dict` as their definite-length forms (the chunks of
a string are joined), and the encoder writes definite-length items only, so
`encode(decode(x))` re-encodes them. These are rejected with `CBORDecodeError`:
a break with no item to end, a missing break (`CBORTruncatedError`), a chunk
that is not a definite-length string of the same type, a text chunk that is not
UTF-8 by itself, a map that ends after a key, and an indefinite integer or tag.

`undefined` decodes to `UNDEFINED` (the one instance of `CBORUndefined`) and
every other simple value to `CBORSimple(n)` (`n` from 0 to 19 and 32 to 255;
`false`, `true` and `null` stay `False`, `True` and `None`). Both encode back,
are hashable, and work as map keys (as `CBORKey`). The two-byte form below 32
(`f8 18`) and the reserved additional information 28 to 30 are not well-formed.

### Keeping how an item was written

```python
from cddl_verifier.cbor import decode, head_info

value = decode(bytes.fromhex("1805"), keep_encoding=True)   # 5 with a 1-byte argument
value == 5              # True: it behaves as the plain int
head_info(value)        # 24: the additional information of its initial byte
head_info(5)            # 5: a plain value is taken to be written the shortest way
```

With `keep_encoding=True` (`decode`, `cbor_decode`, `CBOR.load`, `CBOR.loads`) a
float always carries its width (`info` 25, 26 or 27), and an integer, string,
array or map carries the additional information of its head when that is not
the shortest one, or 31 for an indefinite length. They are instances of a
subclass of the Python type (`type(x).__name__` is still `int`, `float`, ...)
that compare, hash and print as the plain value. A float is encoded in the width it
carries, so `encode(decode(data, keep_encoding=True))` gives back the bytes (apart
from heads other than a float's, which the encoder writes the shortest way); copying
or pickling gives the plain value (a float keeps its width when copied). The validator decodes this way to check `float16`,
`float32`, `float64` and `#M.N`. It is off by default so decoded values are
plain Python values.

A non-bytes argument raises `TypeError`.

```python
from cddl_verifier.cbor import CBORTrailingDataError, cbor_decode

try:
    cbor_decode(b"\x01\x02")          # two items: a CBOR sequence
except CBORTrailingDataError as e:
    print(e.offset)                   # 1
    print(e)                          # Trailing data after CBOR item: 1 extra byte(s) (at offset 1)
```

CBOR sequences (RFC 8742) are deliberately **not** accepted by these functions.
`decode_sequence(data, keep_encoding=False)` (`cbor_decode_sequence`,
`CBOR.loads_sequence`) reads one:

```python
from cddl_verifier.cbor import decode_sequence

decode_sequence(b"\x01\x62ab\x82\x01\x02")   # [1, 'ab', [1, 2]]
decode_sequence(b"")                          # []  (the empty sequence)
decode_sequence(b"\x01\x62a")                # CBORTruncatedError: item 1 of the sequence ... (at offset 2)
```

A sequence is zero or more complete items, back to back. Empty input is the
empty sequence. A truncated or malformed item raises the same exceptions as
`decode`, with a message that names the item and an `offset` into the whole
input. Nested `.cbor`-controlled byte strings in the analyzer are decoded with
the strict single-item rules; `.cborseq` byte strings with `decode_sequence`.

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
raise `ValueError`. Without `canonical=True`, maps keep insertion order. Floats are
written in the shortest exact width in both modes (RFC 8949 §4.1 preferred
serialization), so `encode(1.5)` is `f9 3e00`; before 0.34.0 the default wrote
every float as an 8-byte double.

### Fixing the width of a float: `float_width`

Some schemas and protocols fix the width: `float64` accepts only a float written as
a double, so `encode({"x": 1.5})` (a half float) would fail it. `float_width(value, bits)`
marks one value with the width (16, 32 or 64) the encoder writes it in:

```python
from cddl_verifier.cbor import encode, float_width

encode({"x": float_width(1.5, 64)}).hex()   # a1 6178 fb 3ff8000000000000
```

The result is a `float` that compares, hashes and prints as the plain value and
carries its width as `info` (see `head_info`), like a float decoded with
`keep_encoding=True`; the validator takes it for a float of that width, so Python
data and the bytes it encodes to agree on `float16`, `float32` and `float64`.
`ValueError` for a width other than 16, 32 or 64 and for a value the width cannot
hold exactly (`float_width(0.1, 32)`); `TypeError` for anything but a float or an
int. `encode(canonical=True)` always writes the shortest width and ignores it.
Arithmetic on the result gives a plain float again.

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
    typed: bool = False,    # preserve bytes, tags and map keys as JSON annotations
    pretty: bool = False,   # pretty-print with indentation
    indent: int = 2,
    sort_keys: bool = False # sort JSON object keys (untyped int keys become
                            # strings, so sorting is lexicographic)
) -> str
```

`cbor_bytes` is decoded with the strict decoder above, so it must hold exactly
one CBOR item.

> **Round-trips need `typed=True`.** Without it, `bytes` values are reduced to
> Base64 strings, CBOR tag numbers are discarded and map keys become JSON strings
> (`{0: "a"}` comes back as `{"0": "a"}`). Two keys that would become the same
> string (`0` and `"0"`, `true` and `"true"`) raise `ValueError` instead of one
> value being dropped. With `typed=True`, a map with any non-string key is written
> as a list of `[key, value]` pairs (below), so it converts back exactly.
>
> `json_to_cbor` raises `ValueError` for a JSON object that gives the same key
> twice, instead of keeping the last value.
>
> `json_to_cbor` reads `{"$cbor": ...}` objects strictly: a malformed annotation raises
> `ValueError` naming its place in the document, and `annotations=False` reads the JSON
> as plain data, so a `"$cbor"` key is an ordinary key
> ([CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md#strict-reading-of-annotations-79)).

### Type annotations in JSON

With `typed=True`, CBOR values that have no JSON equivalent are kept as objects:

| CBOR value | JSON representation |
|-----------|---------------------|
| `b'\x01\x02'` | `{"$cbor": "bytes", "$value": "AQI="}` |
| `CBORTag(32, "https://…")` | `{"$cbor": "tag", "$tag": 32, "$value": "https://…"}` |
| `UNDEFINED` | `{"$cbor": "undefined"}` |
| `CBORSimple(16)` | `{"$cbor": "simple", "$value": 16}` |
| `float('nan')` | `{"$cbor": "NaN"}` |
| `float('inf')` | `{"$cbor": "Infinity"}` |
| `{0: "a", "0": "b"}` (a map with any non-string key, or a `"$cbor"` key) | `{"$cbor": "map", "$value": [[0, "a"], ["0", "b"]]}` |

Maps whose keys are all strings stay JSON objects. In the pairs form, each key
and value is written with the same annotations, so byte-string, float, `true`,
`null` and nested keys are kept. A key given twice in the pairs raises
`ValueError`.

### File conversion

```python
from cddl_verifier.json_codec import cbor_file_to_json_file, json_file_to_cbor_file

cbor_file_to_json_file("data.cbor", "data.json", pretty=True, typed=True)
json_file_to_cbor_file("data.json", "data.cbor", canonical=True)
json_file_to_cbor_file("data.json", "data.cbor", annotations=False)   # "$cbor" is plain data
```

See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md) for round-trip examples.
