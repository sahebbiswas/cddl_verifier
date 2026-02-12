# CBOR Tag Notation in EDN Output

## Feature

EDN output now uses standard CBOR tag notation `tag_number(content)` to show tagged data, making it immediately clear which CBOR tags wrap which content.

## Syntax

```edn
tag_number(
  content
)
```

Where:
- `tag_number` is the CBOR tag value (0-4294967295)
- `content` is the tagged data (can be any CBOR type)

## Examples

### Tagged Maps (CoRIM Structure)

```edn
501(
{  / corim /
  0: "test corim id",  / id /
  1: [
    506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
      0: "en-GB",  / language /
      1: {...},  / tag-identity /
      2: [...],  / entities /
      4: {...}  / triples /
    }
)
  ]  / tags /
}
)
```

This shows:
- Top-level data is wrapped in tag **501** (unsigned-corim-map)
- Inside tags array, each element is wrapped in tag **506** (concise-mid-tag/CoMID)

### Common CBOR Tags in CoRIM

**Tag 32: URI**
```edn
1: 32("https://acme.example")  / reg-id /
```

**Tag 37: UUID** (if present)
```edn
0: 37(h'550e8400e29b41d4a716446655440000')
```

**Tag 600: UEID (class-id)**
```edn
0: 600(h'61636d652d696d706c656d656e746174696f6e2d69642d303030303030303031')  / class-id /
```

**Tag 601: Measured Element**
```edn
0: 601(
{  / $measured-element-type-choice /
  1: "BL",
  4: "2.1.0",
  5: h'acbb11c7e4da217205523ce4ce1a245ae1a239ae3c6bfd9e7871f7e5d8bae86b'
}
)  / mkey /
```

### Nested Tagged Content

Tags can be nested:
```edn
501(  / unsigned-corim-map /
{  / corim /
  1: [
    506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
      4: {  / triples-map /
        0: [
          [
            {  / environment-map /
              0: {  / class-map /
                0: 600(h'...')  / class-id (UEID) /
                1: "ACME",  / vendor /
              }
            }
          ]
        ]
      }
    }
)
  ]
}
)
```

This shows:
- Outer tag **501** (unsigned-corim-map)
- Tag **506** (concise-mid-tag)
- Tag **600** (UEID for class-id)

## CBOR Tag Registry

Common tags you'll see in CoRIM/CoMID/CoSWID:

| Tag | Name | Usage | Example |
|-----|------|-------|---------|
| 32 | URI | Text strings that are URIs | `32("https://...")` |
| 37 | UUID | Binary UUID | `37(h'...')` |
| 501 | unsigned-corim-map | CoRIM without signature | `501({...})` |
| 502 | signed-corim | Signed CoRIM (COSE) | `502({...})` |
| 505 | concise-swid-tag | CoSWID tag | `505({...})` |
| 506 | concise-mid-tag | CoMID tag | `506({...})` |
| 600 | UEID | Unique Endpoint ID | `600(h'...')` |
| 601 | OID | Object Identifier | `601(...)` |

Full registry: https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml

## How It Works

### Detection

When the decoder encounters a CBOR tagged value, it returns a tuple:
```python
(tag_number, value)
```

### Generation

The EDN generator detects tagged tuples and wraps the content:
```python
if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
    tag_num = value[0]
    inner_value = value[1]
    
    # Generate EDN for inner value
    inner_edn = self._generate_value(inner_value, type_name, annotate)
    
    # Wrap in tag notation
    result_lines = [f"{tag_num}(  {tag_comment}"]  # With type annotation
    result_lines.extend(inner_edn.split('\n'))
    result_lines.append(")")
    return '\n'.join(result_lines)
```

### Special Case: Nested CBOR

For tags with `.cbor` control (nested CBOR in bytes), the tag wrapper shows the decoding:
```edn
506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
  ...decoded content...
}
)
```

This makes it clear:
1. The data is wrapped in tag **506**
2. The CDDL type is `tagged-concise-mid-tag`
3. Inside the tag is bytes containing CBOR
4. That CBOR decodes to `concise-mid-tag` structure

## Complete Example

```edn
501(
{  / corim /
  0: "test corim id",  / id /
  1: [
    506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
      2: [
        {  / entity-map /
          0: "ACME Ltd.",  / entity-name /
          1: 32("https://acme.example")  / reg-id /
          2: [0, 1, 2]  / role /
        }
      ],  / entities /
      4: {  / triples-map /
        0: [
          [  / reference-triple-record /
            {  / environment-map /
              0: {  / class-map /
                0: 600(h'61636d652d696d706c656d656e746174696f6e2d69642d303030303030303031')  / class-id /
                1: "ACME",  / vendor /
                2: "RoadRunner"  / model /
              }  / class /
            },
            [
              {  / measurement-map /
                0: 601(
{  / $measured-element-type-choice /
                  1: "BL",
                  4: "2.1.0",
                  5: h'acbb11c7e4da217205523ce4ce1a245ae1a239ae3c6bfd9e7871f7e5d8bae86b'
                }
)  / mkey /
              }
            ]
          ]
        ]  / reference-triples /
      }  / triples /
    }
)
  ]  / tags /
}
)
```

### Tag Annotations

**Tag 501**: unsigned-corim-map - Wraps the entire CoRIM structure
**Tag 506**: tagged-concise-mid-tag - Wraps CoMID data with nested CBOR
**Tag 32**: URI - Wraps the entity reg-id URL
**Tag 600**: UEID - Wraps the class-id (device identifier)
**Tag 601**: Measured element - Wraps measurement key data

## Benefits

### 1. **Immediate Tag Visibility**
Tags are shown inline with their content:
```edn
32("https://acme.example")  // Clearly a URI tag
```

Not hidden in annotations or separate metadata.

### 2. **Standard CBOR Notation**
Uses the same notation as CBOR diagnostic format (RFC 8949):
```edn
tag_number(content)
```

Familiar to anyone working with CBOR.

### 3. **Structure Clarity**
Opening `tag(` and closing `)` clearly delineate tagged content:
```edn
506(
{...}
)
```

Easy to see where tag begins and ends.

### 4. **Tag Nesting Visible**
Multiple levels of tags are clearly shown:
```edn
501(
  {...
    506(
      {...
        600(h'...')
      }
    )
  }
)
```

### 5. **Type + Tag Information**
Combines tag number with CDDL type name:
```edn
506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
  ...
}
)
```

Shows both the CBOR tag (506) and the CDDL types.

## Comparison

### Without Tag Notation (Old)
```edn
{  tagged-concise-mid-tag -> concise-mid-tag /
  0: "en-GB",
  1: {...},
  2: [
    {
      0: "ACME Ltd.",
      1: "https://acme.example",  // Is this a URI tag? Not clear.
    }
  ]
}
```

Tag information hidden in comments, not clear which values are tagged.

### With Tag Notation (New)
```edn
506(  / tagged-concise-mid-tag /
{  / concise-mid-tag /
  0: "en-GB",
  1: {...},
  2: [
    {
      0: "ACME Ltd.",
      1: 32("https://acme.example"),  // Clearly tagged as URI!
    }
  ]
}
)
```

Tag numbers explicit, structure clear, standard notation.

## Usage

Tag notation is automatically included in EDN output:

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format keyindex
```

To see tag information with verbose logging:
```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format keyindex --verbose 2>&1 | grep "Tag"
```

**Example output:**
```
[DEBUG] Tag 506 wrapping: dict
[DEBUG] Tag 32 wrapping: str
[DEBUG] Tag 600 wrapping: bytes
```

## Validation

Tag notation helps validate that:
- Correct tags are used (e.g., 506 for CoMID, not 505 for CoSWID)
- Tags wrap the right content type (e.g., tag 32 wraps strings)
- Tag nesting is correct
- No missing or extra tags

## Summary

CBOR tag notation provides:
- [OK] Standard `tag_number(content)` syntax
- [OK] Inline tag visibility
- [OK] Clear tag boundaries with `(` and `)`
- [OK] Combined tag + type annotations
- [OK] Nested tag support
- [OK] Common tags (32, 37, 501, 506, 600, 601)
- [OK] RFC 8949 CBOR diagnostic format compliance

Every CBOR tag in your data is now explicitly shown, making the structure completely transparent!
