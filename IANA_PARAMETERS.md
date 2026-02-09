# IANA Registered Parameters - Comparison

## Overview

IANA registered parameters allow CBOR to use compact numeric keys (keyindex) in the binary format while preserving semantic meaning through registered names (keyname).

## CDDL Syntax

```cddl
message = {
  &( msg_type : 1 ) => uint,
  &( payload : 2 ) => tstr,
  &( timestamp : 3 ) => uint,
}
```

- `&(...)` indicates an IANA registered parameter
- `msg_type` is the keyname (semantic identifier)
- `1` is the keyindex (used in CBOR binary)
- `uint` is the value type

## CBOR Binary Format

In the CBOR file, data is stored using numeric keyindex for efficiency:

```
Binary: A3 01 1864 02 6D48656C6C6F2C20576F726C6421 03 1A61F5E000
Decoded: {1: 100, 2: "Hello, World!", 3: 1640995200}
```

## EDN Output (Human-Readable)

When generating EDN with type information, the keyindex is preserved as the key, and the keyname is shown as a comment:

```edn
{
  1: 100,  / msg_type /
  2: "Hello, World!",  / payload /
  3: 1640995200  / timestamp /
}
```

This format:
- **Preserves the binary representation** (numeric keys 1, 2, 3)
- **Shows the semantic meaning** (comments: msg_type, payload, timestamp)
- **Makes EDN round-trippable** (can recreate CBOR from EDN)

## Comparison Table

| Format | Key Representation | Use Case |
|--------|-------------------|----------|
| CBOR Binary | Numeric keyindex (1, 2, 3) | Transmission, storage (compact) |
| EDN with Schema | Keyindex + name comment (1: value / name /) | Human reading, debugging, round-trip |
| EDN without Schema | Numeric keyindex only (1: value) | When schema not available |

## Benefits

1. **Compact Binary**: CBOR uses small integers as keys
2. **Readable Output**: EDN uses meaningful names
3. **Standardization**: IANA registry ensures consistency
4. **Interoperability**: Different implementations understand same keys

## Example Commands

### With type information (shows keyindex + name):
```bash
python cbor_cddl_analyzer_standalone.py schema.cddl data.cbor --type message
```

Output:
```edn
{
  1: 100,  / msg_type /
  2: "Hello, World!"  / payload /
}
```

### Without type information (shows keyindex):
```bash
python cbor_cddl_analyzer_standalone.py schema.cddl data.cbor --no-annotate
```

Output:
```edn
{
  1: 100,
  2: "Hello, World!"
}
```
