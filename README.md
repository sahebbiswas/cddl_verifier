# CBOR-CDDL Analyzer

A comprehensive Python tool for analyzing CBOR (Concise Binary Object Representation) data against CDDL (Concise Data Definition Language) schemas, with automatic type resolution and fully annotated EDN (Extended Diagnostic Notation) output.

## Overview

This tool provides industrial-strength CBOR analysis with:
- **Automatic type resolution** from CDDL schemas
- **Full IANA registered parameter support** with three output formats
- **Nested CBOR decoding** with `.cbor` control operator
- **CBOR tag visualization** in RFC 8949 diagnostic notation
- **Size constraint validation** for bytes and text fields
- **Professional EDN output** with left-aligned annotations

## Key Features

### Validation & Analysis
- ✅ **Automatic validation** when `--type` is specified (no separate `--validate` flag needed)
- ✅ **Type choice resolution** - automatically determines which type variant matches your data
- ✅ **Tag type extraction** - extracts inner types from tagged definitions
- ✅ **Nested CBOR decoding** - automatically decodes `.cbor` embedded content
- ✅ **Size constraint validation** - validates `.size` constraints on bytes/text fields

### EDN Generation
- ✅ **Three annotation formats** - keyindex (default), keyname, both
- ✅ **Left-aligned annotations** - field names, tag types, and map types on the left for easy scanning
- ✅ **CBOR tag notation** - standard `tag_number(content)` format per RFC 8949
- ✅ **bytes() wrapper** - explicitly shows nested CBOR content
- ✅ **Type name headers** - shows resolved CDDL types on maps and arrays
- ✅ **Perfect indentation** - all closing brackets align with their opening counterparts

### CDDL Support
- ✅ **IANA Registered Parameters** - `&( keyname : keyindex ) => type`
- ✅ **Type Aliases** - `name = other_name` with full resolution chains
- ✅ **Type Choices** - `$name /= alternative` with automatic matching
- ✅ **CBOR Tags** - `#6.xxx(type)` with automatic parsing
- ✅ **Control Operators** - `.cbor`, `.size`, `.bits`, `.default`
- ✅ **Groups** - `name = ( fields )`
- ✅ **Socket Extensions** - `$$name //= extension`
- ✅ **Optional Fields** - `?` prefix support
- ✅ **Generics** - `name<M>` basic support

## Installation

### Prerequisites
Python 3.7 or higher

### Dependencies (Optional)
For enhanced CBOR support:
```bash
pip install cbor2
```

The tool includes a built-in CBOR decoder, so external dependencies are optional.

## Quick Start

### Basic Usage

```bash
# Simple EDN generation (no validation)
python cbor_cddl_analyzer.py schema.cddl data.cbor

# With automatic validation and type resolution
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim

# Save to file
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim --output result.edn

# Show all parsed types
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

### CoRIM Example

```bash
# Validate and generate annotated EDN for a CoRIM file
python cbor_cddl_analyzer.py unified.cddl unsigned-good-corim.cbor --type corim

# Output includes:
# - Validation: [OK] Validation successful
# - Automatic type resolution: corim → tagged-unsigned-corim-map
# - Full annotations with resolved type names
# - Nested CBOR decoded and displayed
```

## EDN Output Features

### Automatic Type Resolution

When you specify `--type corim`, the tool automatically:

1. **Resolves type aliases**: `corim` → `concise-rim-type-choice`
2. **Matches type choices**: Detects tag 501 → `tagged-unsigned-corim-map`
3. **Extracts tag inner types**: `#6.501(unsigned-corim-map)` → `unsigned-corim-map` → `corim-map`
4. **Shows resolved names** in output

**No manual type specification needed!** Just use the top-level type and the tool discovers everything.

### Professional EDN Format

```edn
/ tagged-unsigned-corim-map / 501(
  / corim-map / {
    / id / 0: "test corim id",
    / tags / 1: [
      / tagged-concise-mid-tag / 506(
        bytes(
          / concise-mid-tag / {
            / entities / 2: [
              / entity-map / {
                / entity-name / 0: "ACME Ltd.",
                / reg-id / 1: / uri / 32("https://acme.example"),
                / role / 2: [0, 1, 2]
              }
            ],
            / triples / 4: / triples-map / {
              / reference-triples / 0: [
                / reference-triple-record / [
                  / environment-map / {
                    / class / 0: / class-map / {
                      / class-id / 0: / tagged-oid-type / 600(h'...'),
                      / vendor / 1: "ACME",
                      / model / 2: "RoadRunner"
                    }
                  }
                ]
              ]
            }
          }
        )
      )
    ]
  }
)
```

**Features shown:**
- ✅ Left-aligned annotations (`/ name /` before keys)
- ✅ CBOR tags with type names (`/ tagged-unsigned-corim-map / 501(`)
- ✅ Type headers on maps (`/ corim-map / {`)
- ✅ Type headers on arrays (`/ reference-triple-record / [`)
- ✅ Nested CBOR with `bytes(...)` wrapper
- ✅ Perfect indentation alignment
- ✅ All resolved type names displayed

## Three EDN Formats

### Format: keyindex (default)
Preserves binary structure with semantic annotations:
```edn
/ id / 0: "value",
/ data / 1: {
  / count / 0: 42
}
```
✅ Round-trip compatible  
✅ Shows binary structure  
✅ Includes semantic meaning

### Format: keyname
Uses semantic names as keys:
```edn
"id": "value",
"data": {
  "count": 42
}
```
✅ Highly readable  
✅ JSON-like format

### Format: both
Shows both representations:
```edn
0 / id /: "value",
1 / data /: {
  0 / count /: 42
}
```
✅ Complete information  
✅ Educational

## Size Constraint Validation

The tool validates `.size` constraints on bytes and text fields:

### Supported Syntax
```cddl
uuid = bstr .size 16              ; Exact: must be 16 bytes
hash-256 = bytes .size 32         ; Exact: must be 32 bytes
short-text = tstr .size (1..255)  ; Range: 1 to 255 characters
min-length = text .size (8..)     ; Minimum: at least 8 characters
max-length = text .size (..100)   ; Maximum: at most 100 characters
```

### Validation Messages
```
[MISMATCH] Size mismatch: expected exactly 16 bytes, got 20
[MISMATCH] Size mismatch: expected at least 8, got 5
[MISMATCH] Size mismatch: expected at most 100, got 150
```

## Command-Line Options

```
Usage: cbor_cddl_analyzer.py [-h] [-o OUTPUT] [-t TYPE] [-a] [--no-annotate]
                              [--edn-format {keyindex,keyname,both}]
                              [--verbose] [--show-types]
                              cddl_file cbor_file

Positional Arguments:
  cddl_file             Path to CDDL schema file
  cbor_file             Path to CBOR data file

Optional Arguments:
  -h, --help            Show this help message and exit
  -o, --output FILE     Write EDN output to file (default: stdout)
  -t, --type TYPE       Root type name for validation and type resolution
                        (triggers automatic validation)
  -a, --annotate        Annotate EDN with field names (default: True)
  --no-annotate         Disable annotations in EDN output
  --edn-format {keyindex,keyname,both}
                        EDN annotation format (default: keyindex)
  --verbose             Enable detailed validation and resolution logging
  --show-types          Display all parsed CDDL types and exit
```

### Key Notes

- **--type triggers validation**: No separate `--validate` flag needed
- **Automatic type resolution**: Tool discovers actual matched types
- **--verbose**: Shows type resolution, choice matching, tag extraction

## CBOR Tag Notation

The tool displays CBOR tags using standard RFC 8949 diagnostic notation:

### Simple Tags
```edn
/ uri / 32("https://example.com")
/ uuid / 37(h'...')
```

### Nested CBOR Tags
```edn
/ tagged-concise-mid-tag / 506(
  bytes(
    / concise-mid-tag / {
      ...
    }
  )
)
```

### Common Tags
- **Tag 32**: URI (text string)
- **Tag 37**: UUID (byte string)
- **Tag 501**: unsigned-corim-map
- **Tag 505**: concise-swid-tag
- **Tag 506**: concise-mid-tag
- **Tag 600**: UEID / OID
- **Tag 601**: Measured element

## Nested CBOR Support

The tool automatically detects and decodes nested CBOR using the `.cbor` control operator:

### CDDL Definition
```cddl
tagged-concise-mid-tag = #6.506(bytes .cbor concise-mid-tag)
```

### EDN Output
```edn
/ tagged-concise-mid-tag / 506(
  bytes(
    / concise-mid-tag / {
      / entities / 2: [...],
      / triples / 4: {...}
    }
  )
)
```

The `bytes(...)` wrapper explicitly shows:
1. The outer tag (506)
2. That the content is byte-encoded
3. The decoded CBOR structure inside

## Type Resolution Examples

### Example 1: Type Alias Chain
```cddl
corim = concise-rim-type-choice
concise-rim-type-choice /= tagged-unsigned-corim-map
tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
unsigned-corim-map = corim-map
```

**User command:**
```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim
```

**Tool resolves:**
1. `corim` → `concise-rim-type-choice` (alias)
2. Detects tag 501 → matches `tagged-unsigned-corim-map` (choice)
3. Extracts inner type → `unsigned-corim-map` (tag definition)
4. Resolves alias → `corim-map` (final type)

**EDN output:**
```edn
/ tagged-unsigned-corim-map / 501(
  / corim-map / {
    ...
  }
)
```

### Example 2: Choice Type Matching
```cddl
$measured-element-type-choice /= {
  ? &(version: 4) => version-map
  ? &(svn: 1) => svn-type
  ...
}
```

**Tool behavior:**
- Examines the actual CBOR data
- Checks which fields are present
- Resolves to the matching choice variant
- Shows resolved type in EDN

## CoRIM Support

Extensively tested against the CoRIM (Concise Reference Integrity Manifest) unified schema.

### Test Results
- ✅ **903-line schema** fully parsed
- ✅ **98 IANA parameters** all recognized
- ✅ **46 type choices** all resolved
- ✅ **39 CBOR tags** all decoded
- ✅ **10 nested CBOR** instances decoded
- ✅ **Deep nesting** (5+ levels) handled correctly

### Validated Features
- Multi-level type alias resolution
- Nested CBOR in CoMID/CoSWID tags
- Complex choice types with multiple variants
- Deeply nested maps and arrays
- IANA registered parameters at all levels
- Size constraints on UUIDs and hashes

## Advanced Features

### Schema Analysis

Get detailed statistics about your CDDL schema:

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

Output includes:
- All type definitions with fields
- CDDL groups and their contents
- Type choices and alternatives
- Socket extensions
- IANA registered parameter mappings
- Control operator usage

### Verbose Mode

See exactly how the tool resolves types:

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --type corim --verbose
```

Shows:
- Type alias resolution steps
- Type choice matching logic
- Tag extraction process
- Nested CBOR decoding
- Field validation details

## Documentation

Comprehensive documentation available in `/docs`:

- **EDN_FORMATTING_IMPROVEMENTS.md** - Details on annotation formatting
- **TYPE_NAME_ANNOTATIONS.md** - Type header feature guide
- **CBOR_TAG_NOTATION.md** - Tag notation implementation
- **IANA_ANNOTATIONS_STATUS.md** - IANA parameter support
- **CORIM_SUPPORT.md** - CoRIM compatibility testing

## Testing

### Test Files Included

```
test-data/
├── minimal-corim.cbor          # Minimal CoRIM structure
├── test_doc1.cbor              # Type choice examples
└── example_iana.cbor           # IANA parameter examples

cddl-schemas/
├── unified.cddl                # CoRIM unified schema (903 lines)
├── test_groups_choices.cddl    # Groups and choices
└── example_iana.cddl           # IANA parameters
```

### Run Tests

```bash
# Test CoRIM with full validation
python cbor_cddl_analyzer.py \
  cddl-schemas/unified.cddl \
  test-data/unsigned-good-corim.cbor \
  --type corim \
  --output corim-output.edn

# Test type choices
python cbor_cddl_analyzer.py \
  cddl-schemas/test_groups_choices.cddl \
  test-data/test_doc1.cbor \
  --type document \
  --verbose

# Test IANA parameters
python cbor_cddl_analyzer.py \
  cddl-schemas/example_iana.cddl \
  test-data/example_iana.cbor \
  --type message
```

## Implementation Highlights

### Parser Enhancements
- **Multi-line field parsing** - handles CDDL fields split across lines
- **Structured array types** - converts named array fields to indexed types
- **Tag definition parsing** - correctly distinguishes tags from groups
- **Size constraint extraction** - regex-based constraint parsing

### Validation Improvements
- **Type choice resolution** - matches CBOR data to choice alternatives
- **Tag type extraction** - extracts inner types from `#6.N(type)` notation
- **Size validation** - checks exact, min, max, and range constraints
- **Nested CBOR handling** - recursive decoding with `.cbor` operator

### EDN Generation Features
- **Relative indentation** - tags generate without absolute positioning
- **Absolute positioning** - content lines include full indentation
- **Type name resolution** - uses final resolved types, not aliases
- **Annotation consistency** - all annotations left-aligned

## Known Limitations

1. **Complex constraints** - `.and`, `.within`, `.lt`, `.gt` not validated
2. **Group expansion** - Groups displayed but not expanded into parent types
3. **Regexp validation** - `.regexp` control operator not enforced
4. **CBOR sequences** - Single items only, not CBOR sequences

## Future Enhancements

Planned improvements:
1. Full constraint validation (`.and`, `.within`, ranges)
2. Group expansion into parent type definitions
3. CBOR sequence support
4. Regexp pattern validation
5. Default value handling

## Contributing

To extend functionality:

1. **Add parsing**: Update `CDDLParser.parse()` for new CDDL features
2. **Add validation**: Extend `CBORAnalyzer._validate_type()` for new checks
3. **Update EDN**: Modify `EDNGenerator._generate_value()` for new formats
4. **Add tests**: Create test files in `test-data/`
5. **Document**: Update relevant `.md` files in `docs/`

## Version History

### Current Version
- Automatic type resolution from type choices
- Tag type extraction and display
- Size constraint validation
- Nested CBOR decoding
- Left-aligned annotations
- CBOR tag notation (RFC 8949)
- Perfect indentation alignment

### Recent Improvements
- Removed workarounds (tags parse correctly now)
- Fixed tag parsing vs group parsing
- Added `.size` constraint support
- Automatic validation with `--type`
- Type name headers on maps/arrays

## References

- **CBOR**: RFC 8949 - Concise Binary Object Representation
- **CDDL**: RFC 8610 - Concise Data Definition Language
- **CoRIM**: draft-ietf-rats-corim (IETF RATS WG)
- **EDN**: RFC 8610 Section 8 - Diagnostic Notation

## License

This tool is provided for CBOR/CDDL schema development and testing.

## Acknowledgments

Developed and tested extensively against real-world schemas including:
- CoRIM unified schema (IETF RATS Working Group)
- Complex nested structures with 5+ levels
- Multiple CBOR tag types
- Comprehensive IANA registered parameters

Special thanks to the IETF RATS WG for providing comprehensive test schemas.