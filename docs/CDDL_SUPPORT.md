# CDDL support

`cddl-verify` and `CDDLParser` implement the subset of CDDL (RFC 8610) used by
practical attestation schemas such as CoRIM and CoSWID. This page lists what is
supported and how validation behaves.

## Schema format

The tool supports the IANA registered-parameter syntax used in CoRIM and CoSWID:

```cddl
person = {
  &( name  : 0 ) => tstr,               ; required text field
  &( age   : 1 ) => uint,               ; required unsigned integer
  ? &( email : 2 ) => tstr,             ; optional text field
  &( uuid  : 3 ) => bstr .size 16,      ; exactly 16 bytes
  &( label : 4 ) => tstr .size (1..64), ; 1–64 UTF-8 bytes
}
```

## Supported primitive types

| CDDL type | Python type | Notes |
|-----------|-------------|-------|
| `tstr` / `text` | `str` | Optional `.size` constraint (UTF-8 bytes; see below) |
| `bstr` / `bytes` | `bytes` | Optional `.size` constraint (bytes) |
| `uint` | `int >= 0` | `bool` rejected (distinct CBOR major type); optional `.size` constraint (see below) |
| `int` | `int` | `bool` rejected |
| `bool` | `bool` | `int` rejected |
| `float` / `float16` / `float32` / `float64` | `float` | `int` rejected |
| `null` / `nil` | `None` | |
| `any` | anything | No type check performed |

## Array types

```cddl
tags    = [ * tstr ]   ; zero or more text strings
aliases = [ + tstr ]   ; one or more (empty array fails validation)
```

## CBOR tags

```cddl
tagged-corim = #6.501(unsigned-corim-map)
```

## Nested CBOR (`.cbor` control operator)

When a field is typed as `bytes .cbor inner-type`, the EDN generator automatically
decodes the nested CBOR and renders it inline, wrapped in `bytes<N>(...)` where `N` is
the byte length of the encoded inner value:

```cddl
payload = #6.506(bytes .cbor inner-type)
inner-type = {
  &( value : 0 ) => uint,
}
```

```edn
/ outer-type / 506(
  bytes<4>(
    / inner-type / {
      / value / 0: 42
    }
  )
)
```

The nested byte string must hold exactly one CBOR item. Truncated content or
trailing bytes inside it are reported as a validation error, using the same strict
decoder described in [API.md](API.md#strict-single-item-decoding).

## Type choices

```cddl
$kind /= option-a
$kind /= option-b
```

When the root type resolves to a type choice the tool attempts to auto-select the matching
alternative based on the data structure.

## IANA registered parameters and CoRIM

The analyzer is tested against real CoRIM and CoSWID CDDL schemas.  See
[CORIM_SUPPORT.md](CORIM_SUPPORT.md) for the full list of supported features and
[IANA_ANNOTATIONS_STATUS.md](IANA_ANNOTATIONS_STATUS.md) for annotation behaviour at
every nesting level.

## Validation behaviour

| Condition | Result |
|-----------|--------|
| Required field missing | ❌ fail |
| Optional field absent | ✅ pass |
| Optional field present with wrong type | ❌ fail |
| Field value wrong primitive type | ❌ fail |
| `.size` constraint violated | ❌ fail |
| Unknown key not in schema | ❌ fail |
| `[ + type ]` with empty array | ❌ fail |
| Array element wrong type | ❌ fail |

## `.size`

`.size` follows RFC 8610 §3.8.1. It is checked on map fields, array elements
(including inline arrays such as `[* tstr .size 8]`), aliases
(`label = tstr .size (1..64)`) and a root rule (`id = tstr .size 2`).

| Target | What is measured | Example |
|--------|------------------|---------|
| `tstr` | Length of the UTF-8 encoding in bytes | `"ü"` has size 2; `tstr .size 2` accepts it |
| `bstr` | Length in bytes | `bstr .size 16` accepts exactly 16 bytes |
| `uint` | Number of bytes needed to represent the value | `uint .size 3` accepts `0..16777215` (`value < 256**3`) |

The argument can be:

- an integer: `.size 16`, `.size 0x10`
- a range: `.size (1..64)` (inclusive) or `.size (1...64)` (upper bound
  exclusive)
- a constant defined elsewhere: `.size max-len` with `max-len = 64` or
  `max-len = 1..64`

For `uint`, a value satisfies `.size S` when it fits in some allowed number of
bytes in `S`, so only the upper bound of a range matters:
`uint .size (1..2)` accepts `0..65535`.

These are reported as validation errors instead of being skipped silently:

- an argument that is not a non-negative integer, range or such a constant
  (`.size foo`, `.size -1`)
- an empty range (`.size (3..1)`)
- `.size` on a type other than `tstr`, `bstr` or `uint` (`int .size 1`)
