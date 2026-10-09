# CDDL support

`cddl-verify` and `CDDLParser` parse the whole CDDL grammar (RFC 8610, as
updated by RFC 9682) and validate the subset used by practical attestation
schemas such as CoRIM and CoSWID. This page lists what is supported and how
validation behaves.

## Schema syntax

A schema that is not valid CDDL is rejected with its position:

```
schema.cddl:12:7: expected a type, found '}'
```

`Validator` and `validate()` raise `SchemaError`, and `cddl-verify` exits with
status 1. Forms that earlier versions accepted but are not CDDL are rejected
too, for example `&(a: 0) => uint ?` (write `? &(a: 0) => uint`) and an
occurrence with no type (`any = *`).

Names may contain dots and hyphens, as RFC 8610 allows (`coswid.tag-id`). So
`lo..hi` is one name: write `lo .. hi` for a range between two names. A
`.size` argument that is such an undefined name says so in its error.

`.regexp` patterns are CDDL text strings, so their escapes are decoded first,
as in RFC 8610's examples: `"a\\.b"` is the regular expression `a\.b`.

One non-standard form is accepted: chained controls such as
`uint .ge 0 .le 150`, read as `(uint .ge 0) .le 150`. RFC 8610 allows one
control per type, but this form is common and its bounds are enforced. The
standard spellings (`(uint .ge 0) .le 150`, `0..150`) parse but are not yet
enforced ([#71](https://github.com/sahebbiswas/cddl_verifier/issues/71),
[#76](https://github.com/sahebbiswas/cddl_verifier/issues/76)).

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

Tag numbers are checked wherever a rule is used: at the root, in map fields, in
array elements and in `$socket` choices.

- A tagged rule requires its tag: `tagged-corim` rejects untagged data and
  data with any other tag. Nested tags (`#6.1(#6.2(m))`) must appear in order.
- An untagged rule rejects tagged data: `unsigned-corim-map` rejects
  `501({...})` with "expects an untagged map, but the data has CBOR tag 501".
  Validate tagged data against the tagged rule.
- Tags around primitives (`#6.1(tstr .size 2)`) are checked, and then the
  value inside is checked against the primitive and its controls.

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
$kind /= option-a                  ; socket choice
$kind /= option-b
label = int / tstr                  ; inline choice
version = 1 / 2 / "draft"           ; choice of literal values
digest = bstr .size 32 / tagged-digest
```

A value is valid when it matches any alternative. Inline choices are checked in
map fields, array elements, inline arrays and at the root. Alternatives can be
primitives with controls (`.size`, `.regexp`, `.ge`/`.gt`/`.le`/`.lt`), named
rules, tagged rules and literals (integers, floats, quoted text, byte strings
such as `h'0102'`, `true`, `false`), including literals with controls
(`"t" .size 1`). When nothing matches, the error lists each alternative and why it
failed, for example:

```text
Field 'x' in 'r' matches none of uint / tstr (uint: expected uint, got 1.5; tstr: expected tstr, got 1.5)
```

The root rule may also be a primitive with controls (`id = tstr .size 2`,
`n = uint .le 5`) or an inline choice (`n = uint / tstr`). Ranges as types
(`0..255`) are not supported yet
([#71](https://github.com/sahebbiswas/cddl_verifier/issues/71)).

When the root type resolves to a socket choice the tool selects the matching
alternative based on the data.

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
| Extra key matching a computed key (`* tstr => uint`) with a matching value | ✅ pass |
| Extra key matching a computed key with a wrong value | ❌ fail |
| `[ + type ]` with empty array | ❌ fail |
| Array element wrong type | ❌ fail |

## Map members and inline maps

Members can be written in any RFC 8610 form: `name: T`, `1: T`, `"x": T`,
`1 => T`, and the IANA form `&(name: 1) => T`. A member with a computed key,
such as `* tstr => uint` or `* cose-label => cose-value`, allows any number of
extra keys that match the key type; their values must match the value type.

Inline maps are validated like named rules, wherever they appear:

```cddl
r = { a: { b: uint }, c: [ + { d: tstr } ], t: #6.9({ x: int }) }
```

Error messages refer to an inline map by a generated name, `<rule>@<path>`
(`r@a`, `r@c@0`, `r@t@tag`). EDN output does not show these names.

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
