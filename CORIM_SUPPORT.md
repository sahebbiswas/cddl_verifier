# CoRIM CDDL Support

This document describes the support for CoRIM (Concise Reference Integrity Manifest) CDDL schemas in the CBOR-CDDL Analyzer.

## Overview

CoRIM (draft-ietf-rats-corim-09) is an IETF RATS specification that defines a format for encoding Reference Values and Endorsements for remote attestation. The analyzer has been tested against CoRIM CDDL schemas and supports the key features needed for parsing CoRIM documents.

## Supported CoRIM CDDL Features

### ✓ IANA Registered Parameters
CoRIM extensively uses IANA registered parameters with the `&( keyname : keyindex ) => type` syntax.

**Example from CoRIM:**
```cddl
corim-map = {
  & ( corim-id : 0 ) => $corim-id-type-choice,
  & ( tags : 1 ) => [ + $concise-tag-type-choice ],
  ? & ( profile : 3 ) => uri,
}
```

**Support:** ✓ FULL
- Correctly parses registered parameters
- Handles whitespace variations
- EDN output uses keynames instead of keyindexes

### ✓ Optional Fields
CoRIM uses `?` prefix to denote optional fields.

**Example:**
```cddl
validity-map = {
  ? & ( not-before : 0 ) => time,
  ? & ( not-after : 1 ) => time,
}
```

**Support:** ✓ FULL
- Parses `?` prefix for both IANA parameters and regular fields
- Correctly marks fields as optional in type display

### ✓ Map and Array Types
CoRIM defines both map (`{}`) and array (`[]`) types.

**Support:** ✓ FULL
- Correctly identifies map vs array types
- Parses field definitions within maps
- Handles array type definitions

### ✓ Comments
CoRIM CDDL uses semicolon comments for documentation.

**Example:**
```cddl
comid-map = {
  & ( comid-id : 0 ) => comid-id-type,  ; Unique identifier
  ? & ( entities : 1 ) => [ + comid-entity-map ],  ; Involved entities  
}
```

**Support:** ✓ FULL
- Strips comments during parsing
- Uses comments as field name hints

### ⚠ CBOR Tags
CoRIM uses CBOR tags with `#6.xxx(type)` notation.

**Example:**
```cddl
$concise-tag-type-choice /= #6.505(comid-map)
$concise-tag-type-choice /= #6.506(coswid-map)
tagged-uuid-type = #6.37(uuid-type)
```

**Support:** ⚠ PARTIAL
- Parses CBOR tag syntax
- Extracts underlying type (e.g., `#6.37(uuid-type)` → `uuid-type`)
- Does NOT validate CBOR tag numbers during decode
- Tag information is preserved in type definitions

### ⚠ Type Choices ($name /= value)
CoRIM uses type choice extensions heavily.

**Example:**
```cddl
$corim-id-type-choice /= tstr
$corim-id-type-choice /= uuid-type

$corim-role-type-choice /= & ( manifest-creator : 1 )
$corim-role-type-choice /= & ( manifest-signer : 2 )
```

**Support:** ⚠ PARTIAL
- Recognizes `/=` syntax
- Skips type choice lines (doesn't fully expand choices)
- References to `$name` types are preserved in field definitions
- Future enhancement: expand choices to show all possible types

### ⚠ Generics
CoRIM defines generic types like `non-empty<M>`.

**Example:**
```cddl
non-empty<M> = (M) .within ({ + any => any })

validity-map = non-empty<{
  ? & ( not-before : 0 ) => time,
  ? & ( not-after : 1 ) => time,
}>
```

**Support:** ⚠ PARTIAL
- Parses generic type definitions
- Strips generic parameters (e.g., `<M>`) from type names
- Does NOT instantiate or validate generic constraints
- Treats instantiated generics as regular types

### ⚠ CDDL Control Operators
CoRIM uses operators like `.size`, `.within`, `.cbor`, etc.

**Example:**
```cddl
uuid-type = bstr .size 16
eui-48 = bstr .size 6
tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
```

**Support:** ⚠ PARTIAL
- Recognizes control operator syntax
- Does NOT validate constraints (e.g., `.size 16`)
- Operators are treated as part of type description
- Future enhancement: constraint validation

### ✓ Nested Structures
CoRIM has deeply nested type definitions.

**Example:**
```cddl
triples-map = {
  ? & ( reference-values : 0 ) => [ + reference-triple-record ],
  ? & ( endorsed-values : 1 ) => [ + endorsed-triple-record ],
}

reference-triple-record = [
  environment-map,
  measurement-map,
]
```

**Support:** ✓ FULL
- Correctly parses nested map and array definitions
- Maintains type references
- Handles multiple levels of nesting

## CoRIM-Specific Test Results

### Test Case: CoRIM Base Schema

**Input CDDL:** Simplified CoRIM schema (based on draft-ietf-rats-corim-09)

**Parsed Types:** 40+ type definitions including:
- `corim-map` (top-level CoRIM structure)
- `comid-map` (Concise Module Identifier)
- `triples-map` (reference values, endorsed values)
- `environment-map` (attesting environment)
- `measurement-map` (measurement values)
- `validity-map` (time validity)
- Various triple record types

**Result:** ✓ PASS
- All map types correctly identified
- All IANA registered parameters parsed
- Optional fields correctly marked
- Field types preserved with references

### Test Case: CoRIM with CBOR Tags

**CDDL:**
```cddl
tagged-uuid-type = #6.37(uuid-type)
tagged-oid-type = #6.111(oid-type)
$class-id-type-choice /= tagged-uuid-type
$class-id-type-choice /= tagged-oid-type
```

**Result:** ✓ PASS (with notes)
- CBOR tag notation parsed
- Underlying types extracted
- Type references maintained
- Tag validation not performed (acceptable for schema parsing)

## Usage with CoRIM Files

### Parsing CoRIM CDDL Schema

```bash
python cbor_cddl_analyzer.py corim.cddl corim_data.cbor --show-types
```

### Validating CoRIM CBOR Data

```bash
python cbor_cddl_analyzer.py corim.cddl corim_data.cbor --validate --type corim-map
```

### Generating EDN for CoRIM

```bash
python cbor_cddl_analyzer.py corim.cddl corim_data.cbor --type corim-map --output corim.edn
```

**EDN Output Example:**
```edn
{
  "corim-id": "550e8400-e29b-41d4-a716-446655440000",
  "tags": [
    {
      "comid-id": "example-comid",
      "entities": [
        {
          "entity-name": "Acme Corp",
          "reg-id": "https://acme.example",
          "roles": [1]
        }
      ]
    }
  ],
  "profile": "tag:rats@ietf.org,2025:profile#1"
}
```

Note how IANA registered parameter keys (0, 1, 2, etc.) are displayed as their semantic names ("corim-id", "tags", etc.).

## Recommendations for CoRIM Users

1. **Schema Validation:** Use this tool to validate CoRIM CDDL schemas are parseable
2. **EDN Generation:** Generate human-readable EDN for debugging CoRIM CBOR files
3. **Type Exploration:** Use `--show-types` to understand CoRIM structure
4. **Field Naming:** IANA parameters will automatically use semantic names in EDN

## Known Limitations

1. **No Type Choice Expansion:** Type choices (`$name /= value`) are not fully expanded
2. **No Constraint Validation:** Control operators (`.size`, `.within`) are not validated
3. **No Generic Instantiation:** Generic types are not instantiated with actual types
4. **Basic Validation Only:** Structural validation only, not semantic validation

## Future Enhancements

Priority enhancements for better CoRIM support:

1. **Type Choice Resolver:** Expand `$name /= value` definitions to show all alternatives
2. **Constraint Validator:** Validate `.size`, `.within`, and other control operators
3. **CBOR Tag Validator:** Validate CBOR tag numbers match IANA registry
4. **Generic Expander:** Properly instantiate generic types

## Conclusion

The CBOR-CDDL Analyzer provides strong support for CoRIM CDDL schemas, correctly handling the most important features:
- IANA registered parameters (core CoRIM feature)
- Optional fields
- Map and array structures  
- Nested definitions
- Comments

The tool is suitable for:
- CoRIM schema exploration
- CoRIM CBOR debugging
- EDN generation for human review
- Basic structural validation

For full semantic validation of CoRIM documents, use a complete CoRIM validator alongside this tool.
