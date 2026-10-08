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
  &( label : 4 ) => tstr .size (1..64), ; 1–64 characters
}
```

## Supported primitive types

| CDDL type | Python type | Notes |
|-----------|-------------|-------|
| `tstr` / `text` | `str` | Optional `.size` constraint (counts characters, not UTF-8 bytes; see below) |
| `bstr` / `bytes` | `bytes` | Optional `.size` constraint |
| `uint` | `int >= 0` | `bool` rejected (distinct CBOR major type); `.size` is not enforced |
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

## Known gaps in `.size`

`.size` does not yet follow RFC 8610 §3.8.1 in every case
([#67](https://github.com/sahebbiswas/cddl_verifier/issues/67)):

- On `tstr` it counts Unicode characters instead of UTF-8 bytes, so non-ASCII
  text can pass or fail incorrectly.
- On `uint` it is not enforced.
- A top-level rule that is only a constrained primitive (`id = tstr .size 2`)
  cannot be used as the root type; use it as a field type instead.
