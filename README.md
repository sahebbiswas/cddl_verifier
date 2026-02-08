# CBOR-CDDL Analyzer and EDN Generator

A Python tool for analyzing CBOR (Concise Binary Object Representation) data against CDDL (Concise Data Definition Language) schemas and generating annotated EDN (Extended Diagnostic Notation) output.

## Features

- **Load and parse CDDL schema files** - Extract type definitions and field names
- **Load CBOR binary files** - Parse CBOR-encoded data
- **Validate CBOR against CDDL** - Check if data conforms to schema
- **Generate annotated EDN** - Create human-readable EDN with field name annotations from CDDL

## Installation

### Prerequisites

Python 3.7 or higher

### Install Dependencies

```bash
pip install cbor2
```

Or using the requirements file:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
# Generate annotated EDN to stdout
python cbor_cddl_analyzer.py schema.cddl data.cbor

# Save EDN to a file
python cbor_cddl_analyzer.py schema.cddl data.cbor --output data.edn

# Validate CBOR against CDDL schema
python cbor_cddl_analyzer.py schema.cddl data.cbor --validate --type person

# Generate EDN without annotations
python cbor_cddl_analyzer.py schema.cddl data.cbor --no-annotate
```

### Command-Line Options

```
positional arguments:
  cddl_file             Path to CDDL schema file
  cbor_file             Path to CBOR data file

optional arguments:
  -h, --help            Show help message and exit
  -o, --output PATH     Output EDN file (default: stdout)
  -t, --type TYPE       Root type name from CDDL for validation
  -v, --validate        Validate CBOR against CDDL
  -a, --annotate        Annotate EDN with field names (default: True)
  --no-annotate         Disable annotations in EDN output
  --show-types          Show parsed CDDL types and exit
```

### Examples

#### Example 1: Show Parsed CDDL Types

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor --show-types
```

Output:
```
Parsed CDDL Types:
==================================================

person (map):
  0: 0 -> tstr
  1: 1 -> uint
  2: 2 -> tstr
  3: 3 -> address
  4: 4 -> [* tstr] (optional)

address (map):
  0: 0 -> tstr
  1: 1 -> tstr
  2: 2 -> tstr
  3: 3 -> uint (optional)
```

#### Example 2: Validate and Generate Annotated EDN

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor \
  --validate --type person --output output.edn
```

Output (output.edn):
```
{
  0: "Alice Johnson",  / name /
  1: 28,  / age /
  2: "alice@example.com",  / email /
  3: {  / address /
    0: "123 Main Street",  / street /
    1: "Springfield",  / city /
    2: "USA",  / country /
    3: 12345  / postal_code /
  },
  4: [  / hobbies /
    "reading",
    "hiking",
    "photography"
  ]
}
```

#### Example 3: Generate EDN Without Validation

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor
```

#### Example 4: IANA Registered Parameters

CDDL schema with registered parameters:
```cddl
message = {
  &( msg_type : 1 ) => uint,
  &( payload : 2 ) => tstr,
  &( timestamp : 3 ) => uint,
}
```

Generate EDN with semantic keynames:
```bash
python cbor_cddl_analyzer_standalone.py example_iana.cddl example_iana.cbor \
  --validate --type message --output output.edn
```

Output shows keynames instead of keyindexes:
```edn
{
  "msg_type": 100,
  "payload": "Hello, World!",
  "timestamp": 1640995200
}
```

Without type information (raw CBOR display):
```bash
python cbor_cddl_analyzer_standalone.py example_iana.cddl example_iana.cbor --no-annotate
```

Output shows original numeric keys:
```edn
{
  1: 100,
  2: "Hello, World!",
  3: 1640995200
}
```

## CDDL Schema Format

The tool supports a simplified CDDL syntax for defining data structures:

### Map Types

```cddl
person = {
  0: tstr,           ; name (required)
  1: uint,           ; age (required)
  2: tstr ?,         ; email (optional)
}
```

### IANA Registered Parameters

For space efficiency, CBOR often uses numeric keys (keyindex) in the binary format, but these should be represented by their semantic names (keyname) in human-readable formats like EDN. The tool supports IANA registered parameter syntax:

```cddl
message = {
  &( msg_type : 1 ) => uint,
  &( payload : 2 ) => tstr,
  &( timestamp : 3 ) => uint,
}
```

**How it works:**
- In the CBOR binary file, the map uses numeric keys (1, 2, 3) to save space
- In the generated EDN output, these are automatically converted to their semantic names ("msg_type", "payload", "timestamp")
- The syntax `&( keyname : keyindex ) => type` tells the parser that keyindex should be displayed as keyname

**Example:**

CBOR binary (uses keyindex):
```
{1: 100, 2: "Hello", 3: 1640995200}
```

Generated EDN (uses keyname):
```edn
{
  "msg_type": 100,
  "payload": "Hello",
  "timestamp": 1640995200
}
```

### Mixed Format

You can mix IANA registered parameters with regular numeric keys:

```cddl
mixed = {
  &( id : 0 ) => tstr,        ; Shows as "id" in EDN
  1: uint,                     ; Shows as 1 with comment in EDN
  &( status : 2 ) => tstr,    ; Shows as "status" in EDN
  3: tstr,                     ; Shows as 3 with comment in EDN
}
```

### Array Types

```cddl
items = [
  0: tstr,
  1: uint,
]
```

### Supported CDDL Types

- `tstr` - text string
- `uint` - unsigned integer
- `int` - signed integer
- `bstr` - byte string
- `bool` - boolean
- `float` - floating point
- Custom types (references to other definitions)

### Optional Fields

Mark fields as optional using the `?` suffix:

```cddl
person = {
  name: tstr,
  email: tstr ?,     ; optional field
}
```

## EDN Output Format

The generated EDN (Extended Diagnostic Notation) is a human-readable representation of CBOR data:

- **Maps**: Represented as `{key: value, ...}`
- **Arrays**: Represented as `[item1, item2, ...]`
- **Strings**: Quoted text `"string"`
- **Numbers**: Plain numbers `42`, `3.14`
- **Bytes**: Hex format `h'48656c6c6f'`
- **Annotations**: Comments with field names `/ field_name /`

## Limitations

- The CDDL parser supports a simplified subset of the full CDDL specification
- Complex CDDL features like choices, groups, and advanced validators are not fully supported
- Validation is basic and focuses on structure rather than detailed type checking

## Creating Your Own CBOR Files

You can create CBOR files using Python:

```python
import cbor2

data = {
    0: "Alice",
    1: 28,
    2: {"street": "123 Main St", "city": "Boston"}
}

with open('data.cbor', 'wb') as f:
    cbor2.dump(data, f)
```

## License

This script is provided as-is for educational and development purposes.

## Contributing

Feel free to extend the CDDL parser to support more features or improve validation logic.
