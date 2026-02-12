# IANA Annotations in Nested CBOR - COMPLETE

## Status: [OK] FULLY WORKING

IANA parameter annotations now work correctly at ALL nesting levels, including deeply nested structures in arrays.

### What Works [OK]

#### Top-level fields
```edn
{
  0: "test corim id",  / id /
  1: [...]  / tags /
}
```
[OK] Annotations present

#### First-level nested structures
```edn
{
  "entities": [...],
  "triples": {...},
  0: "en-GB",  / language /
  1: {...}  / tag-identity /
}
```
[OK] Annotations present for concise-mid-tag fields

#### Entity maps in arrays
```edn
"entities": [
  {
    0: "ACME Ltd.",  / entity-name /
    1: "https://acme.example",  / reg-id /
    2: [0, 1, 2]  / role /
  }
]
```
[OK] Annotations present for entity-map fields

#### Deeply nested structures in triples [OK] FIXED

Reference triples now have complete annotations:
```edn
"triples": {
  "reference-triples": [
    [
      {
        0: {
          1: "ACME",      / vendor /
          2: "RoadRunner", / model /
          0: h'...'        / class-id /
        }
      },
      [
        {
          0: {...},  / mkey /
          1: {...}   / mval /
        }
      ]
    ]
  ]
}
```

[OK] All class-map fields have annotations (vendor, model, class-id)
[OK] All measurement fields have annotations (mkey, mval, digests)

## Technical Solution

### Problem Identified

Multi-line CDDL field definitions were not being parsed correctly:
```cddl
? &(reference-triples: 0) =>
  [ + reference-triple-record ]
```

The parser only read the first line ending with `=>`, missing the type on the next line.

### Fix Implemented

**1. Multi-line Field Parsing**
- Added `pending_field_line` variable to track incomplete field definitions
- When a registered parameter line ends with `=>` but has no type, mark it as pending
- On the next non-empty line, concatenate with pending line and re-parse
- Properly captures type information from continuation lines

**2. Structured Array Element Types**
- Parse array definitions to extract element types by index
- Store in `element_types` dict: `{0: 'environment-map', 1: '[ + measurement-map ]'}`
- During EDN generation, use indexed types for structured arrays
- Pass correct type through to nested structures

**3. Type Propagation**
- Field types are passed from maps to nested structures
- Array element types are resolved and passed to elements  
- Structured arrays use indexed element types
- Type information flows through all nesting levels

### Code Changes

**Parser Enhancement (`parse` method):**
```python
pending_field_line = None

# Handle continuation lines
if pending_field_line:
    continuation = line.rstrip(',').strip()
    if continuation:
        full_line = pending_field_line + ' ' + continuation
        pending_field_line = None
        self._parse_registered_param(full_line, current_fields)

# Detect incomplete fields
if '&(' in line and '=>' in line:
    value_after_arrow = line[arrow_pos + 2:].strip()
    if not value_after_arrow:
        pending_field_line = line  # Type on next line
        continue
```

**Array Post-processing:**
```python
# Convert named array fields to indexed element_types
for type_name, type_def in self.types.items():
    if type_def.get('type') == 'array':
        element_types = {}
        for idx, (field_name, field_info) in enumerate(fields.items()):
            element_type = field_info.get('type')
            element_types[idx] = element_type
        type_def['element_types'] = element_types
```

**EDN Generation:**
```python
# Check for structured arrays
type_def = self.cddl.get_type(type_name)
if type_def and type_def.get('type') == 'array':
    element_types_by_index = type_def.get('element_types', {})
    
# Use indexed types
if element_types_by_index and i in element_types_by_index:
    resolved_element_type = element_types_by_index[i]
```

## Testing

Verify annotations at all levels:

```bash
# Deep nesting (now works!)
python cbor_cddl_analyzer.py unified.cddl corim.cbor --type corim --edn-format keyindex \
  | grep -A 3 "ACME"
```

**Output:**
```edn
{
  0: {
    1: "ACME",      / vendor /
    2: "RoadRunner", / model /
    0: h'...'        / class-id /
  }
}
```

## Summary

All annotation levels now working:
- [OK] Top-level structures
- [OK] Simple nested maps
- [OK] First-level arrays
- [OK] Array elements that are maps
- [OK] Arrays containing arrays (multi-level nesting)
- [OK] Deeply nested map fields in array elements
- [OK] All IANA registered parameters at any depth

The parser correctly handles:
- [OK] Multi-line CDDL field definitions
- [OK] Structured array types with indexed elements
- [OK] Type propagation through N levels of nesting
- [OK] IANA parameter resolution at all depths
