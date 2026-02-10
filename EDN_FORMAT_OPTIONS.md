# EDN Format Options Guide

## Overview

The CBOR-CDDL Analyzer now supports **three different EDN output formats** for IANA registered parameters, allowing you to choose the representation that best suits your needs.

## Command-Line Option

```bash
--edn-format {keyindex,keyname,both}
```

**Default:** `keyindex`

## Format Comparison

### Sample CDDL Schema

```cddl
record = {
  &( id : 0 ) => uint,
  &( data : 1 ) => data-block,
}

data-block = {
  &( value : 0 ) => uint,
  &( label : 1 ) => tstr,
}
```

### Sample CBOR Binary

```
A2 00 19 3039 01 A2 00 182A 01 67 6578616D706C65
```

Decoded structure:
```
{
  0: 12345,
  1: {
    0: 42,
    1: "example"
  }
}
```

## Format 1: keyindex (Default)

**Preserves binary structure, adds semantic annotations as comments**

### Command

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --edn-format keyindex
```

### Output

```edn
{
  0: 12345,  / id /
  1: {  / data /
    0: 42,  / value /
    1: "example"  / label /
  }
}
```

### Characteristics

- ✅ **Round-trip compatible** - Can recreate exact CBOR
- ✅ **Shows binary structure** - Numeric keys match CBOR
- ✅ **Semantic meaning** - Comments explain each field
- ✅ **Debugging friendly** - See both representations
- 🎯 **Best for:** Development, debugging, understanding CBOR structure

## Format 2: keyname

**Uses semantic names as keys, human-readable**

### Command

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --edn-format keyname
```

### Output

```edn
{
  "id": 12345,
  "data": {
    "value": 42,
    "label": "example"
  }
}
```

### Characteristics

- ✅ **Highly readable** - Self-documenting
- ✅ **JSON-like** - Familiar format
- ❌ **Not round-trip** - Loses keyindex information
- ❌ **Can't recreate exact CBOR** - Requires schema for conversion
- 🎯 **Best for:** Documentation, presentations, quick understanding

## Format 3: both

**Shows both keyindex and keyname inline**

### Command

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --edn-format both
```

### Output

```edn
{
  0 / id /: 12345,
  1 / data /: {
    0 / value /: 42,
    1 / label /: "example"
  }
}
```

### Characteristics

- ✅ **Complete information** - Both representations visible
- ✅ **Learning tool** - Shows keyindex ↔ keyname mapping
- ⚠️ **Verbose** - More characters per line
- 🎯 **Best for:** Learning, education, complete transparency

## Recursive Annotation

All three formats support **recursive annotation** - IANA parameter names are applied at all nesting levels.

### Deep Nesting Example

**CDDL:**
```cddl
record = {
  &( id : 0 ) => uint,
  &( data : 1 ) => data-block,
}

data-block = {
  &( value : 0 ) => uint,
  &( metadata : 1 ) => metadata-info,
}

metadata-info = {
  &( created : 0 ) => uint,
  &( author : 1 ) => tstr,
}
```

**keyindex format:**
```edn
{
  0: 12345,  / id /
  1: {  / data /
    0: 42,  / value /
    1: {  / metadata /
      0: 1640995200,  / created /
      1: "Alice"  / author /
    }
  }
}
```

**keyname format:**
```edn
{
  "id": 12345,
  "data": {
    "value": 42,
    "metadata": {
      "created": 1640995200,
      "author": "Alice"
    }
  }
}
```

**both format:**
```edn
{
  0 / id /: 12345,
  1 / data /: {
    0 / value /: 42,
    1 / metadata /: {
      0 / created /: 1640995200,
      1 / author /: "Alice"
    }
  }
}
```

## CoRIM Examples

### Minimal CoRIM (keyindex)

```bash
python cbor_cddl_analyzer.py unified.cddl minimal-corim.cbor \
  --type corim-map --edn-format keyindex
```

```edn
{
  0: "urn:example:corim:minimal-example",  / id /
  1: [  / tags /
    h'a201a1007775726e3a6578616d706c653a636f6d69643a313233343504a0'
  ]
}
```

### Minimal CoRIM (keyname)

```bash
python cbor_cddl_analyzer.py unified.cddl minimal-corim.cbor \
  --type corim-map --edn-format keyname
```

```edn
{
  "id": "urn:example:corim:minimal-example",
  "tags": [
    h'a201a1007775726e3a6578616d706c653a636f6d69643a313233343504a0'
  ]
}
```

### Minimal CoRIM (both)

```bash
python cbor_cddl_analyzer.py unified.cddl minimal-corim.cbor \
  --type corim-map --edn-format both
```

```edn
{
  0 / id /: "urn:example:corim:minimal-example",
  1 / tags /: [
    h'a201a1007775726e3a6578616d706c653a636f6d69643a313233343504a0'
  ]
}
```

## Type Alias Resolution

The analyzer now automatically resolves type aliases:

```cddl
corim = concise-rim-type-choice
concise-rim-type-choice /= tagged-unsigned-corim-map
tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
unsigned-corim-map = corim-map
```

When you specify `--type corim`, the analyzer:
1. Recognizes it's a type alias
2. Resolves to `concise-rim-type-choice`
3. Identifies it's a type choice
4. Provides helpful error with alternatives

**Error message:**
```
Type 'corim' resolves to type choice 'concise-rim-type-choice' 
with alternatives: tagged-unsigned-corim-map, signed-corim. 
Please specify one of the concrete types for validation.
```

**Solution:** Use `--type corim-map` or `--type unsigned-corim-map`

## Non-IANA Fields

For regular fields (not IANA registered parameters), all formats behave the same:

**CDDL:**
```cddl
person = {
  0: tstr,  ; name
  1: uint,  ; age
}
```

**All formats show:**
```edn
{
  0: "Alice",  / name /
  1: 28  / age /
}
```

Comments are annotations from the CDDL schema, not transformations.

## Compatibility Notes

### Round-Trip Conversion

| Format | CBOR → EDN | EDN → CBOR |
|--------|-----------|-----------|
| keyindex | ✅ Perfect | ✅ Direct |
| keyname | ✅ Perfect | ⚠️ Needs schema |
| both | ✅ Perfect | ✅ Use keyindex part |

### Tool Interoperability

- **keyindex**: Compatible with standard EDN parsers
- **keyname**: Compatible with JSON parsers (after comment removal)
- **both**: Requires custom parser for keyindex extraction

## Use Case Recommendations

| Use Case | Recommended Format |
|----------|-------------------|
| Development & Debugging | `keyindex` |
| Production Monitoring | `keyindex` |
| Documentation | `keyname` |
| API Responses (human) | `keyname` |
| API Responses (machine) | CBOR binary |
| Learning CBOR/CDDL | `both` |
| Round-trip testing | `keyindex` |
| Compliance verification | `keyindex` |

## Advanced Usage

### Combining with Other Options

```bash
# keyindex format with validation
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --validate --type record --edn-format keyindex

# keyname format without annotations
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --no-annotate --edn-format keyname

# both format to file
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --output result.edn --edn-format both
```

### Pipeline Usage

```bash
# Generate all three formats
for fmt in keyindex keyname both; do
  python cbor_cddl_analyzer.py schema.cddl data.cbor \
    --edn-format $fmt --output "result-${fmt}.edn"
done
```

## Summary

Choose the format that best fits your workflow:

- **keyindex** (default) - Preserves binary structure, round-trip safe
- **keyname** - Human-readable, self-documenting
- **both** - Complete information, educational

All formats support full recursive annotation for nested structures.
