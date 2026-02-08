# CDDL Groups and Type Choices Support

This document describes the support for CDDL groups and type choices in the CBOR-CDDL Analyzer.

## Type Choices

### Overview
Type choices allow defining extensible type alternatives using the `/=` operator. This is commonly used in CDDL to create polymorphic types where a field can accept multiple different types.

### Syntax

```cddl
$choice-name /= alternative1
$choice-name /= alternative2
$choice-name /= alternative3
```

### Examples

#### Basic Type Choice
```cddl
$id-type-choice /= tstr
$id-type-choice /= int
$id-type-choice /= uuid-type

document = {
  & ( doc-id : 0 ) => $id-type-choice,
}
```

**Interpretation:** The `doc-id` field can be a text string, integer, or UUID type.

#### IANA Parameter Choices
```cddl
$role-type-choice /= & ( admin : 0 )
$role-type-choice /= & ( user : 1 )
$role-type-choice /= & ( guest : 2 )

permissions = {
  & ( role : 1 ) => $role-type-choice,
}
```

**Interpretation:** The `role` field can be one of three registered parameter values: admin(0), user(1), or guest(2).

#### String Literal Choices
```cddl
$status-choice /= "active"
$status-choice /= "inactive"
$status-choice /= "pending"

record = {
  & ( status : 2 ) => $status-choice,
}
```

**Interpretation:** The `status` field must be one of three specific string values.

### Parser Support

The analyzer now:
- ✅ Parses all `/=` type choice definitions
- ✅ Collects all alternatives for each choice name
- ✅ Displays choices in `--show-types` output
- ✅ Preserves choice references in field type definitions

### Usage

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

**Output:**
```
Type Choices:
==================================================

$id-type-choice:
  /= tstr
  /= int
  /= uuid-type

$role-type-choice:
  /= & ( admin : 0 )
  /= & ( user : 1 )
  /= & ( guest : 2 )
```

## Groups

### Overview
Groups are reusable collections of field definitions that can be included in multiple type definitions. They promote DRY (Don't Repeat Yourself) principles in CDDL schemas.

### Syntax

**Single-line group:**
```cddl
group-name = ( field1: type1, field2: type2 )
```

**Multi-line group:**
```cddl
group-name = (
  field1: type1,
  field2: type2,
  field3: type3,
)
```

**Group with IANA parameters:**
```cddl
group-name = (
  & ( key1 : 0 ) => type1,
  ? & ( key2 : 1 ) => type2,
)
```

### Examples

#### Simple Group
```cddl
coordinates = ( latitude: float, longitude: float )

location-map = {
  coordinates,
  & ( label : 2 ) => tstr,
}
```

**Effect:** The `location-map` includes the `latitude` and `longitude` fields from the `coordinates` group.

#### Audit Information Group
```cddl
audit-info = (
  created-by: tstr,
  created-at: uint,
  ? modified-by: tstr,
  ? modified-at: uint,
)

user-record = {
  & ( user-id : 0 ) => tstr,
  audit-info,
}
```

**Effect:** The `user-record` includes all audit fields (created-by, created-at, modified-by, modified-at).

#### Multiple Groups
```cddl
entity-info = (
  & ( name : 0 ) => tstr,
  & ( email : 1 ) => tstr,
)

audit-info = (
  created-by: tstr,
  created-at: uint,
)

full-record = {
  entity-info,
  audit-info,
  & ( status : 4 ) => tstr,
}
```

**Effect:** The `full-record` includes fields from both `entity-info` and `audit-info` groups.

### Parser Support

The analyzer now:
- ✅ Parses single-line group definitions
- ✅ Parses multi-line group definitions
- ✅ Captures group content (fields and IANA parameters)
- ✅ Displays groups in `--show-types` output
- ⚠️ Does NOT expand groups into parent types (shows reference only)

### Usage

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor --show-types
```

**Output:**
```
CDDL Groups:
==================================================

coordinates:
  latitude: float, longitude: float

audit-info:
  created-by: tstr,
  created-at: uint,
  ? modified-by: tstr,
  ? modified-at: uint,

entity-info:
  & ( name : 0 ) => tstr,
  ? & ( email : 1 ) => tstr,
  & ( created : 2 ) => uint,
```

## Combined Example: Groups + Type Choices

```cddl
; Define reusable groups
resource-group = (
  & ( resource-id : 0 ) => $id-type-choice,
  & ( resource-type : 1 ) => tstr,
)

audit-info = (
  created-by: tstr,
  created-at: uint,
)

; Define type choices
$id-type-choice /= tstr
$id-type-choice /= int
$id-type-choice /= uuid-type

$role-type-choice /= & ( admin : 0 )
$role-type-choice /= & ( user : 1 )

; Use groups and choices together
access-record = {
  resource-group,
  & ( user-role : 2 ) => $role-type-choice,
  audit-info,
}
```

This creates an `access-record` type that:
1. Includes `resource-id` and `resource-type` fields (from `resource-group`)
2. Has a `user-role` field that can be admin(0) or user(1)
3. Includes `created-by` and `created-at` fields (from `audit-info`)

## CoRIM Usage

CoRIM extensively uses both groups and type choices:

### CoRIM Type Choices
```cddl
$corim-id-type-choice /= tstr
$corim-id-type-choice /= uuid-type

$corim-role-type-choice /= & ( manifest-creator : 1 )
$corim-role-type-choice /= & ( manifest-signer : 2 )

$concise-tag-type-choice /= #6.505(comid-map)
$concise-tag-type-choice /= #6.506(coswid-map)
```

### CoRIM Groups (if present)
Groups in CoRIM help avoid repeating common field collections across different triple types.

## Validation Behavior

### Type Choice Validation
When validating CBOR data:
- The parser recognizes that a field has a choice type
- ⚠️ Currently does NOT validate that the value matches one of the alternatives
- Future enhancement: validate value against all choice alternatives

### Group Expansion
When validating CBOR data:
- ⚠️ Groups are currently shown but NOT expanded into parent types
- This means fields from groups may not be validated in parent types
- Future enhancement: expand groups inline during type resolution

## Future Enhancements

### Priority 1: Group Expansion
Expand group references into parent type definitions:
```cddl
coords = ( lat: float, lon: float )
location = { coords, label: tstr }

# Should expand to:
location = { lat: float, lon: float, label: tstr }
```

### Priority 2: Type Choice Validation
Validate that field values match at least one alternative:
```cddl
$id /= tstr
$id /= int

record = { id: $id }

# Should validate that CBOR id field is either tstr OR int
```

### Priority 3: Nested Choice Resolution
Handle nested type choices:
```cddl
$inner /= tstr
$inner /= int

$outer /= $inner
$outer /= bstr

# Should recognize $outer can be: tstr, int, or bstr
```

## Testing

### Test File
Use `test_groups_choices.cddl` for comprehensive testing of both features.

### Run Tests
```bash
# Show all parsed groups and type choices
python cbor_cddl_analyzer.py test_groups_choices.cddl data.cbor --show-types

# Validate with type choices and groups
python cbor_cddl_analyzer.py test_groups_choices.cddl data.cbor --validate --type document
```

## Summary

The CBOR-CDDL Analyzer now provides:

✅ **Full Type Choice Parsing**
- Recognizes all `/=` alternatives
- Displays all choices per type
- Preserves choice references in fields

✅ **Basic Group Parsing**
- Captures group definitions
- Handles single and multi-line groups
- Displays group content

⚠️ **Limitations**
- Groups not expanded into parent types
- Type choices not validated against alternatives
- Nested choices not fully resolved

These features provide good support for understanding CoRIM and other complex CDDL schemas, with room for enhancement in validation capabilities.
