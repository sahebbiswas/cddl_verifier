# EDN Output Format Reference

## Overview

The CBOR-CDDL Analyzer generates EDN (Extended Diagnostic Notation) that **preserves the binary CBOR structure** while adding **semantic annotations** from the CDDL schema.

## Format Philosophy

**Goal:** Make CBOR human-readable while maintaining round-trip compatibility.

- **Keys remain numeric** (matching CBOR binary)
- **Semantic names shown as comments** (from CDDL schema)
- **EDN can recreate exact CBOR** (no information loss)

## IANA Registered Parameters

### CDDL Definition
```cddl
message = {
  &( msg_type : 1 ) => uint,
  &( payload : 2 ) => tstr,
  &( timestamp : 3 ) => uint,
}
```

### CBOR Binary
```
A3 01 1864 02 6D... 03 1A...
{1: 100, 2: "Hello, World!", 3: 1640995200}
```

### EDN Output (Annotated)
```edn
{
  1: 100,  / msg_type /
  2: "Hello, World!",  / payload /
  3: 1640995200  / timestamp /
}
```

### EDN Output (No Annotations)
```edn
{
  1: 100,
  2: "Hello, World!",
  3: 1640995200
}
```

## Regular Fields with Comments

### CDDL Definition
```cddl
person = {
  0: tstr,  ; name
  1: uint,  ; age
  2: tstr,  ; email
}
```

### EDN Output
```edn
{
  0: "Alice",  / name /
  1: 28,  / age /
  2: "alice@example.com"  / email /
}
```

## Nested Structures

### CDDL Definition
```cddl
record = {
  &( id : 0 ) => tstr,
  &( data : 1 ) => {
    &( value : 0 ) => uint,
    &( label : 1 ) => tstr,
  }
}
```

### EDN Output
```edn
{
  0: "rec-001",  / id /
  1: {  / data /
    0: 42,  / value /
    1: "example"  / label /
  }
}
```

## Arrays

### CDDL Definition
```cddl
list = {
  &( items : 0 ) => [ * tstr ],
  &( count : 1 ) => uint,
}
```

### EDN Output
```edn
{
  0: [  / items /
    "apple",
    "banana",
    "cherry"
  ],
  1: 3  / count /
}
```

## Optional Fields

### CDDL Definition
```cddl
profile = {
  &( name : 0 ) => tstr,
  ? &( email : 1 ) => tstr,
  ? &( phone : 2 ) => tstr,
}
```

### EDN Output (All Fields Present)
```edn
{
  0: "Alice",  / name /
  1: "alice@example.com",  / email /
  2: "+1-555-0100"  / phone /
}
```

### EDN Output (Optional Fields Missing)
```edn
{
  0: "Bob"  / name /
}
```

## Type Choices

### CDDL Definition
```cddl
$id-choice /= tstr
$id-choice /= uint

record = {
  &( id : 0 ) => $id-choice,
}
```

### EDN Output (String Variant)
```edn
{
  0: "ID-12345"  / id /
}
```

### EDN Output (Integer Variant)
```edn
{
  0: 42  / id /
}
```

## CoRIM Example

### CDDL (Simplified)
```cddl
corim-map = {
  &( id : 0 ) => tstr,
  &( tags : 1 ) => [ + concise-tag ],
}

tagged-unsigned-corim-map = #6.501(corim-map)
```

### CBOR Binary
```
D9 01F5           ; CBOR tag 501
  A2              ; map(2)
    00            ; key 0
      78 21 ...   ; text string "urn:example..."
    01            ; key 1
      81          ; array(1)
        D9 01FA   ; CBOR tag 506
          58 ...  ; bytes (nested CBOR)
```

### EDN Output
```edn
{
  0: "urn:example:corim:minimal-example",  / id /
  1: [  / tags /
    h'a201a1007775726e3a6578616d706c653a636f6d69643a313233343504a0'
  ]
}
```

**Notes:**
- CBOR tag 501 is decoded (outer structure shown)
- Nested CBOR (tag 506 with `.cbor`) shown as hex bytes
- Keys remain numeric (0, 1)
- Comments show semantic meaning (id, tags)

## Benefits of This Format

### 1. Round-Trip Compatible
```bash
# EDN → Parse → Validate → CBOR
# Result: Exact same binary as original
```

### 2. Debugging Friendly
- **See actual CBOR structure** (numeric keys)
- **Understand semantic meaning** (comment names)
- **No ambiguity** about what's in the binary

### 3. Diff-Friendly
```diff
  {
-   0: "old-id",  / id /
+   0: "new-id",  / id /
    1: [...]  / tags /
  }
```
Changes are clear in both structure and meaning.

### 4. Educational
Learn the mapping between:
- **Binary representation** (keyindex 0, 1, 2...)
- **Semantic meaning** (id, tags, name...)
- **IANA registry** (standardized mappings)

## Comparison: Old vs New Format

### Old Format (Replaced Keys)
```edn
{
  "id": "value",
  "tags": [...]
}
```
**Problems:**
- ❌ Lost binary structure information
- ❌ Not round-trip compatible
- ❌ Unclear if using keyindex or keyname
- ❌ Can't recreate exact CBOR

### New Format (Preserved Keys + Comments)
```edn
{
  0: "value",  / id /
  1: [...]  / tags /
}
```
**Benefits:**
- ✅ Preserves binary structure
- ✅ Round-trip compatible
- ✅ Clear keyindex → keyname mapping
- ✅ Can recreate exact CBOR

## Command-Line Control

### Generate with Annotations
```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --annotate
```

### Generate without Annotations
```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --no-annotate
```

Output:
```edn
{
  0: "value",
  1: [...]
}
```

## EDN Specification

This format follows RFC 8610 (CDDL) Appendix G - Extended Diagnostic Notation:

- **Numeric keys:** Standard EDN
- **Comments:** `/ comment /` style from CDDL
- **Hex bytes:** `h'...'` notation
- **CBOR tags:** Decoded automatically

## Summary

**The EDN output format is designed to:**
1. **Preserve CBOR binary structure** (numeric keys)
2. **Add semantic annotations** (comment names)
3. **Enable round-trip conversion** (EDN → CBOR)
4. **Aid debugging and understanding** (show both representations)

This makes it ideal for development, testing, and documentation of CBOR/CDDL systems.
