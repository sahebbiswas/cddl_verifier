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

### ✓ CBOR Tags
CoRIM uses CBOR tags with `#6.xxx(type)` notation.

**Example:**
```cddl
$concise-tag-type-choice /= #6.505(comid-map)
$concise-tag-type-choice /= #6.506(coswid-map)
tagged-uuid-type = #6.37(uuid-type)
```

**Support:** ✓ FULL
- The tag number is required and checked: a tagged rule rejects untagged data and
  other tag numbers, and an untagged rule rejects tagged data
- The tagged content is checked against the rule inside the tag
- The content of the registered tags the library knows (dates, URIs, bignums, MIME, ...)
  is checked too ([CDDL_SUPPORT.md](CDDL_SUPPORT.md#cbor-tags))

### ✓ Type Choices ($name /= value)
CoRIM uses type choice extensions heavily.

**Example:**
```cddl
$corim-id-type-choice /= tstr
$corim-id-type-choice /= uuid-type

$corim-role-type-choice /= & ( manifest-creator : 1 )
$corim-role-type-choice /= & ( manifest-signer : 2 )
```

**Support:** ✓ FULL
- `$name /= type` adds an alternative; data must match one of them
- `$$name //= group` sockets add group alternatives in a map or array
- A socket nothing extends matches nothing (as RFC 8610 says)
- A choice from a group (`&( name : n )`) is the choice of its values

### ✓ Generics
CoRIM defines generic types like `non-empty<M>`. The validator enforces
the `.and` operand, so an empty `triples-map` or `environment-map` is rejected.

**Example:**
```cddl
non-empty<M> = (M) .and ({ + any => any })

validity-map = non-empty<{
  ? & ( not-before : 0 ) => time,
  ? & ( not-after : 1 ) => time,
}>
```

**Support:** ✓ FULL
- Generic rules are instantiated where they are used, recursively too
- `.and` / `.within` / `.eq` / `.ne` are enforced on any target, including a generic
  structure (`non-empty<M>` rejects an empty map)
- A control that has no meaning for a structure (`.size`, `.regexp`, ...) is a schema error

### ✓ CDDL Control Operators
CoRIM uses operators like `.size`, `.within`, `.cbor`, etc.

**Example:**
```cddl
uuid-type = bstr .size 16
eui-48 = bstr .size 6
tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
```

**Support:** ✓ FULL
- `.size`, `.cbor`, `.cborseq`, `.regexp`, `.bits`, `.ge`, `.gt`, `.le`, `.lt`, `.eq`, `.ne`,
  `.and` and `.within` are checked (`.default` only requires the base type)
- The controls of RFC 9165 and RFC 9741 (`.plus`, `.cat`, `.det`, `.abnf`, the conversions,
  `.printf`, `.json`, `.join`) are checked too
- A control that cannot apply to its type is a schema error
- [CONFORMANCE.md](CONFORMANCE.md) lists every control with its status

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
- Tag numbers are validated: a tagged rule requires its tag, and an untagged
  rule rejects tagged data

## Usage with CoRIM Files

### Parsing CoRIM CDDL Schema

```bash
cddl-verify corim.cddl corim_data.cbor --show-types
```

### Validating CoRIM CBOR Data

```bash
cddl-verify corim.cddl corim_data.cbor --type corim-map
```

`--type` must match how the data is tagged. A CoRIM wrapped in tag 501 (as in
`test-data/minimal-corim.cbor`) validates against `tagged-unsigned-corim-map`
or `corim`, not against the untagged `corim-map`, which rejects it with
"expects an untagged map, but the data has CBOR tag 501".

### Generating EDN for CoRIM

```bash
cddl-verify corim.cddl corim_data.cbor --type corim-map --output corim.edn
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

1. **Structure, not meaning:** The tool checks that data has the shape the schema describes.
   Whatever the CoRIM specification says in prose (for example, that a profile's
   extensions are consistent) is not checked.
2. **Imported modules:** A CoRIM schema that imports CoSWID needs that file given
   (`-M` / `--module`); a name from a module that is not loaded matches any value and a
   warning says so ([CLI.md](CLI.md#imported-modules)).
3. **`.regexp` limits:** Patterns are read as XSD regular expressions and matched in time
   linear in the text. A pattern over 50 000 NFA states is a schema error, and a match
   over 10 million steps is refused ([CDDL_SUPPORT.md](CDDL_SUPPORT.md)).
4. **Tag 36 content:** The content of tag 36 (`mime-message`) is checked only as text
   ([#187](https://github.com/sahebbiswas/cddl_verifier/issues/187)).

For full semantic validation of CoRIM documents, use a complete CoRIM validator alongside
this tool.
