# CDDL support

`cddl-verify` and `CDDLParser` parse the whole CDDL grammar (RFC 8610, as
updated by RFC 9682) and validate the subset used by practical attestation
schemas such as CoRIM and CoSWID. This page lists what is supported and how
validation behaves; [CONFORMANCE.md](CONFORMANCE.md) is the construct-by-construct
matrix (parser, AST, resolver, validator, EDN, tests, and what is not yet done),
checked against the code by the test suite
([#86](https://github.com/sahebbiswas/cddl_verifier/issues/86)).

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

The pattern is an XML Schema regular expression, which RFC 8610 names, and
not a Python one (#182). It matches the whole text; `^` and `$` are ordinary
characters; `\d` is any Unicode decimal digit; `\w` leaves out punctuation
(so `_`), separators and "other" characters; `\s` is only space, tab, line feed
and carriage return; `\i` and `\c` are XML name characters; `\p{Lu}`, `\P{Nd}` and
`\p{IsGreek}` name Unicode categories and blocks; `[a-z-[aeiou]]` subtracts a
class. Categories come from the running Python's Unicode database, and only
the common blocks are known. XSD has no lazy quantifier, `(?:...)`,
look-around or back reference: such a pattern, or an unknown category or
block, is a schema error that names it. The argument is checked again
once a generic's arguments are in (`g<"a+?">`). Matching uses Python's
backtracking engine, so a pattern such as `(a+)+b` can take very long on a
long text: do not use `.regexp` with schemas or data you do not trust
([#185](https://github.com/sahebbiswas/cddl_verifier/issues/185)).

`bstr .cborseq T` holds a CBOR sequence (RFC 8742). The sequence is decoded
and its items, taken as an array, must match `T`, which must be an array type:
`bstr .cborseq [* uint]` takes any number of uints, including none, and
`bstr .cborseq [uint, tstr]` takes exactly those two. A truncated or malformed
item is an error that names the item and the offset (#77).

`.bits` allows only the bit positions its argument names (RFC 8610 §3.8): a
`uint`, a range, a choice of them, or a rule or `&(...)` that is one. For a
`uint` position `n` is the bit worth `2**n`; for a `bstr` the first byte is
the least significant, so position 8 is the lowest bit of the second byte.
The error names the bit that is set. An argument that is not bit positions is
a schema error. A name from an import that is not loaded cannot be read, so it
is not enforced; the unloaded-module warning says so.

A type takes one control, as RFC 8610 says. Chained controls such as
`uint .ge 0 .le 150`, which earlier versions accepted as an extension, are a
syntax error (since 0.23.0) and the message gives the spelling to use:
`(uint .ge 0) .le 150`, which is enforced, or the range `0..150` (see
[Ranges](#ranges)). Likewise `(bstr .cbor uint) .size 1`, not
`bstr .cbor uint .size 1`.

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

`~x` (unwrap) takes the type out of a tag or the entries out of a map or an
array: `~t` with `t = #6.5(int)` is `int`, and `[ ~hdr, x: int ]` or
`{ ~hdr, x: int }` has the entries of `hdr` followed by `x`. A map or array
unwrapped in a place that needs a type (`a: ~m` with `m = { b: int }`) is a
group, not a type, so nothing matches it and the error says why; in a choice
(`c = ~m / tstr`) the other alternatives still match
([#106](https://github.com/sahebbiswas/cddl_verifier/issues/106)). A map
cannot splice an array (`{ ~hdr }` with `hdr = [ a: int ]` is rejected).

### Modules

A schema can import other CDDL files with the comment syntax of the CDDL
modules draft (draft-ietf-cbor-cddl-modules), as the CoRIM drafts do:

```cddl
;# import rfc9393 as coswid
;# include extra-types
```

Give `Validator` (or `cddl-verify`) the modules as text or files, or a search
path, and each module is checked as a schema of its own and its rules join the
schema: as `coswid.<rule>` for an import with `as`, under their own names for
an include or an import without `as`. References inside a module are renamed
with its rules, generic parameters keep shadowing them, sockets (`$x`, `$$x`)
stay shared, and modules may import modules (a cycle is an error). A name a
module does not define is an error that says so
([#121](https://github.com/sahebbiswas/cddl_verifier/issues/121)).

A module that is not found does not stop the schema from loading. Names used
from it (`coswid.tag-id`, or any undefined name after an import without `as`)
are not checked and match any value, a group from it in a map allows any
other entries, and `Validator.warnings` (the `Warning:` lines of `cddl-verify`)
says which modules were missing. No RFC modules are bundled.

### Generics

Generic rules are instantiated where they are used: `pair<uint, tstr>`, with
`pair<A, B> = [A, B]`, checks a two-element array of an unsigned integer and a
text string, as a rule, a map field or an array element. `opt<uint>`, with
`opt<T> = T / nil`, is the choice `uint / nil`. Recursive generics such as
`tree<T> = { v: T, ? l: tree<T> }` are checked at every depth. A generic can also be
the argument of `.cbor` (`bstr .cbor pair<uint, tstr>`): the embedded item is
checked against the instance. `.and` and `.within` are enforced on any
target, including a generic structure: `non-empty<{ ? a: int }>`, with
`non-empty<M> = (M) .and ({ + any => any })`, is the map `{ ? a: int }` that
must also match `{ + any => any }`, so `{}` is rejected. Other controls
around a generic structure (`.size`, ...) are not enforced yet
([#76](https://github.com/sahebbiswas/cddl_verifier/issues/76)).

A map member with a wildcard key counts the entries it takes and checks the
occurrence: `{ + tstr => int }` needs at least one, `{ 2*3 tstr => int }` two
to three, `{ ? tstr => int }` at most one, and a member without an occurrence
(`{ tstr => int }`) exactly one. A key goes to the first member whose key type
and value type both match and that still has room; if several members could
take it and none matches the value, the first one reports the value. A member
written `K ^=> V` (a cut) keeps a key matching `K` from the members after it.
A member written `K => V` with a literal key and no cut leaves its key free
when its value does not match, so a later wildcard can take the entry (RFC 8610
§3.5.4): `{ ? "a" => int, * tstr => any }` accepts `{"a": "x"}`. `K: V` and
`K ^=> V` keep the key. (RFC 9052 relies on this: `? 1 => int / tstr` followed
by `* label => values` leaves the header `alg` unconstrained to an int or text
string when an entry does not fit.) A required member still needs an entry of
its own.

A group choice that is repeated in a map (`{+ $$params}` with several
`$$params //= (...)`, `{* g}`, `{2*3 g}`) lets each repetition take any of its
alternatives, so a map can mix entries from several. An alternative with fixed
keys is taken at most once and needs all its required members; the number of
alternatives present has to fit the occurrence. An alternative with wildcard members repeats as often as its entries need (each repetition takes the member's own bounds), and the repetitions of all the alternatives together have to meet the occurrence of the choice. An ordinary member with the same key as an alternative's keeps that key. A repeated choice is not
enumerated, so the number of alternatives does not count against the
1024-combination limit.

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
| `float` | `float` | Any width; `int` rejected |
| `float16` / `float32` / `float64` | `float` | The width the float was written in (see below); `int` rejected |
| `null` / `nil` | `None` | |
| `undefined` | `UNDEFINED` | The CBOR `undefined` item, not `null` |
| `any` | anything | No type check performed |

**Float widths.** Data decoded from CBOR bytes keeps how each float was
written, so `float16` rejects a float written as a double: `fb 3ff8000000000000`
(1.5 as a double) is `float64`, and only `f9 3e00` is `float16`. `float` and the
choices `float16-32`, `float32-64` accept the widths they name. A float given as
a Python value was never written, so it matches every width it fits without
loss: `1.5` is `float16`, `float32` and `float64`, `1.1` only `float64`,
`1e300` only `float64`.

### Other prelude types

The rest of the RFC 8610 prelude (Appendix D) is checked as the prelude
defines it ([#130](https://github.com/sahebbiswas/cddl_verifier/issues/130)):

| CDDL type | Definition | Accepts |
|-----------|------------|---------|
| `number` | `int / float` | an integer or a float |
| `tdate` | `#6.0(tstr)` | tag 0 around an RFC 3339 date-time |
| `time` | `#6.1(number)` | tag 1 around an integer or a float |
| `uri` | `#6.32(tstr)` | tag 32 around an RFC 3986 URI reference |
| `b64url` | `#6.33(tstr)` | tag 33 around base64url text, without padding |
| `b64legacy` | `#6.34(tstr)` | tag 34 around base64 text, with padding |
| `regexp` | `#6.35(tstr)` | tag 35 around a regular expression |
| `mime-message` | `#6.36(tstr)` | tag 36 around a text string (not parsed as MIME) |
| `encoded-cbor` | `#6.24(bstr)` | tag 24 around a byte string holding one well-formed CBOR item |
| `eb64url`, `eb64legacy`, `eb16` | `#6.21(any)` … `#6.23(any)` | that tag around any value |
| `cbor-any` | `#6.55799(any)` | tag 55799 around any value |
| `decfrac`, `bigfloat` | `#6.4([e10: int, m: integer])`, `#6.5([e2: int, m: integer])` | that tag around an array of an `int` exponent and an `integer` mantissa |
| `biguint` | `#6.2(bstr)` | a bignum ≥ 2⁶⁴, or tag 2 around a byte string |
| `bignint` | `#6.3(bstr)` | a bignum < −2⁶⁴, or tag 3 around a byte string |
| `bigint` | `biguint / bignint` | either of the above |
| `integer` | `int / bigint` | any integer, of any size |
| `unsigned` | `uint / biguint` | any non-negative integer, of any size |

The content of tags 0, 24, 32, 33, 34 and 35 is checked for what it means, not
only for its type, wherever the tag is matched, however the schema writes it
(`tdate`, `#6.0(tstr)`, a field, an element, a choice) (#169):

- Tag 0: `YYYY-MM-DDThh:mm:ss[.fraction]` and `Z` or `+hh:mm`, with a real date
  (`2021-02-29` and month 13 are errors, a leap day needs a leap year), a
  time up to `23:59:59` and an offset up to `23:59`. Second 60 is allowed only
  as 23:59:60 UTC on the last day of March, June, September or December, so
  `1990-12-31T15:59:60-08:00` is valid and `2020-01-01T12:00:60Z` is not.
- Tag 32: a URI reference with a valid scheme, authority (user information,
  host, IPv6 literal, port), path, query and fragment, and `%HH` escapes.
  Spaces, non-ASCII characters and `<>"\^`{|}` must be escaped; `::x` is not a
  URI reference.
- Tags 33 and 34: the base64url and base64 alphabets, padding as the tag says,
  and the unused bits of the last character are 0 (`YQ` is valid, `YR` is not;
  RFC 8949 section 3.4.5.3).
- Tag 35: a pattern Python's `re` can compile, after `(?<name>...)` and
  `\k<name>` are read the Python way. This approximates the PCRE and ECMA-262
  the RFC names; a construct only one of them has is rejected.
- Tag 24: the bytes decode as exactly one CBOR item.
- Tag 36 is shape-only: the text is not parsed as a MIME message
  ([#187](https://github.com/sahebbiswas/cddl_verifier/issues/187)).

Content of the wrong type (a number under tag 0) is reported as before.

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
("has unexpected element [2]").

Elements are assigned to the entries in order, as RFC 8610 §3.4 describes:
each entry takes as many elements as its occurrence allows, a group name or
an inline group (`( int, tstr )`) takes its entries, and a group choice
(`//`) takes one of its alternatives. Every assignment is tried, so these
all match:

```cddl
a = [ ? int, tstr ]            ; ["x"] and [1, "x"]
b = [ * int, tstr ]            ; ["x"], [1, 2, "x"]
c = [ int, pair ]              ; [1, 2, "x"] with pair = (a: int, b: tstr)
d = [ + ( int, tstr ) ]        ; [1, "a", 2, "b"]
e = [ int // tstr, bool ]      ; [1] and ["x", true], not [1, true]
```

When no assignment takes every element, the error names the first element
that none gets past: "Array element [1] of 'c' matches no entry (int:
expected int, got str)", "is missing element [n]" when the array ends too
early, or "has unexpected element [i]" when the entries are used up. The
work grows linearly with the number of elements.

A group that refers to itself, directly or through other groups
(`g = (int, ? g)`), is followed as far as the elements go:
`[ g ]` takes one or more integers and rejects `[1, "x"]`. A rule that only
refers to itself without taking an element (`g = (? g)`) adds nothing. The
length of the array is not limited, but the work for such a group grows with
the square of the number of elements (10,000 elements take about a second and
a half; see [#138](https://github.com/sahebbiswas/cddl_verifier/issues/138)).

This holds wherever an array appears: as a rule, a map field, an array
element, a choice alternative or inside a tag (`decfrac` rejects `4([1])`).

## Ranges

```cddl
port   = 0..65535          ; an integer from 0 to 65535
offset = -128..127
ratio  = 0.0...1.0         ; a float from 0.0, below 1.0
level  = low .. high       ; bounds may name numeric constants
low    = 1
high   = 5
```

A range is checked wherever a type can be: at the root, in map fields, as an
array element, as a choice alternative or socket alternative, as a computed
map key (`* 0..9 => tstr`) and as a generic argument (`pair<0..3>`).

- `..` includes the upper bound and `...` excludes it (RFC 8610 §2.2.2.1).
- An integer range matches CBOR integers only (major types 0 and 1): not a
  float (`5.0`), a boolean or a bignum. A float range matches floats only, so
  `0.0..1.0` rejects the integer `1`.
- Both bounds are needed. `1..` and `..10` are not CDDL and raise
  `SchemaError`, as do a range between an integer and a float (`1..2.5`), an
  empty range (`5..1`, `1...1`) and text bounds (`"a".."z"`).
- Errors name the range: "11 is outside 0..10", "expected an integer in
  0..10, got 5.0".
- Controls on a parenthesized range apply to the number:
  `(0..100) .le 50` takes 0 to 50.
- `.eq` and `.ne` compare with one value and reject any other argument at
  load time. `.and` and `.within` must match both operands, so
  `(uint .ge 0) .le 150` and `{a: int} .and {a: 0..5}` are enforced
  ([#181](https://github.com/sahebbiswas/cddl_verifier/issues/181)).

The comparison controls `.ge`, `.gt`, `.le` and `.lt` work the same way on
integers and floats, at the root, in map fields and in array elements:
`{a: float .le 1.0}` rejects `{"a": 2.0}`.

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
- A tag number can be a type (RFC 9682): `#6.<uint>(tstr)` is any tag around a
  text string, and `#6.<uint .le 10>(any)` or `#6.<1 / 2>(any)` limit the
  number. `#6(t)` accepts any tag. A bignum the decoder returns as an integer
  counts as tag 2 or 3.

## Major types (`#`, `#0`, `#7.25`)

A reference by encoding matches data items by their major type, and is checked
at the root, in map fields, in array elements and in choices (`#1 / tstr`):

| Reference | Matches |
|-----------|---------|
| `#` | any data item |
| `#0`, `#1` | an unsigned integer, a negative integer (a 64-bit one; bignums are tags) |
| `#2`, `#3` | a byte string, a text string |
| `#4`, `#5` | an array, a map |
| `#6` | any tag (and a bignum) |
| `#6.N`, `#6.<type>` | a tag numbered N (or whose number the type allows) around any item, as `#6.N(#)`; the number is the tag number, not additional information (RFC 9682), so any N is checked |
| `#7` | `false`, `true`, `null` or a float |
| `#0.5`, `#1.0` | the integer 5, the integer -1 |
| `#2.2`, `#3.1`, `#4.2`, `#5.1` | a string, array or map of that length (below 24) |
| `#7.20`, `#7.21`, `#7.22` | `false`, `true`, `null` |
| `#7.23` | `undefined` |
| `#7.25`, `#7.26`, `#7.27` | a float written as a half, single or double (as `float16`, `float32`, `float64`) |
| `#7.N` for any other `N` | the simple value `N` (`#7.16` is `simple(16)`); 24 to 31 are not simple value numbers and match nothing |
| `#7.<type>` | whichever of the above the type's numbers (0 to 255) allow |

For majors 0 to 5 the number is the additional information of the item's
head: below 24 it is the value or length itself, 24 to 27 mean the argument is
written in 1, 2, 4 or 8 bytes, and 31 means an indefinite length. So `#0.24`
matches `1805` (5 written with a one-byte argument) and not `05`, `#0.5`
matches `05` and not `1805`, and `#4.31` matches `9f01ff` and not `8101`.
This needs how the item was written, which the validator has for CBOR bytes.
Data given as Python values is taken to be written the preferred way (the
shortest head): `5` is `#0.5`, `200` is `#0.24`, a three-element list is `#4.3`.

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

The argument of `.cbor` can be any type: a rule name, a generic instance
(`bstr .cbor pair<uint, tstr>`), an inline map (`bstr .cbor { a: uint }`) or an
inline array (`bstr .cbor [+ uint]`). The embedded item is checked against it.

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
`n = uint .le 5`), an inline choice (`n = uint / tstr`) or a range
(`n = 0..255`).

Every alternative is checked completely (tag number and content, fields,
controls), and a choice is resolved by that check alone
([#74](https://github.com/sahebbiswas/cddl_verifier/issues/74)): one matching
alternative decides it, none is an error, and no alternative is assumed. EDN
output uses the same resolution, so a value is labelled with the alternative it
matches (`/ m2 /`), even for alternatives that share a tag or are all maps, and
a value that matches none is left unlabelled. When data fits several
alternatives, which CDDL allows, validation accepts it and EDN uses the first
of them in schema order. `Validator(schema, unique_choices=True)` (or
`--unique-choices`) turns that into an error when more than one map, array or
tag alternative fits; overlapping primitives such as `uint / int` are not
reported.

A type socket (`$name`) is checked the same way as an inline choice of its
`/=` alternatives, wherever it is used: `$role /= uint` and `$role /= tstr`
make `role: $role` accept an unsigned integer or a text string and nothing
else. A socket with no alternatives in the schema matches nothing (RFC 8610
§3.9), so an optional field typed with one must be absent.

A socket of an imported module can be named with the module's prefix
(`coswid.$version-scheme`): it is the same shared socket as `$version-scheme`,
so extensions made in the importing schema apply to it. A prefixed name for a
socket the module does not have is still an undefined name. An optional group
socket (`? $$ext`) with no alternatives takes no entries.

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

Groups work in maps as they do in arrays (RFC 8610 §3.5, §3.7): a group
name, an inline group `( a: int, b: tstr )`, a group choice (`//`), a group
socket (`$$ext`) and an unwrapped map (`~hdr`) are expanded into their
members, with the occurrence written on them:

```cddl
r = { id: uint, header, ? ( x: int, y: int ) }   ; header = ( a: tstr, b: bool )
s = { (name: tstr) // (code: uint) }               ; exactly one of the two
t = { ~hdr, extra: int }                           ; hdr = { a: tstr }
```

A choice or an optional group gives the map several alternatives, and the map
must match one of them: `s` takes `{ "name": "x" }` or `{ "code": 1 }`, not
both and not neither, and `{ ? ( x: int, y: int ) }` takes both members or
neither. When no alternative matches, the errors of the closest one are
reported. A group that refers to itself adds its members once.

A literal key stands for the CBOR value it denotes, so `1.5: tstr`,
`h'01': int` and `true => int` are required members like `1: int`. Note that
`true: int` is the text key `"true"` (RFC 8610: a bare word before `:` is a
text string); write `true => int` for the simple value. The same goes for
`null => T` (or `nil => T`): it is a required member, where `null: T` is the text
key `"null"`.

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
