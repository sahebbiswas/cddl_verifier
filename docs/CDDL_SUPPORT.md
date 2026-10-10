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
standard spelling `(uint .ge 0) .le 150` is enforced too; the range `0..150`
parses but is not yet enforced
([#71](https://github.com/sahebbiswas/cddl_verifier/issues/71)).

## Schema semantics

A schema that parses is then checked as a whole
([#70](https://github.com/sahebbiswas/cddl_verifier/issues/70)), and the first
problem is reported the same way, with its position:

```
schema.cddl:4:12: undefined name 'tsrt'; did you mean 'tstr'?
```

| Check | Rejected example |
|-------|------------------|
| Every name is defined by the schema or the RFC 8610 prelude, or is a generic parameter in scope | `r = { a: tsrt }` |
| A name is defined once with `=`; alternatives are added with `/=` (types) or `//=` (groups). Writing the same rule twice is allowed | `a = 1` then `a = 2` |
| `$name` is a type socket and `$$name` a group socket; either may have no alternatives | `$$ext /= int`, `a: $$ext` |
| A group is not used as a type, and a map entry without a key is a group | `a: pii` where `pii = (x: int)`; `{ uint }` |
| `~x` names a map, array or tag; `&x` names a group | `~uint`, `&tstr` |
| A generic is given as many arguments as it has parameters | `pair<int>` where `pair<A, B> = [A, B]` |
| Control operators are those of RFC 8610, RFC 9165 and RFC 9741 | `tstr .sise 3` |
| A control fits its target and argument, where their kinds are evident | `float .size 4`, `tstr .regexp 'x'`, `tstr .le 5` |
| A range is between two integers or two floats, and is not empty | `5..1`, `1..2.5`, `"a".."z"` |
| An occurrence `n*m` has `n <= m` | `[ 3*2 int ]` |
| A rule is not defined only in terms of itself | `a = b` and `b = a` |

`a = b` and `a = ( b )` define a group when `b` is a group and a type
otherwise. Recursive rules (`tree = [ uint, * tree ]`) are fine.

Names from other modules are not checked when the schema imports the module in
the comment syntax of the CDDL modules draft (draft-ietf-cbor-cddl-modules), as
the CoRIM drafts do: after `;# import rfc9393 as coswid`, `coswid.tag-id` is
accepted, and after an `;# import` or `;# include` without `as`, any undefined
name is. The module is not loaded, so these names match any value
([#121](https://github.com/sahebbiswas/cddl_verifier/issues/121)).

### Generics

Generic rules are instantiated where they are used: `pair<uint, tstr>`, with
`pair<A, B> = [A, B]`, checks a two-element array of an unsigned integer and a
text string, as a rule, a map field or an array element. `opt<uint>`, with
`opt<T> = T / nil`, is the choice `uint / nil`. Recursive generics such as
`tree<T> = { v: T, ? l: tree<T> }` are checked at every depth. A control around a generic
structure is not enforced: `non-empty<{ a: int }>`, with
`non-empty<M> = (M) .and ({ + any => any })`, is checked as the map
`{ a: int }` ([#120](https://github.com/sahebbiswas/cddl_verifier/issues/120)).

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
| `nint` | `int < 0` | Major type 1 only: −2⁶⁴ … −1 |
| `int` | `int` | `bool` rejected |
| `bool` | `bool` | `int` rejected |
| `float` / `float16` / `float32` / `float64` | `float` | `int` rejected |
| `null` / `nil` | `None` | |
| `any` | anything | No type check performed |

### Other prelude types

The rest of the RFC 8610 prelude (Appendix D) is checked as the prelude
defines it ([#130](https://github.com/sahebbiswas/cddl_verifier/issues/130)):

| CDDL type | Definition | Accepts |
|-----------|------------|---------|
| `number` | `int / float` | an integer or a float |
| `tdate` | `#6.0(tstr)` | tag 0 around a text string |
| `time` | `#6.1(number)` | tag 1 around an integer or a float |
| `uri`, `b64url`, `b64legacy`, `regexp`, `mime-message` | `#6.32(tstr)` … `#6.36(tstr)` | that tag around a text string |
| `encoded-cbor` | `#6.24(bstr)` | tag 24 around a byte string |
| `eb64url`, `eb64legacy`, `eb16` | `#6.21(any)` … `#6.23(any)` | that tag around any value |
| `cbor-any` | `#6.55799(any)` | tag 55799 around any value |
| `decfrac`, `bigfloat` | `#6.4([e10: int, m: integer])`, `#6.5([e2: int, m: integer])` | that tag around an array of an `int` exponent and an `integer` mantissa |
| `biguint` | `#6.2(bstr)` | a bignum ≥ 2⁶⁴, or tag 2 around a byte string |
| `bignint` | `#6.3(bstr)` | a bignum < −2⁶⁴, or tag 3 around a byte string |
| `bigint` | `biguint / bignint` | either of the above |
| `integer` | `int / bigint` | any integer, of any size |
| `unsigned` | `uint / biguint` | any non-negative integer, of any size |

The decoder turns a bignum (tag 2 or 3) whose value does not fit in 64 bits
into a plain integer, and keeps one that fits as the tag and its bytes (see
[API.md](API.md)). So `biguint` accepts `2**64` and `(2, b"\x01")`, but not
`5`, which is a `uint`. A schema that defines its own rule with one of these
names (`uri = tstr`) gets its own rule.

## Literal types

```cddl
header = [ 1, tstr ]               ; first element must be the integer 1
kind = { type: "corim" }           ; the field must be the text "corim"
flags = { strict: true }           ; true only, not any bool
magic = h'd9d9f7'                  ; these three bytes
```

A literal matches only its own value, wherever it is used: map fields, array
elements, inline arrays, named rules (`version = 1`) and the root rule
([#73](https://github.com/sahebbiswas/cddl_verifier/issues/73)). Integers,
floats, quoted text, byte strings (`h'01'`, `'ab'`), `true` and `false` are
supported. The value must also have the literal's CBOR type: `1` does not
match `1.0` or `true`, `"a"` does not match the byte string `'a'`, and `0.0`
does not match `-0.0`. Controls on a literal apply too (`"ab" .size 2`,
`5 .le 10`). A schema that defines its own rule named `true` or `false` gets
that rule instead of the literal.

## Array types

```cddl
tags    = [ * tstr ]   ; zero or more text strings
aliases = [ + tstr ]   ; one or more (empty array fails validation)
point   = [ x: int, y: int, ? label: tstr ]   ; two or three elements
```

An array must have as many elements as its entries' occurrences allow: no
occurrence means exactly one, `?` zero or one, `*` any number, `+` one or more
and `n*m` between `n` and `m`. A type name counts as one element and a group
name as its entries (`[ int, pair ]` with `pair = (a: int, b: tstr)` takes
three). A missing or extra element fails with its index:
`[ int, tstr ]` rejects `[1]` ("is missing element [1]") and `[1, "a", 2]`
("has unexpected element [2]"). With group choices, the shortest and the
longest choice set the bounds. Elements are matched to entries by position;
when an optional entry comes before others, which entry an element belongs
to is not worked out yet
([#72](https://github.com/sahebbiswas/cddl_verifier/issues/72)).

This holds wherever an array appears: as a rule, a map field, an array
element, a choice alternative or inside a tag (`decfrac` rejects `4([1])`).

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

A type in parentheses is checked as the type inside them, and a control
outside the parentheses applies to each alternative inside them:
`(uint / tstr) .size 1` is `uint .size 1 / tstr .size 1`. A control on a named
choice works the same way, so with `m = uint / nil`, `m .size 1` is
`(uint / nil) .size 1`, as is `opt<uint> .size 1` with `opt<T> = T / nil`.
`.size` is defined only for `uint`, `bstr` and `tstr`, so `nil` does not match
any of these.

The root rule may also be a primitive with controls (`id = tstr .size 2`,
`n = uint .le 5`) or an inline choice (`n = uint / tstr`). Ranges as types
(`0..255`) are not supported yet
([#71](https://github.com/sahebbiswas/cddl_verifier/issues/71)).

A type socket (`$name`) is checked the same way as an inline choice of its
`/=` alternatives, wherever it is used: `$role /= uint` and `$role /= tstr`
make `role: $role` accept an unsigned integer or a text string and nothing
else. A socket with no alternatives in the schema matches nothing (RFC 8610
§3.9), so an optional field typed with one must be absent.

A choice from a group (`&(a: 0, b: 1)`, or `&colors` with
`colors = (red: 0, green: 1)`) is the choice of the group's entry types,
keys dropped: here `0 / 1`. Entries that are groups contribute their own
entries, and a control applies to each value (`&colors .size 1`). A group too
deeply nested to expand is reported as an error rather than accepted. CoRIM's role sockets (`$comid-role-type-choice /= &(creator: 1)`)
are checked this way.

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
| Array with a missing or extra element (`[ int, tstr ]` with `[1]`) | ❌ fail |
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
(`r@a`, `r@c@0`, `r@t@tag`). Inline arrays other than `[ + T ]` and
`[ * T ]` get such names too (`r = { o: [ int, tstr ] }` gives `r@o`). EDN
output does not show these names.

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
