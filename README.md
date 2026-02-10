# CBOR-CDDL Analyzer

A Python tool for analyzing CBOR (Concise Binary Object Representation) data against CDDL (Concise Data Definition Language) schemas and generating annotated EDN (Extended Diagnostic Notation) output.

## Project Structure

```
.
├── cbor_cddl_analyzer.py      # Main analyzer tool
├── analyze_cddl.py             # CDDL schema analysis utility
├── requirements.txt            # Python dependencies
├── cddl-schemas/              # CDDL schema files
│   ├── unified.cddl           # CoRIM unified schema (903 lines)
│   ├── corim_test.cddl        # CoRIM test schema
│   ├── test_groups_choices.cddl
│   └── ...
├── test-data/                 # CBOR test files and generators
│   ├── minimal-corim.cbor     # Minimal CoRIM example
│   ├── generate_corim.py      # CoRIM CBOR generator
│   ├── test_doc1.cbor
│   └── ...
└── docs/                      # Documentation
    ├── CORIM_SUPPORT.md       # CoRIM compatibility guide
    ├── GROUPS_CHOICES.md      # Groups and type choices
    ├── WHITESPACE_HANDLING.md # Whitespace flexibility
    ├── IANA_PARAMETERS.md     # IANA parameter support
    └── README.md              # This file
```

## Features

### Core Functionality
- ✅ Load and parse CDDL schema files
- ✅ Load CBOR binary files (with built-in decoder)
- ✅ Validate CBOR against CDDL schema
- ✅ Generate annotated EDN with field names from CDDL
- ✅ Extract and display schema information

### CDDL Features Supported

#### Fully Supported
- **IANA Registered Parameters** - `&( keyname : keyindex ) => type`
  - Three output formats: keyindex (default), keyname, both
  - Recursive annotation for nested structures
- **Type Aliases** - `name = other_name` with automatic resolution
- **Type Choices** - `$name /= alternative`
- **Groups** - `name = ( fields )`
- **Socket Extensions** - `$$name //= extension`
- **Optional Fields** - `?` prefix
- **CBOR Tags** - `#6.xxx(type)`
- **Map and Array Types** - `{ }` and `[ ]`
- **Named Array Fields** - `fieldname: type` in arrays
- **Generics** - `name<M>` (basic support)
- **Comments** - `;` comments for documentation

#### Partially Supported
- **Control Operators** - `.cbor`, `.size`, `.default`, `.bits`, etc.
  - Parsed and preserved in type information
  - Constraints not validated

#### Not Supported
- Complex constraints validation (`.and`, `.within`)
- Group expansion into parent types
- Type choice validation (doesn't check if value matches alternatives)
- Nested CBOR decoding (`.cbor` content shown as hex bytes)

## Installation

### Prerequisites
Python 3.7 or higher

### Dependencies (Optional)
For enhanced CBOR support:
```bash
pip install cbor2
```

The tool includes a built-in CBOR decoder, so external dependencies are optional.

## Usage

### Basic Commands

```bash
# Show parsed CDDL schema types
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types

# Generate EDN output
python cbor_cddl_analyzer.py schema.cddl data.cbor

# Save EDN to file
python cbor_cddl_analyzer.py schema.cddl data.cbor --output result.edn

# Validate CBOR against schema
python cbor_cddl_analyzer.py schema.cddl data.cbor --validate --type typename

# Analyze CDDL schema
python analyze_cddl.py schema.cddl
```

### CoRIM Example

```bash
# Analyze CoRIM unified schema
python analyze_cddl.py cddl-schemas/unified.cddl

# Parse minimal CoRIM
python cbor_cddl_analyzer.py cddl-schemas/unified.cddl test-data/minimal-corim.cbor

# Validate against corim-map type
python cbor_cddl_analyzer.py cddl-schemas/unified.cddl test-data/minimal-corim.cbor \
  --validate --type corim-map --output corim-output.edn

# Show all parsed types, choices, and groups
python cbor_cddl_analyzer.py cddl-schemas/unified.cddl test-data/minimal-corim.cbor --show-types
```

### Output Example

With IANA registered parameters, the EDN output shows keyindex with semantic names as comments:

**CBOR (binary):**
```
{0: "value1", 1: "value2", 2: 42}
```

**CDDL Schema:**
```cddl
record = {
  &( name : 0 ) => tstr,
  &( email : 1 ) => tstr,
  &( age : 2 ) => uint,
}
```

**Generated EDN:**
```edn
{
  0: "value1",  / name /
  1: "value2",  / email /
  2: 42  / age /
}
```

This format preserves the binary representation (numeric keys) while showing the semantic meaning (comments).

## CoRIM Support

The analyzer has been tested against the CoRIM (Concise Reference Integrity Manifest) unified schema (903 lines, draft-ietf-rats-corim-09).

### Tested Features
- ✅ 39 type definitions (20 maps, 19 arrays)
- ✅ 46 type choices
- ✅ 98 IANA registered parameters
- ✅ 39 CBOR tags
- ✅ 9 socket extensions
- ✅ Named array fields
- ✅ Complex nested structures

### CoRIM Schema Statistics
```
Total lines: 903
Type Definitions: 39 (20 maps, 19 arrays)
Type Choices: 46
Groups: 3
IANA Parameters: 98
CBOR Tags: 39
Socket Extensions: 9
Control Operators:
  .cbor: 10
  .size: 4
  .bits: 1
  .default: 1
  .and: 1
```

## Testing

### Generate Test Data

```bash
# Generate minimal CoRIM
cd test-data
python generate_corim.py

# Generate test documents with type choices
python generate_test_choices.py

# Generate IANA parameter examples
python generate_iana_cbor.py
```

### Run Tests

```bash
# Test groups and type choices
python cbor_cddl_analyzer.py cddl-schemas/test_groups_choices.cddl \
  test-data/test_doc1.cbor --validate --type document

# Test IANA parameters
python cbor_cddl_analyzer.py cddl-schemas/example_iana.cddl \
  test-data/example_iana.cbor --validate --type message

# Test CoRIM
python cbor_cddl_analyzer.py cddl-schemas/unified.cddl \
  test-data/minimal-corim.cbor --validate --type corim-map
```

## Documentation

Comprehensive guides are available in the `docs/` directory:

- **CORIM_SUPPORT.md** - CoRIM schema compatibility and testing
- **GROUPS_CHOICES.md** - CDDL groups and type choices usage
- **IANA_PARAMETERS.md** - IANA registered parameter format
- **WHITESPACE_HANDLING.md** - Flexible whitespace parsing

## Command-Line Options

```
positional arguments:
  cddl_file             Path to CDDL schema file
  cbor_file             Path to CBOR data file

optional arguments:
  -h, --help            Show help message
  -o, --output PATH     Output EDN file (default: stdout)
  -t, --type TYPE       Root type name from CDDL for validation
  -v, --validate        Validate CBOR against CDDL
  -a, --annotate        Annotate EDN with field names (default: True)
  --no-annotate         Disable annotations in EDN output
  --edn-format {keyindex,keyname,both}
                        EDN key format (default: keyindex)
                        - keyindex: 0: value  / name /
                        - keyname: "name": value
                        - both: 0 / name /: value
  --show-types          Show parsed CDDL types and exit
```

## EDN Format Options

The analyzer supports **three EDN output formats** for maximum flexibility:

### Format: keyindex (default)
Preserves binary structure with semantic annotations:
```edn
{
  0: "value",  / id /
  1: {  / data /
    0: 42  / count /
  }
}
```
✅ Round-trip compatible, shows binary structure

### Format: keyname
Uses semantic names as keys:
```edn
{
  "id": "value",
  "data": {
    "count": 42
  }
}
```
✅ Highly readable, JSON-like

### Format: both
Shows both representations:
```edn
{
  0 / id /: "value",
  1 / data /: {
    0 / count /: 42
  }
}
```
✅ Complete information, educational

See [EDN_FORMAT_OPTIONS.md](docs/EDN_FORMAT_OPTIONS.md) for detailed guide.

## Advanced Features

### Schema Analysis

The `analyze_cddl.py` utility provides detailed schema analysis:

```bash
python analyze_cddl.py cddl-schemas/unified.cddl
```

Output includes:
- Type definition counts
- Feature usage statistics
- Unsupported construct detection
- Support status summary

### Type Information Display

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

Shows:
- All parsed types with fields
- CDDL groups
- Type choices
- Socket extensions
- IANA registered parameters

## Limitations

1. **Group Expansion** - Groups are displayed but not expanded into parent types
2. **Type Choice Validation** - Value matching against alternatives not validated
3. **Constraint Validation** - Control operators parsed but constraints not checked
4. **Nested CBOR** - `.cbor` embedded content shown as hex bytes

## Future Enhancements

Priority improvements:
1. Group expansion into parent type definitions
2. Type choice validation against alternatives
3. Control operator constraint validation
4. Nested CBOR automatic decoding
5. Socket extension expansion

## Contributing

To add support for new CDDL features:

1. Add parsing logic to `CDDLParser.parse()`
2. Add validation logic to `CBORAnalyzer.validate()`
3. Update EDN generation in `EDNGenerator` if needed
4. Add tests in `test-data/`
5. Update documentation

## License

This tool is provided for CBOR/CDDL schema development and testing.

## References

- CBOR: RFC 8949
- CDDL: RFC 8610
- CoRIM: draft-ietf-rats-corim-09
- EDN: RFC 8610 Section 8

## Acknowledgments

Developed with extensive testing against the CoRIM unified schema from the IETF RATS Working Group.
