# Type Name Annotations in EDN Output

## Feature

EDN output now includes CDDL type names as inline comments on opening braces/brackets, making it easy to understand the structure at every level.

## Examples

### Maps with Type Headers

```edn
{  / corim /
  0: "test corim id",  / id /
  1: [...],  / tags /
  ...
}
```

```edn
{  / entity-map /
  0: "ACME Ltd.",  / entity-name /
  1: "https://acme.example",  / reg-id /
  2: [0, 1, 2]  / role /
}
```

```edn
{  / class-map /
  0: h'61636d652d696d706c656d656e746174696f6e2d69642d303030303030303031',  / class-id /
  1: "ACME",  / vendor /
  2: "RoadRunner"  / model /
}
```

### Arrays with Type Headers

```edn
[  / reference-triple-record /
  {  / environment-map /
    0: {  / class-map /
      ...
    }
  },
  [
    {  / measurement-map /
      ...
    }
  ]
]
```

### Tagged Types with Decoding Chain

For CBOR tagged data that contains nested CBOR, the annotation shows the complete decoding chain:

```edn
{  tagged-concise-mid-tag -> concise-mid-tag /
  0: "en-GB",  / language /
  1: {...},  / tag-identity /
  2: [...],  / entities /
  4: {...}  / triples /
}
```

This shows:
- Original CDDL type: `tagged-concise-mid-tag` (tag 506)
- Decoded inner type: `concise-mid-tag` (the actual CoMID structure)

## How It Works

### Map Type Headers

When generating a map, if a type name is provided and annotations are enabled:
```python
type_header = ""
if type_name and annotate and type_name not in ['map', 'dict']:
    type_header = f"  / {type_name} /"

lines = ["{" + type_header]
```

**Example:**
```edn
{  / class-map /
  0: h'...',  / class-id /
  1: "ACME"  / vendor /
}
```

### Array Type Headers

For arrays, type headers are shown for named array types (not inline syntax):
```python
type_header = ""
if type_name and annotate and not type_name.startswith('['):
    # It's a named array type (not inline array syntax)
    type_header = f"  / {type_name} /"

lines = ["[" + type_header]
```

**Example:**
```edn
[  / reference-triple-record /
  {  / environment-map /
    ...
  },
  [...]
]
```

**Not shown for inline syntax:**
```edn
[  // No type header for [ + comid-entity-map ]
  {  / entity-map /  // But element type is shown
    ...
  }
]
```

### Tagged Type Chain

For nested CBOR within tagged data:
```python
if annotate and type_name != inner_type:
    # Show both the tagged type and the inner type
    nested_edn = self._generate_value(nested_data, inner_type, annotate)
    first_line, rest = (nested_edn.split('\n', 1) + [''])[:2]
    if type_name and '{' in first_line:
        # Add tagged type comment after the inner type
        modified_first = first_line.replace(
            f'/ {inner_type} /', 
            f'{type_name} -> {inner_type} /'
        )
        return modified_first + ('\n' + rest if rest else '')
```

**Example:**
```edn
{  tagged-concise-mid-tag -> concise-mid-tag /
  // Shows: tag 506 contains concise-mid-tag structure
  ...
}
```

## Complete Structure Example

```edn
{  / corim /
  0: "test corim id",  / id /
  1: [
    {  tagged-concise-mid-tag -> concise-mid-tag /
      2: [
        {  / entity-map /
          0: "ACME Ltd.",  / entity-name /
          1: "https://acme.example",  / reg-id /
          2: [0, 1, 2]  / role /
        }
      ],  / entities /
      4: {  / triples-map /
        0: [
          [  / reference-triple-record /
            {  / environment-map /
              0: {  / class-map /
                0: h'...',  / class-id /
                1: "ACME",  / vendor /
                2: "RoadRunner"  / model /
              }  / class /
            },
            [
              {  / measurement-map /
                0: {  / $measured-element-type-choice /
                  1: "BL",
                  4: "2.1.0",
                  5: h'...'
                },  / mkey /
                1: {  / measurement-values-map /
                  2: [  / digests-type /
                    [1, h'...']
                  ]  / digests /
                }  / mval /
              }
            ]
          ]  / reference-triples /
        ]
      }  / triples /
    }
  ]  / tags /
}
```

## Benefits

### 1. Structure Clarity
Each opening brace/bracket immediately shows what type of data follows:
- `{  / class-map /` - This is a class-map structure
- `[  / reference-triple-record /` - This array contains reference-triple-record elements

### 2. Navigation
Easy to find specific structures by searching for type names:
```bash
# Find all measurement maps
grep "measurement-map" output.edn

# Find class definitions
grep "class-map" output.edn
```

### 3. Debugging
When validating fails, type headers show exactly which CDDL type each structure should conform to.

### 4. Learning
Helps understand the relationship between CBOR data and CDDL schema:
- See which CDDL types map to which data structures
- Understand tagged type unwrapping (`tagged-X -> X`)
- Follow type resolution through aliases

### 5. Documentation
EDN output serves as self-documenting format:
- Type names explain the purpose of each structure
- Field annotations explain each field
- Together they provide complete documentation

## Type Name Sources

Type names come from:
1. **Top-level type** - Specified with `--type` parameter
2. **Field types** - From CDDL field definitions
3. **Array element types** - From inline `[ + type ]` or indexed element_types
4. **Tagged types** - From CBOR tag resolution
5. **Nested types** - From `.cbor` control operator

## Usage

Type headers are automatically included when using any EDN format:

```bash
# With field indices (default)
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format keyindex

# With field names
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format keyname

# With both
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format both
```

To disable annotations (including type headers):
```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --no-annotate
```

## Comparison

### Without Type Headers (Old)
```edn
{
  0: "test corim id",
  1: [
    {
      2: [
        {
          0: "ACME Ltd.",
          1: "https://acme.example",
          2: [0, 1, 2]
        }
      ],
      4: {
        0: [
          [
            {
              0: {
                0: h'...',
                1: "ACME",
                2: "RoadRunner"
              }
            }
          ]
        ]
      }
    }
  ]
}
```

Hard to understand what each structure represents!

### With Type Headers (New)
```edn
{  / corim /
  0: "test corim id",  / id /
  1: [
    {  tagged-concise-mid-tag -> concise-mid-tag /
      2: [
        {  / entity-map /
          0: "ACME Ltd.",  / entity-name /
          1: "https://acme.example",  / reg-id /
          2: [0, 1, 2]  / role /
        }
      ],  / entities /
      4: {  / triples-map /
        0: [
          [  / reference-triple-record /
            {  / environment-map /
              0: {  / class-map /
                0: h'...',  / class-id /
                1: "ACME",  / vendor /
                2: "RoadRunner"  / model /
              }  / class /
            }
          ]  / reference-triples /
        ]
      }  / triples /
    }
  ]  / tags /
}
```

Crystal clear what each structure represents!

## Summary

Type name annotations provide:
- [OK] Type headers on maps (`{  / type-name /`)
- [OK] Type headers on arrays (`[  / type-name /`)
- [OK] Tagged type chains (`tagged-X -> X /`)
- [OK] IANA field annotations (`/ field-name /`)
- [OK] Complete documentation in EDN output
- [OK] Self-describing data structures

Every level of nesting now clearly shows its CDDL type, making the EDN output fully self-documenting!
