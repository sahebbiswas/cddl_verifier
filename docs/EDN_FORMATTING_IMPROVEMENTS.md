# EDN Formatting Improvements

## Overview

Two major formatting improvements make the EDN output even more readable:

1. **Annotations on the left** - Field names appear before keys for better readability
2. **bytes(...) wrapper** - Nested CBOR content is explicitly wrapped to show encoding

## 1. Left-Aligned Annotations

All annotations now appear on the left for maximum consistency and scannability.

### Field Annotations

**Format:** `/ field-name / key: value`

```edn
/ id / 0: "test corim id",
/ tags / 1: [...],
/ vendor / 1: "ACME",
/ model / 2: "RoadRunner"
```

### Tag Annotations

**Format:** `/ tag-type / tag_number(...)`

```edn
/ corim / 501(
  ...
)

/ tagged-concise-mid-tag / 506(
  ...
)

/ uri / 32("https://acme.example")
```

### Map Type Annotations

**Format:** `/ map-type / {`

```edn
/ corim / {
  ...
}

/ entity-map / {
  ...
}

/ class-map / {
  ...
}
```

### Array Type Annotations

**Format:** `/ array-type / [`

```edn
/ reference-triple-record / [
  ...
]
```

### Benefits

**Complete consistency**: Every annotation uses the same left-aligned format
```edn
/ corim / 501(
/ corim / {
  / id / 0: "test",
  / tags / 1: [
    / tagged-concise-mid-tag / 506(
      / entity-map / {
        / entity-name / 0: "ACME"
      }
    )
  ]
}
)
```

**Easy scanning**: Read down the left side to see all types, tags, and field names

**No visual clutter**: Clean, consistent formatting throughout

## 2. bytes(...) Wrapper for Nested CBOR

### Concept

When CBOR data contains bytes that themselves encode CBOR (nested CBOR), this is shown with `bytes(...)` wrapper.

### Example Structure

```edn
506(  / tagged-concise-mid-tag /
bytes(
{  / concise-mid-tag /
  / entities / 2: [...],
  / triples / 4: {...}
}
)
)
```

This shows:
1. Tag **506** wraps the data
2. The data is **bytes** (not directly a map)
3. Those bytes contain **CBOR-encoded** concise-mid-tag
4. The decoded CBOR is the `{...}` map

### Why It Matters

**Shows encoding layers**: Makes it clear when data is double-encoded
```edn
506(           <- CBOR tag 506
bytes(         <- Byte string containing CBOR
{...}          <- Decoded CBOR content
)
)
```

**Matches CDDL notation**: Corresponds to `.cbor` control operator
```cddl
tagged-concise-mid-tag = #6.506(bytes .cbor concise-mid-tag)
```

Becomes:
```edn
506(
bytes(
{  / concise-mid-tag /
  ...
}
)
)
```

**Debugging aid**: If bytes are malformed, you know exactly what layer failed
- Tag decoding works?
- Bytes extracted?
- Nested CBOR decoding fails? <- `bytes(...)` shows this layer

## Complete Example

```edn
/ corim / 501(
/ corim / {
  / id / 0: "test corim id",
  / tags / 1: [
    / tagged-concise-mid-tag / 506(
bytes(
/ concise-mid-tag / {
      / entities / 2: [
        / entity-map / {
          / entity-name / 0: "ACME Ltd.",
          / reg-id / 1: / uri / 32("https://acme.example"),
          / role / 2: [0, 1, 2]
        }
      ],
      / triples / 4: / triples-map / {
        / reference-triples / 0: [
          / reference-triple-record / [
            / environment-map / {
              / class / 0: / class-map / {
                / class-id / 0: / $class-id-type-choice / 600(h'61636d652d696d706c656d656e746174696f6e2d69642d303030303030303031'),
                / vendor / 1: "ACME",
                / model / 2: "RoadRunner"
              }
            },
            [
              / measurement-map / {
                / mkey / 0: / $measured-element-type-choice / 601(
/ $measured-element-type-choice / {
                  1: "BL",
                  4: "2.1.0",
                  5: h'acbb11c7e4da217205523ce4ce1a245ae1a239ae3c6bfd9e7871f7e5d8bae86b'
                }
),
                / mval / 1: / measurement-values-map / {
                  / digests / 2: / digests-type / [
                    [
                      1,
                      h'87428fc522803d31065e7bce3cf03fe475096631e5e07bbd7a0fde60c4cf25c7'
                    ]
                  ]
                }
              }
            ]
          ]
        ]
      },
      / language / 0: "en-GB",
      / tag-identity / 1: / tag-identity-map / {
        / tag-id / 0: h'43bbe37f2e614b33aed353cff1428b16'
      }
    }
)
)
  ]
}
)
```

## Structure Breakdown

### Outer layers
```edn
501(              <- Tag 501: unsigned-corim-map
{  / corim /      <- Corim map
  / tags / 1: [   <- Tags array
```

### Tag element with nested CBOR
```edn
    / tagged-concise-mid-tag / 506(  <- Tag 506 annotation + tag number
bytes(            <- Bytes containing CBOR
{  / concise-mid-tag /  <- Decoded CBOR content
  ...
}
)                 <- End of bytes
)                 <- End of tag 506
```

### Nested tags without bytes wrapper
```edn
/ reg-id / 1: 32("https://acme.example")
```
No `bytes()` here because tag 32 directly wraps a string, not nested CBOR.

```edn
/ class-id / 0: 600(h'...')
```
No `bytes()` here because tag 600 directly wraps bytes, not nested CBOR.

```edn
/ mkey / 0: 601(
{...}
)
```
No `bytes()` because tag 601 directly wraps a map, not nested CBOR.

**Only use bytes() when**: The tag wraps bytes that contain CBOR (`.cbor` control operator)

## Field Annotation Details

### All fields annotated on left
```edn
{  / class-map /
  / class-id / 0: 600(h'...'),
  / vendor / 1: "ACME",
  / model / 2: "RoadRunner"
}
```

### Arrays
```edn
/ entities / 2: [
  {  / entity-map /
    / entity-name / 0: "ACME Ltd.",
    / reg-id / 1: 32("https://acme.example"),
    / role / 2: [0, 1, 2]
  }
]
```

### Nested structures
```edn
/ triples / 4: {  / triples-map /
  / reference-triples / 0: [
    [  / reference-triple-record /
      ...
    ]
  ]
}
```

Every field has its name on the left, making it easy to scan and find fields.

## Comparison: Old vs New

### Old Format
```edn
{
  0: "test corim id",  / id /
  1: [
    506(  / tagged-concise-mid-tag /
{...}
)
  ]  / tags /
}
```

Issues:
- Names at end of line (hard to scan)
- Tag type after tag number
- No indication that tag 506 contains nested CBOR

### New Format
```edn
501(
{  / corim /
  / id / 0: "test corim id",
  / tags / 1: [
    / tagged-concise-mid-tag / 506(
bytes(
{  / concise-mid-tag /
  ...
}
)
)
  ]
}
)
```

Benefits:
- Names at start of line (easy to scan)
- Tag types before tag numbers
- `bytes(...)` shows nested CBOR encoding
- Consistent left-aligned annotation style

## Usage

These improvements are automatic in the default `keyindex` format:

```bash
python cbor_cddl_analyzer.py schema.cddl data.cbor \
  --type corim --edn-format keyindex
```

The `keyname` and `both` formats also benefit from the `bytes()` wrapper.

## Summary

Formatting improvements provide:
- [OK] Left-aligned field annotations (`/ name / 0:` format)
- [OK] Left-aligned tag annotations (`/ type / tag_num(` format)
- [OK] Easy scanning down the left side
- [OK] Consistent annotation style for all elements
- [OK] `bytes(...)` wrapper for nested CBOR
- [OK] Clear encoding layer visualization
- [OK] Matches CDDL `.cbor` control operator
- [OK] Tag numbers + bytes + decoded content all visible
- [OK] Self-documenting structure

The EDN output is now maximally readable with all encoding layers, tags, types, and field names clearly visible and consistently formatted!
