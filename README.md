# CBOR-CDDL Analyzer and EDN Generator

A Python tool for analyzing CBOR (Concise Binary Object Representation) data
against CDDL (Concise Data Definition Language) schemas and generating
annotated EDN (Extended Diagnostic Notation) output.

## Features

- **CDDL schema parsing** — map types, array types, type aliases, CBOR tag notation, `.cbor` control, `.size` constraints, type choices, optional fields, IANA registered parameters
- **CBOR validation** — enforces required/optional fields, primitive types (`tstr`, `uint`, `int`, `bool`, `float`, `null`, `bstr`), `.size` constraints, unknown-field detection, and array occurrence (`+`/`*`)
- **Annotated EDN generation** — three output formats: `keyindex`, `keyname`, and `both`
- **Nested CBOR decoding** — automatically decodes `bytes .cbor inner-type` fields and renders them as indented EDN with a `bytes()` wrapper
- **CBOR tag notation** — renders tagged data as `tag(value)` with the type name as a comment

## Installation

```bash
pip install cbor2   # optional but recommended for broader CBOR compatibility
```

`cbor2` is optional. The bundled `simple_cbor` module handles encoding and
decoding without it.

## Command-line usage

```bash
# Generate annotated EDN
python cbor_cddl_analyzer.py schema.cddl data.cbor

# Save EDN to a file
python cbor_cddl_analyzer.py schema.cddl data.cbor --output data.edn

# Validate and generate
python cbor_cddl_analyzer.py schema.cddl data.cbor --validate --type person

# Show all parsed CDDL types and exit
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

### Options

| Flag | Description |
|------|-------------|
| `-o / --output PATH` | Write EDN to file instead of stdout |
| `-t / --type TYPE` | Root CDDL type name for validation |
| `-v / --validate` | Validate CBOR against the schema |
| `--no-annotate` | Disable field-name comments in EDN |
| `--show-types` | Print parsed types and exit |
| `--format {keyindex,keyname,both}` | EDN key format (default: `keyindex`) |

## Python API

```python
from cbor_cddl_analyzer import CDDLParser, CBORAnalyzer, EDNGenerator

# Parse schema
cddl = CDDLParser(open("schema.cddl").read())

# Validate
analyzer = CBORAnalyzer(cddl)
if not analyzer.validate(decoded_data, "person"):
    for err in analyzer.get_errors():
        print(err)

# Generate EDN
gen = EDNGenerator(cddl, edn_format="keyindex")
print(gen.generate(decoded_data, "person"))
```

## CDDL schema format

The tool supports the multi-line IANA registered-parameter syntax used in
CoRIM and CoSWID schemas:

```cddl
person = {
  &( name  : 0 ) => tstr,          ; required text field
  &( age   : 1 ) => uint,          ; required unsigned integer
  ? &( email : 2 ) => tstr,        ; optional text field
  &( uuid  : 3 ) => bstr .size 16, ; exactly 16 bytes
  &( label : 4 ) => tstr .size (1..64), ; 1–64 characters
}
```

### Supported primitive types

| CDDL type | Python type | Notes |
|-----------|-------------|-------|
| `tstr` / `text` | `str` | With optional `.size` constraint |
| `bstr` / `bytes` | `bytes` | With optional `.size` constraint |
| `uint` | `int >= 0` | `bool` rejected (distinct CBOR major type) |
| `int` | `int` | `bool` rejected |
| `bool` | `bool` | `int` rejected |
| `float` / `float16` / `float32` / `float64` | `float` | `int` rejected |
| `null` / `nil` | `None` | |
| `any` | anything | No type check |

### Array types

```cddl
tags    = [ * tstr ]   ; zero or more text strings
aliases = [ + tstr ]   ; one or more text strings (empty array fails)
```

### CBOR tags

```cddl
tagged-corim = #6.501(unsigned-corim-map)
```

### Nested CBOR (`.cbor` control)

```cddl
payload = #6.506(bytes .cbor inner-type)
inner-type = {
  &( value : 0 ) => uint,
}
```

### Type choices

```cddl
$kind /= option-a
$kind /= option-b
```

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

## EDN output formats

**`keyindex`** (default) — integer keys with name comments:
```edn
/ person / {
  / name / 0: "Alice",
  / age  / 1: 30,
}
```

**`keyname`** — keys replaced by quoted names:
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

## Creating CBOR test data

```python
from simple_cbor import cbor_encode, cbor_decode

data = {0: "Alice", 1: 28}
cbor_bytes = cbor_encode(data)
assert cbor_decode(cbor_bytes) == data
```

## Limitations

- Single-line map/array bodies (`record = { &(k:0)=>tstr }`) are not parsed
  as structured types; use the multi-line form.
- Advanced CDDL validators (`.regexp`, value range predicates, etc.) are not evaluated.
- The CDDL subset covers practical attestation schemas, not the full RFC 8610 grammar.