# CDDL AST and migration design

Status: **accepted** ([#87](https://github.com/sahebbiswas/cddl_verifier/issues/87)).
This document is for contributors. It defines the typed syntax tree that
[#69](https://github.com/sahebbiswas/cddl_verifier/issues/69) introduces, the
rules every parser and consumer must follow, and the order in which the current
string-based parser is replaced. The semantic issues that build on it are
[#70](https://github.com/sahebbiswas/cddl_verifier/issues/70) (resolution),
[#71](https://github.com/sahebbiswas/cddl_verifier/issues/71) (ranges),
[#72](https://github.com/sahebbiswas/cddl_verifier/issues/72) (groups),
[#73](https://github.com/sahebbiswas/cddl_verifier/issues/73) (literals) and
[#74](https://github.com/sahebbiswas/cddl_verifier/issues/74) (choices).

Nothing here changes the public API. The AST lives in internal modules.

---

## 1. Why

`CDDLParser.parse()` reads the schema one line at a time and decides what each
line is by looking for characters: `'{' in line`, `'(' in line and '/=' not in
line`, `'&(' in line_normalized`. It stores most type expressions as raw text,
for example `{'name': 'label', 'type': 'tstr .size (1..64)', 'optional': False}`.
The validator and EDN generator then parse that text again, each in its own way:

| Re-parser | What it reads from a string |
|-----------|-----------------------------|
| `extract_size_constraint`, `_size_bound` | `.size N`, `.size (M..N)`, `.size name` |
| `extract_value_range` | `.ge` / `.gt` / `.le` / `.lt` |
| `extract_regexp` | `.regexp "..."` |
| `extract_cbor_control` | `bstr .cbor T` |
| `extract_cbor_tag`, `_with_tag`, `_peel_tagged_primitive` | `#6.N(T)` |
| `_split_choice`, `_check_value`, `resolve_type_choice_for_data` | `a / b / c` |
| `_split_top_level_commas`, `_strip_closers` | map bodies, inline arrays |
| `_parse_registered_param` | `&(name: N) => T` |
| `EDNGenerator._generate_array`, `_generate_value` | `[ + T ]` inline arrays, `#6.N(T)` aliases |
| `type_name.split('<')[0]` | generic parameters (discarded) |

This causes recurring bugs that share one root cause. The parser can't
represent the structure, so each consumer guesses it again:

- [#100](https://github.com/sahebbiswas/cddl_verifier/issues/100): an inline map
  type (`{a: {b: uint}}`) is stored as the text `{b: uint}` and never checked.
- [#104](https://github.com/sahebbiswas/cddl_verifier/issues/104): `1 => int`
  members are dropped, and their lines become bogus aliases (`'1': '> tstr / int'`).
- [#102](https://github.com/sahebbiswas/cddl_verifier/issues/102) (fixed in
  #105): `c = a / b` was stored as an alias and validated differently depending
  on where it was used.
- Formatting changes meaning. A member split over two lines, a comment between
  tokens, or `{` on the next line all go down different code paths.
- Malformed CDDL is accepted silently (see the "lenient" note in
  [API.md](API.md#validatorschema)).

## 2. Goals and non-goals

Goals for #69:

1. One parser, built from the RFC 8610 Appendix B grammar as updated by
   RFC 9682, that turns schema text into a typed, immutable tree.
2. Every node carries its source location.
3. Malformed schemas raise a `SchemaError` subclass with a line and column,
   never `IndexError`, `RecursionError` or a silent skip.
4. Consumers read nodes, not text. Once a construct is migrated, no code parses
   its CDDL text again.
5. Incremental: each step leaves the test suite green and can ship on its own.

Non-goals for #69 (owned by later issues):

- Resolving names, checking for undefined or duplicate rules, and instantiating
  generics: #70.
- Range, group, literal and choice *semantics*: #71–#74. The AST represents
  these constructs exactly. What they mean when validating is decided there.
- New control operators: #76. The parser accepts any `.name` control. #70
  rejects unknown ones.
- Structured diagnostics: #75, which will use the spans defined here.
- A public AST API. It stays internal until the node shapes have settled
  through #70–#74.

## 3. Module layout

```
src/cddl_verifier/_cddl/
    __init__.py     # parse_cddl(text, *, source_name=None) -> Schema
    ast.py          # node dataclasses, Span, walk()
    lexer.py        # tokens + trivia (comments)
    parser.py       # recursive-descent parser
    printer.py      # format_node(node) -> canonical CDDL text
    errors.py       # CDDLSyntaxError
    prelude.py      # RFC 8610 Appendix D prelude as CDDL text, parsed like any schema
```

A subpackage keeps the new code out of the 2,900-line `_analyzer.py` and is
the first step of the module split in
[#83](https://github.com/sahebbiswas/cddl_verifier/issues/83). The resolver
from #70 is added as `_cddl/resolve.py`.

Python 3.9 is supported, so there are no `match` statements and no
`dataclass(slots=True)`.

## 4. Source locations

```python
@dataclass(frozen=True)
class Span:
    start: int          # offset of first code point, 0-based
    end: int            # offset after last code point (half-open)

class Source:
    text: str
    name: Optional[str]                     # file name, if known
    def line_col(self, offset) -> Tuple[int, int]   # 1-based line, 1-based column
```

- Offsets are in code points of the original text, not UTF-8 bytes. A tab
  counts as one column.
- Line and column are computed on demand from a line-start table, so nodes
  stay small.
- Every node has a `span` that covers its full extent, including any
  occurrence indicator or member key that belongs to it.
- `span` is declared with `field(compare=False)`, so two trees with the same
  structure compare equal whatever their positions. Tests can then write the
  expected trees without spans. The `comment` field on group entries (§6) is
  declared the same way, so node equality covers syntax only.
- Prelude nodes have spans into the prelude text and `Source.name == "<prelude>"`.

An empty schema, or one that holds only comments and whitespace, is valid
(RFC 9682) and parses to `Schema(rules=())`. The parser does not reject it.
`Validator` keeps its current behaviour for such a schema: without a
`root_type` there is no default root, and `validate("", 1)` raises
`SchemaError`.

## 5. Lexing

The lexer follows the RFC 8610 ABNF exactly. Whitespace and line breaks have
no meaning beyond separating tokens, which removes every line-based heuristic.

| Token | Notes |
|-------|-------|
| `ID` | `EALPHA *(*("-" / ".") (EALPHA / DIGIT))`. Matching is greedy, as the ABNF says, so `tstr.size` is one name and `min..max` is one name. Writing `tstr .size 3` and `min .. max` avoids that, as it does in other implementations. |
| `INT` | decimal, `0x`, `0b`, with optional leading `-`. The value is an unbounded Python `int`. |
| `FLOAT` | decimal with fraction and/or exponent, and hex floats (`0x1.8p3`) |
| `TEXT` | `"..."` with the RFC 9682 escapes |
| `BYTES` | `h'..'` (whitespace and comments allowed inside), `b64'..'`, `'..'` |
| punctuation | `= /= //= / // , : => ^ ? * + ( ) { } [ ] < > ~ & # . .. ...` |
| comment | `;` to end of line. Not a token: the lexer collects comments, and the parser attaches one to a group entry when it follows the entry (and its comma) on the same line |

Tabs are accepted as whitespace, although the ABNF allows only spaces and
line breaks. Rejecting them would break schemas for no benefit.

Comments are kept because the current parser uses a trailing comment as a
field name (`0: tstr ; name`), and EDN output depends on that (see §9).

## 6. Node catalog

All nodes are `@dataclass(frozen=True)`. Child sequences are tuples. Every
node has `span: Span`.

### Rules

```python
Schema(rules: Tuple[Rule, ...], source: Source)

TypeRule(name: str, params: Tuple[str, ...], assign: Literal["=", "/="], type: Type,
         maybe_group: bool)
GroupRule(name: str, params: Tuple[str, ...], assign: Literal["=", "//="], entry: GroupEntry)
```

`params` are the generic parameter names (`non-empty<M>` gives `("M",)`).

The grammar can't always tell a type rule from a group rule. `a = b` and
`a = (b)` are type rules if `b` is a type and group rules if `b` is a group.
The parser decides from syntax alone:

- `//=`, a member key, an occurrence indicator, or more than one entry inside
  `( )` gives a `GroupRule`.
- Otherwise the rule is a `TypeRule`. It gets `maybe_group=True` when its
  right-hand side is one name, or one name in parentheses, so that #70 can
  reclassify it once `b` is known.

### Types

```python
Type(alternatives: Tuple[Type1, ...])            # a / b / c, always >= 1

# Type1
Range(low: Type2, high: Type2, inclusive: bool)  # a..b (True), a...b (False)
Control(target: Type2, op: str, arg: Type2)      # tstr .size 16, op == "size"
# ... or any Type2 on its own

# Type2
IntLit(value: int, text: str)
FloatLit(value: float, text: str)
TextLit(value: str, text: str)
BytesLit(value: bytes, text: str)
Name(name: str, args: Tuple[Type1, ...])         # tstr, $socket, non-empty<{...}>
Paren(type: Type)                                # ( type )
Map(group: Group)                                # { group }
Array(group: Group)                              # [ group ]
Unwrap(name: str, args: Tuple[Type1, ...])       # ~name
ChoiceFrom(group: Group | None, name: str | None, args: ...)   # &( group ) or &name
Tag(tag: HeadNumber, type: Type)                 # #6.501(t), #6(t), #6.<1..3>(t)
Major(major: int, info: HeadNumber)              # #0, #1.5, #7.25, #7.<1..2>
AnyItem()                                        # #

HeadNumber = Union[int, Type, None]              # .N, .<type>, or absent
```

- A `Type` always wraps its alternatives, even when there is only one. Consumers
  handle one shape. Nothing is collapsed or reordered.
- Literals keep `text`, the source spelling, for messages and EDN.
  `value` is the decoded value: `0x10` gives `16`, and `h'0102'` gives
  `b"\x01\x02"`.
- `Name` is a reference. The parser does not decide whether it is a prelude
  type, a rule, a generic parameter or undefined. That is #70's job. `$` and
  `$$` stay part of `name`.
- A control's `op` has no leading dot. Any identifier is accepted.
- The parser also accepts chained controls (`uint .ge 0 .le 150`), which
  RFC 8610 does not allow, and reads them left to right: the `target` of the
  outer `Control` is the inner one. See Phase B in §10.
- `HeadNumber` is shared by `Tag` and `Major`. It holds the integer after the
  dot, the full `Type` written between `<` and `>` (RFC 9682), or `None` when
  there is no `.` part.

### Groups

```python
Group(choices: Tuple[GroupChoice, ...])          # a // b, always >= 1
GroupChoice(entries: Tuple[GroupEntry, ...])     # entries in source order

GroupEntry = Member | GroupRef | InlineGroup     # each has occurrence and comment

Member(occurrence: Optional[Occurrence], key: Optional[MemberKey], value: Type,
       comment: Optional[str])
GroupRef(occurrence, name: str, args: Tuple[Type1, ...], comment)
InlineGroup(occurrence, group: Group, comment)   # ( group )

MemberKey(kind: Literal["bareword", "value", "type"], cut: bool,
          name: Optional[str], value: Optional[Type2], type: Optional[Type1])
Occurrence(min: int, max: Optional[int], text: str)
```

- `name: T` gives `kind="bareword"` (the key is the text `"name"`).
  `1: T` and `"x": T` give `kind="value"`. `K => T` gives `kind="type"`, and
  `cut` is set for `K ^ => T`. In arrays the key is kept but has no effect on
  matching (RFC 8610 §3.4).
- `?` is `(0, 1)`, `*` is `(0, None)`, `+` is `(1, None)`, and `n*m` is
  `(n, m)`. `text` keeps the original spelling.
- `comment` is the trailing comment on the entry's last line, without the `;`
  (§9).
- A bare name with no key (`foo` inside a map) is a `GroupRef`. Whether it
  names a group or a type is decided by #70. RFC 8610 allows a type there only
  in arrays.

### The "IANA registered parameter" form is not special syntax

`&(name: 0) => tstr` is an RFC 8610 member whose key type is
`&(name: 0)`, the choice of values from the group `(name: 0)`. That choice
contains just `0`. The AST represents it exactly that way:

```
Member(key=MemberKey(kind="type",
                     type=ChoiceFrom(group=Group([GroupChoice([
                         Member(key=MemberKey(kind="bareword", name="name"),
                                value=Type([IntLit(0)]))])]))),
       value=Type([Name("tstr")]))
```

A helper, `registered_label(member) -> Optional[Tuple[str, Type2]]`, returns
`("name", IntLit(0))` when a key has this shape. `registered_params` and EDN
key names are built from that helper. The same helper also covers
`$role /= &(admin: 0)` in `test_groups_choices.cddl`.

### Traversal

`walk(node)` yields a node and all its descendants depth first, in source
order. There is no visitor class until a consumer needs one.

`entry_type(entry)` returns the `Type` that a keyless group entry with no
occurrence stands for: `GroupRef("x")` gives `Type[Name x]`, and an
`InlineGroup` with a single such entry gives a `Paren`. It returns `None` for
any other entry. The parser uses it to classify rules, and consumers use it for
array elements such as `[ + T ]`, which parse as `GroupRef`.

## 7. Parser

- Recursive descent over the token stream, one function per ABNF rule. The
  parser looks at most two tokens ahead. The only places that need two tokens
  are member keys (`name :` versus `name` as a type) and `/` versus `//`,
  which the lexer splits for us.
- Errors: `CDDLSyntaxError(SchemaError)` with `message`, `span`, `source`, and
  `expected` (a tuple of token descriptions). `str()` gives
  `schema.cddl:12:7: expected ':' or '=>' after member key, found '}'`.
  Parsing stops at the first error. Recovery and reporting several errors are
  left for #75.
- Nesting depth (brackets, parentheses, tags and generic arguments) is
  limited to 32 and the parser raises `CDDLSyntaxError` past that, so hostile
  input can't cause a `RecursionError`, in the parser or in anything that
  later recurses over the tree. The printer and the dataclass `==`, `hash()`
  and `repr()` use several Python frames per level, and on Python 3.9–3.11 C
  calls count toward the recursion limit too: `==` and `repr()` fail at about
  54 levels there. Real schemas nest far less (CoRIM about 8 levels). The fuzzing work in
  [#82](https://github.com/sahebbiswas/cddl_verifier/issues/82) relies on this.
- `printer.format_node(node)` writes canonical CDDL: single spaces, no
  comments. For every schema `s`, `parse(format(parse(s))) == parse(s)`.
  This holds because equality ignores `span` and `comment` (§4).
  Phase B uses the printer to fill the legacy `'type'` strings.

## 8. What #70 builds on the AST

These are listed so the node shapes above don't need changing later. They are
implemented in #70, not #69.

- **Symbol table.** Names map to rules in source order. RFC 8610 has a single
  namespace for types and groups. `/=` and `//=` add alternatives to a rule
  that is defined elsewhere (or nowhere, for sockets).
- **Prelude.** The prelude text (`prelude.py`) is parsed with the same parser and placed below
  user rules. `uint = #0`, `nint = #1`, `int = uint / nint`, `bigint = biguint
  / bignint` and the rest come from there. This replaces `_add_builtin_types`
  and the hand-written alias tables, and fixes
  [#99](https://github.com/sahebbiswas/cddl_verifier/issues/99).
- **Sockets and plugs.** `$name` and `$$name` with no alternatives are valid
  empty choices, not undefined names.
- **Generics.** Arity is checked, and arguments are substituted on demand when
  a reference is resolved, with memoization. Recursive rules are allowed
  because resolution follows references lazily instead of expanding them.
- **Output.** A resolved model in which each `Name` points to its target
  rule, or to a generic instance. The validator and EDN generator consume that
  model.

## 9. Compatibility boundaries

| Surface | Status | Rule |
|---------|--------|------|
| `validate`, `Validator`, `ValidationResult`, `SchemaError` | public | Unchanged, except that schema rejection becomes stricter (below). |
| `cddl-verify` CLI | public | Unchanged. |
| `CDDLParser`, `CBORAnalyzer`, `EDNGenerator` | internal | Can change. Their dict attributes stay until the last consumer has moved (Phase C), because the tests and EDN generator use them. |
| `_cddl` package | internal | New. Not exported from `cddl_verifier`. |

**Stricter schema parsing is a behaviour change.** Today
`Validator("r = {")` succeeds. With the AST parser it raises a
`CDDLSyntaxError`, which is a `SchemaError` and therefore a `ValueError`, so
callers that already catch `SchemaError` are covered. The change is made in
one step, the switch at the end of Phase B, and recorded in CHANGELOG as a
**Behaviour change**. Under the README versioning policy, that goes into the
unreleased version, or a `0.x` minor bump if 0.1.0 has shipped by then. The
public API does not change shape, so no major bump is needed.

**Comment-derived field names stay.** `0: tstr ; name` names field `0`
"name" in EDN output. This isn't RFC behaviour, but it is how the current
parser works and the EDN tests depend on it. The AST keeps the comment on the
entry (`comment`), and the legacy adapter uses it the way the current parser
does. It will be deprecated later
([#108](https://github.com/sahebbiswas/cddl_verifier/issues/108)).

## 10. Migration plan

Each phase is its own sub-issue of #69 and its own PR. The existing test suite
passes unchanged at the end of every phase, except for tests that the phase
deliberately changes and lists in CHANGELOG.

### Phase A: parser and AST, run in shadow mode ([#109](https://github.com/sahebbiswas/cddl_verifier/issues/109))

- Add `_cddl/` (lexer, AST, parser, printer, errors, prelude).
- `CDDLParser.__init__` also calls `parse_cddl()` and stores the result in
  `self.ast`, or the exception in `self.ast_error`. It catches every exception
  and logs it at debug level, so **this phase cannot change behaviour**.
- Tests (`tests/test_cddl_parser.py`):
  - one or more tests per construct in §6, comparing against an expected tree
    (spans are excluded from equality)
  - spans: line and column of chosen nodes, multi-line members, CRLF input
  - errors: message, line and column for about 20 malformed inputs, plus the
    nesting limit
  - every schema in `cddl-schemas/`, the RFC 8610 examples and the prelude
    parse with `ast_error is None`
  - printer round trip on all of the above

### Phase B: the AST becomes the only parser ([#110](https://github.com/sahebbiswas/cddl_verifier/issues/110))

- Replace the line-based `parse()` with an adapter that builds the same dicts
  (`types`, `type_aliases`, `groups`, `type_choices`, `socket_extensions`,
  `registered_params`, `first_definition`) from the AST. Type strings are
  produced by `format_node`, so consumers still get text but always in
  canonical form.
- During the PR, a temporary parity test compares the old and new dicts for
  every bundled schema. Every difference is either fixed or listed as an
  intended fix. The differences we expect are the parse-shape bugs #100
  (inline maps) and #104 (`key => type` members and bogus aliases). Each one
  that is fixed is recorded in CHANGELOG and its issue closed.
- Switch on strict parsing: `CDDLSyntaxError` propagates from `Validator`.
  This is a behaviour change (§9).
- Delete the line parser, `_parse_registered_param`, `_parse_type_choice`,
  `_parse_socket_extension`, `_split_top_level_commas` and `_strip_closers`,
  along with the temporary parity test.

As implemented in #110 (`_cddl/legacy.py`):

- The parity check ran over the 77 schemas the test suite and bundled files
  use. Every remaining difference was a legacy parse error that the adapter
  gets right, such as positional array elements that were dropped, maps inside
  `non-empty<{...}>` whose nested members leaked into the outer rule, and the
  bogus aliases from #104.
- **Synthetic types (#100).** The validator can only follow names, so an inline
  map, or a map or array directly inside a tag, becomes a type of its own
  named `<owner>@<path>` (`r@a`, `r@c@0`, `r@t@tag`). The names are listed in
  `CDDLParser.synthetic_types`, and EDN output does not annotate them. Inline
  arrays stay as text because the validator already reads them.
- **Computed keys (#104).** A member whose key is a type (`* tstr => any`)
  is listed in the map's `computed_keys`. The validator accepts extra keys
  that match one, and checks their values.
- **Generic wrappers.** `x = non-empty<{ ... }>` (one map or array argument)
  is read as the map inside, as the line-based parser did, until #70
  instantiates generics.
- **Chained controls.** `uint .ge 0 .le 150` is accepted as a non-standard
  extension and parsed as `Control(Control(uint, ge, 0), le, 150)`, because
  the standard forms are not enforced before #71 and #76. Each link counts
  toward `MAX_DEPTH`.

### Phase C: consumers read nodes instead of strings ([#111](https://github.com/sahebbiswas/cddl_verifier/issues/111))

Each legacy dict entry gains a `node` next to its `type` string. Consumers move
one construct at a time, and the string re-parser for that construct is deleted
in the same commit:

| Construct | Legacy code removed | Reads instead |
|-----------|---------------------|---------------|
| `.size` | `extract_size_constraint`, `_size_bound` | `Control(op="size")` with an `IntLit`, `Range` or `Name` argument |
| `.ge/.gt/.le/.lt` | `extract_value_range` | `Control(op=...)` |
| `.regexp` | `extract_regexp` | `Control(op="regexp", arg=TextLit)` |
| `.cbor` | `extract_cbor_control` | `Control(op="cbor")` |
| tags | `extract_cbor_tag`, `_peel_tagged_primitive`, string parts of `_with_tag` | `Tag` |
| choices | `_split_choice`, string parts of `_check_value` | `Type.alternatives` |
| EDN inline arrays and tags | the `[ + T ]` regex in `_generate_array`, `extract_cbor_tag` in `_generate_value` | `Array`, `Tag` |

When Phase C ends, the `type` strings are gone and #69 is done. Name lookup
(`resolve_type_alias`, `get_type`) still goes by name until #70 replaces it
with the resolved model.

### Example: `.size` before and after

```python
# Before: the field type is text, parsed again with a regex
constraint = self.cddl.extract_size_constraint(field['type'])   # 'tstr .size (1..64)'

# After: the field type is a node
for alt in field['node'].alternatives:
    if isinstance(alt, Control) and alt.op == "size":
        constraint = size_bound(alt.arg)   # IntLit | Range | Name
```

### Example: EDN array elements before and after

```python
# Before: EDNGenerator._generate_array matches the field's type text
array_match = re.match(r'^\[\s*([+*]?)\s*(.+?)\s*\]$', type_name)  # '[ + corim-locator-map ]'
if array_match:
    element_type = array_match.group(2).strip()               # 'corim-locator-map'

# After: the field's type is an Array node, and the element type is a node too
for alt in field['node'].alternatives:
    if isinstance(alt, Array):
        entry = alt.group.choices[0].entries[0]               # GroupRef(occ=+, name='corim-locator-map')
        element_node = entry_type(replace(entry, occurrence=None))  # Type[Name corim-locator-map]
```

The same change applies to tags. `_generate_value` currently looks up the
alias text `#6.501(unsigned-corim-map)` and runs `extract_cbor_tag` on it.
Instead it reads `Tag(tag=501, type=...)` from the rule's node and compares
`tag` with the decoded tag number.

### Example: a CoRIM rule

```cddl
tagged-corim-map = #6.501(corim-map)
corim-map = {
  &(id: 0) => $corim-id-type-choice
  ? &(dependent-rims: 2) => [ + corim-locator-map ]
  * $$corim-map-extension
}
```

```
TypeRule tagged-corim-map =
  Type[ Tag(tag=501, type=Type[ Name corim-map ]) ]
TypeRule corim-map =
  Type[ Map(Group[ GroupChoice[
    Member  key=type:ChoiceFrom((id: 0))              value=Type[ Name $corim-id-type-choice ]
    Member  occ=?  key=type:ChoiceFrom((dependent-rims: 2))
            value=Type[ Array(Group[ GroupChoice[ GroupRef occ=+ corim-locator-map ] ]) ]
    GroupRef occ=* $$corim-map-extension
  ]]) ]
```

The `Array` node is what fixes #100. Today the same member's type is the
string `[ + corim-locator-map ]`, which only works because one regex happens
to recognise it.

## 11. How this connects to other issues

| Issue | Relationship |
|-------|--------------|
| #70 | Builds the resolved model and the prelude on this AST (§8). |
| #71–#74, #76 | Implement semantics on `Range`, `Group`/`Occurrence`, literals, `Type.alternatives` and `Control`. |
| #75 | Diagnostics take the schema location from `Span`. |
| #82 | Fuzzes `parse_cddl`. It may raise only `CDDLSyntaxError`. |
| #83 | `_cddl/` is the first module taken out of `_analyzer.py`. |
| #86 | The conformance matrix gets a "parser/AST" column, filled in from Phase A's tests. |
| #94, #99, #100, #104 | Fixed by, or made simple by, Phases B and C and #70. |

## 12. Decisions

These were open questions in the first draft. They were agreed in review of
PR #107.

1. **Strict parsing starts at the end of Phase B**, not in Phase A, so no
   behaviour change ships before the AST has replaced the line parser.
2. **Comment-derived field names are kept for now and deprecated later**, once
   EDN takes labels from `registered_label` and bareword keys everywhere
   ([#108](https://github.com/sahebbiswas/cddl_verifier/issues/108)).
3. **Unknown control operators are accepted and ignored**, as they are today,
   until #70 rejects them as schema errors.
