# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is defined once in `src/cddl_verifier/_version.py`. 0.1.0 is the first
public release ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)); the section below gets its date when the release is published.

## [Unreleased] - 0.1.0

### Changed
- **Behaviour change:** schemas are parsed by the new CDDL parser
  ([#110](https://github.com/sahebbiswas/cddl_verifier/issues/110), Phase B of
  [#69](https://github.com/sahebbiswas/cddl_verifier/issues/69)), and text that
  is not valid CDDL is rejected. `Validator` and `validate()` raise
  `SchemaError` with the position (`schema.cddl:12:7: expected a type, found
  '}'`, with `line` and `column` attributes), and `cddl-verify` exits with
  status 1. Before, malformed schemas were usually accepted silently. Forms the
  old parser tolerated that now need fixing:
  - `?` after a member's type: `&(a: 0) => uint ?` → `? &(a: 0) => uint`
  - unterminated rules, such as `x = #6.1([` with no closing brackets
  - an occurrence indicator without a type: `any = *`
  - text escapes outside RFC 9682 (`"\,"`): use `"\\,"` or drop the backslash

  Chained controls (`uint .ge 0 .le 150`) are still accepted, as a documented
  non-standard extension, so their bounds stay enforced until the standard
  forms are ([#71](https://github.com/sahebbiswas/cddl_verifier/issues/71),
  [#76](https://github.com/sahebbiswas/cddl_verifier/issues/76)).
- **Breaking:** the code is now the `cddl_verifier` package (source under
  `src/cddl_verifier/`). The top-level modules `simple_cbor`, `cbor_json`,
  `cbor_cddl_analyzer` and `_version` are no longer installed; their code lives
  in the internal modules `cddl_verifier._cbor`, `._json_codec`, `._analyzer`
  and `._version`. Use the public API below instead.
- `cddl-verify` now points at `cddl_verifier.cli:main`; `python -m cddl_verifier`
  runs the same CLI. The CBOR/JSON converter runs as
  `python -m cddl_verifier.json_codec`.
- Using the library no longer prints log messages: the package logger
  (`cddl_verifier`) has only a `NullHandler`, and the CLI attaches its coloured
  stderr handler while it runs.
- Package metadata: `requires-python = ">=3.9"`, classifiers, SPDX `MIT`
  license, project URLs, `test` and `dev` extras. The unused `cbor2` extra was
  removed; there are no runtime dependencies.
- **Behaviour change** from earlier unversioned code: `CBOR.load()`, `CBOR.loads()`, `cbor_decode()` and
  `load_cbor_bytes()` now decode exactly one complete CBOR data item. Trailing
  bytes after the item (including concatenated CBOR sequences), which were
  previously ignored silently, are rejected with `CBORTrailingDataError`
  ([#64](https://github.com/sahebbiswas/cddl_verifier/issues/64)). The analyzer CLI and nested `.cbor` validation inherit this behaviour.
- Decoder errors are now `CBORDecodeError` (a `ValueError` subclass) carrying the
  byte `offset` of the problem: `CBORTruncatedError` for empty or truncated
  input, `CBORTrailingDataError` for trailing bytes, `CBORUnsupportedError`
  (also a `NotImplementedError`) for indefinite-length items and `undefined`.
  Invalid UTF-8, reserved additional-info values, duplicate map keys and
  excessive nesting also raise `CBORDecodeError` instead of leaking
  `UnicodeDecodeError` / `RecursionError`.
- Decoding accepts `bytearray` and `memoryview`; non-bytes input raises
  `TypeError`.

### Fixed
- The validator and EDN generator no longer pick type text apart with regular
  expressions ([#111](https://github.com/sahebbiswas/cddl_verifier/issues/111),
  Phase C of [#69](https://github.com/sahebbiswas/cddl_verifier/issues/69));
  `_cddl/query.py` answers from the parsed type. Fixed along the way:
  - names containing dots (`coswid.tag-id`) were cut at the dot when checking
    a field or element type
  - **Behaviour change:** `.regexp` strings are decoded like any CDDL text
    string, so `"a\\.b"` is the regex `a\.b` (a literal dot), as in
    RFC 8610. Before, the raw text was used, so `"\\d"` matched a backslash
    followed by `d`.
  - byte-string literals (`h'0102'`, `b64'AQI'`, `'x'`) and literals with
    controls (`"t" .size 1`, `1 .ge 0`) are checked at the root and in choices.
    Before, a choice with such an alternative accepted any value
    ([#106](https://github.com/sahebbiswas/cddl_verifier/issues/106) for these
    alternatives). Literal field and element types are still unchecked
    ([#73](https://github.com/sahebbiswas/cddl_verifier/issues/73)).
  - an alias to a choice used with a control (`m .size 2`, `m = bstr / tstr`)
    was checked against its first alternative only
  - a field typed with an inline array of several entries (`p: [ int, tstr ]`)
    accepted a non-array value
  - embedded CBOR (`bstr .cbor T`) is checked in every context: a map field or
    array element whose inner type was not a named map or array (`bstr .cbor
    uint`) accepted any embedded item, and controls chained after `.cbor`
    (`bstr .cbor uint .size 1`) apply to the bytes as well
  - inline maps in a choice got synthetic names like `r1`; they are `r@1`,
    like other synthetic names
  - `lo..hi` is one name (names may contain dots); a `.size` argument naming
    an undefined `lo..hi` now suggests writing `lo .. hi`
- Inline map types are validated wherever they appear: as a field type
  (`a: { b: uint }`), as an array element, in a choice, and inside a tag
  (`#6.9({ x: int })`). Before, they were not checked at all
  ([#100](https://github.com/sahebbiswas/cddl_verifier/issues/100)). Error
  messages call them `<rule>@<path>` (`r@a`); EDN output does not show these
  names.
- Map members written `key => type` are parsed
  ([#104](https://github.com/sahebbiswas/cddl_verifier/issues/104)). A literal
  key (`1 => int`) is a field like `1: int`. A computed key
  (`* cose-label => cose-value`) allows extra keys of that type, whose values
  are checked. `COSE_Key` in `unified.cddl` now validates, and the bogus aliases
  such as `'1': '> tstr / int'` are gone.
- Other schemas the line-based parser misread: positional array elements
  without a name (`[ environment-map, [ + measurement-map ] ]`) were dropped;
  members of an optional inline group (`? ( a: int, b: int )`) were required;
  members of a map nested inside `non-empty<{ ... }>` leaked into the enclosing
  rule; a map or array inside a tag lost the tag; a whole single-line array
  (`[ min: int, max: int ]`) was read as one element type.
- Inline type choices (`c = a / b`) are validated
  ([#102](https://github.com/sahebbiswas/cddl_verifier/issues/102)). Before,
  a field or array element typed with one was checked against only the first
  alternative (rejecting valid data), or, for choices of named rules, not
  checked at all (accepting anything). Real CoRIM rules such as
  `svn-type-choice` and `protected-corim-header-map` are affected.
  Alternatives can be primitives with controls, named or tagged rules, and
  literal values (`1 / 2 / "draft"`); the error lists why each alternative
  failed.
- A root rule can be a primitive with any control (`n = uint .le 5`,
  `n = tstr .regexp "..."`) or an inline choice (`n = uint / tstr`), using the
  same checks as map fields
  ([#94](https://github.com/sahebbiswas/cddl_verifier/issues/94)).
- **Behaviour change:** an untagged rule no longer accepts data wrapped in
  any CBOR tag. `validate("m = { a: uint }", 999({"a": 1}))` was valid and is now
  rejected; validate tagged data against the tagged rule (for example
  `tagged-unsigned-corim-map` or `corim` instead of `corim-map` for a
  tag-501 CoRIM) ([#93](https://github.com/sahebbiswas/cddl_verifier/issues/93)).
- Tagged rules are enforced everywhere they are used, not only at the root:
  a missing or wrong tag is rejected in map fields, array elements and
  `$socket` choices. A field typed with a tagged rule (`x: t`, `x: #6.7(m)`)
  no longer rejects correctly tagged data. Array elements typed with a tagged
  rule (`[* t]`) were never checked before. Tags around primitives
  (`x: #6.7(uint)`) are checked, as are nested tags (`#6.1(#6.2(m))`).
- `.size` follows RFC 8610 §3.8.1
  ([#67](https://github.com/sahebbiswas/cddl_verifier/issues/67)). On `tstr`
  it counts UTF-8 bytes instead of characters. On `uint` it is enforced
  (`uint .size N` means `value < 256**N`). Exclusive ranges `(M...N)`, hex
  literals and named constants are accepted as arguments. Invalid arguments,
  empty ranges and `.size` on other types are reported instead of ignored.
  A root rule such as `id = tstr .size 2` can now be validated.
- Inline array field types (`a: [* tstr]`) lost their closing `]` during
  parsing, so their elements were never checked. Primitive elements of inline
  arrays are now type- and `.size`-checked.
- `canonical=True` now follows RFC 8949 §4.2.1 deterministic encoding: floats
  use the shortest exact width (float16/32/64) instead of always float64, NaN is
  always `f97e00`, and map keys that encode identically (e.g. two NaNs) raise
  `ValueError` ([#66](https://github.com/sahebbiswas/cddl_verifier/issues/66)).
- Integers outside the 64-bit range encode as tag 2/3 bignums instead of
  raising `struct.error`, and such bignums decode back to `int`. Bignums whose
  value fits in 64 bits still decode to `(tag, bytes)`. CDDL `uint` and `int`
  reject values outside the 64-bit range.
- Validating against a root rule that is a plain primitive (`n = uint`) now
  checks the value instead of always failing with "not a concrete type".
- A tagged root rule (`root = #6.501(inner)`) now requires the data to carry
  that tag number; data with a different tag, or no tag, was accepted before.

### Added
- Public API ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)):
  `cddl_verifier.validate()`, `Validator` (with `to_edn()`), `ValidationResult`,
  `Diagnostic` and `SchemaError`. Undecodable CBOR is reported as a `"decode"`
  diagnostic with a byte offset instead of being raised.
- `cddl_verifier.cbor` (`encode`, `decode`, `diag_dump`, `CBOR`, decode errors)
  and `cddl_verifier.json_codec` as the public CBOR and JSON helpers.
- `tests/test_public_api.py` for the public API and `python -m cddl_verifier`.
- CI builds the wheel and sdist, runs `twine check`, and runs the test suite
  against the installed wheel on Python 3.9–3.13 and the sdist on 3.11.
- `MANIFEST.in`, so the sdist includes the tests, test data and docs.
- README: installation from PyPI, quick start, supported features, known
  limitations and release steps.
- Versioning scheme: `_version.py` as the single version source, this
  changelog, `[project]` metadata in `pyproject.toml`, and a `--version` flag.
- `.github/workflows/publish_pypi.yml`: builds the sdist and wheel, runs
  `twine check`, smoke-tests both artifacts in clean environments, and
  publishes with PyPI Trusted Publishing: to TestPyPI on manual runs, and to
  TestPyPI then PyPI when a GitHub release is published (the only path to PyPI)
  ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)).
- `pip install .` installs a `cddl-verify` console script.
- `docs/CLI.md`, `docs/API.md` and `docs/CDDL_SUPPORT.md` reference pages; the
  README is now a short overview that links to them. EDN and diagnostic-dump
  samples were regenerated from real output.
- `tests/test_strict_decoding.py`: regression tests for scalars, maps, arrays,
  tags and nested data with trailing bytes, truncation at every prefix, and
  error offsets.
- Internal CDDL parser (`cddl_verifier._cddl`, Phase A of
  [#69](https://github.com/sahebbiswas/cddl_verifier/issues/69), [#109](https://github.com/sahebbiswas/cddl_verifier/issues/109)):
  a lexer, a typed immutable syntax tree with source spans, a recursive-descent
  parser for the RFC 8610 grammar as updated by RFC 9682, a canonical printer,
  and the RFC 8610 prelude. `CDDLSyntaxError` (a `SchemaError`) reports line and
  column. It replaces the line-based parser (#110): `CDDLParser.ast` holds the
  tree and its lookup tables are built from it. See
  [docs/CDDL_AST_DESIGN.md](docs/CDDL_AST_DESIGN.md).
- `tests/test_cddl_parser.py` for the new parser.

### Removed
- `fix_indent_proper.py`, a one-off script that edited `cbor_cddl_analyzer.py`
  in place and no longer applies to the package layout.
