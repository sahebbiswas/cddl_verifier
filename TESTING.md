# Testing

## Overview

The tests live in `tests/`.  The suite runs under both **pytest**
and the standard-library **unittest** runner with no code changes required.

| File | Covers |
|------|--------|
| `tests/test_public_api.py` | Public API: `validate`, `Validator`, `cddl_verifier.cbor`, `json_codec`, `python -m cddl_verifier` |
| `tests/test_module_layout.py` | Every private module imports on its own in a fresh interpreter (no import cycle); the CLI names moved out of `_analyzer` (#83) are still importable from it; the analyzer mixins in `_validator/` each import first and none shadows another's method |
| `tests/test_cbor_cddl_analyzer.py` | CDDL parsing, validation, EDN generation, CoRIM (internal classes) |
| `tests/test_edn_literals.py` | EDN literals (#227): `NaN`, `Infinity` and `-Infinity` at every float width and as map keys, finite floats unchanged, text strings and map keys escaped as JSON (C0 controls, DEL, C1 controls, U+2028/2029) and read back with `json.loads` |
| `tests/test_cddl_parser.py` | CDDL lexer, AST and parser: node shapes, spans, syntax errors, printer round trip, bundled schemas, strict parsing through the API and CLI |
| `tests/test_resolve.py` | `_cddl/resolve.py`: semantic errors with positions (undefined and duplicate names, sockets, type/group misuse, generic arity, controls, ranges, occurrences, alias loops), imports, the resolved model, generic instantiation in the tables (#70) |
| `tests/test_type_query.py` | `_cddl/query.py`: questions about type expressions answered from nodes (#111, #177); dotted names, decoded `.regexp`, byte literals, controls on literals, parenthesized types (#118), choices from groups (#119) |
| `tests/test_node_lookup.py` | Name lookup on nodes (#176): `get_type` gives the same definition for a name or expression as text and as a node, over the bundled schemas; `cbor_inner_type` is a node; `_peel_tagged_primitive` takes and returns nodes; root aliases and tags; embedded CBOR |
| `tests/test_type_nodes.py` | Type expressions as nodes (#175, #177): no table holds text over the bundled schemas and the conformance probes, a table type prints and parses back to itself, `as_type`, `resolve_alias`, `alias_node`, prelude renaming, and what `query` hands back |
| `tests/test_comment_names.py` | Members are named by key, bareword or label by default; a trailing comment names one only with `comment_names` / `--comment-names` (#108) |
| `tests/test_legacy_tables.py` | `CDDLParser` tables built from the AST; inline maps (#100), inline arrays (#133) and `key => type` members (#104) |
| `tests/test_simple_cbor.py` | CBOR encode/decode, diagnostics, round-trips |
| `tests/test_cbor_builder.py` | Iterative construction, nested access, merge |
| `tests/test_canonical_and_json.py` | Canonical encoding, JSON ↔ CBOR conversion |
| `tests/test_mime_message.py` | Tag 36 content must be a MIME message (#187): accepted and refused messages with the reason, folding, comments, multipart boundaries, parts and nesting, the tag check and the validator, no exception on random input, large and hostile input in linear time |
| `tests/test_benchmark.py` | `scripts/benchmark.py` (#85): every case builds, checks its own result and times; names, selection, the table, `--json`, `--compare`, `--fail-above`, `--list`, `--profile` |
| `tests/test_validation_caches.py` | What is remembered about schema nodes (#85) changes no answer: `ast.walk` order, the text kept on a node, the type-definition and check memos and their bounds |
| `tests/test_float_serialization.py` | Preferred serialization of floats in the default mode (#96): shortest widths equal to canonical mode, `float_width` for each width, refused values, decoded widths kept with `keep_encoding`, agreement with the `float16`/`float32`/`float64` checks |
| `tests/test_deterministic_encoding.py` | RFC 8949 §4.2 deterministic encoding golden vectors, Appendix A encoder vectors and float-width boundaries |
| `tests/test_rfc8949_conformance.py` | RFC 8949 decode vectors, non-preferred encodings, malformed/truncated inputs and strict single-item rejection |
| `tests/test_size_control.py` | RFC 8610 `.size` on `tstr`/`bstr`/`uint`, arguments, nested and root contexts |
| `tests/test_tag_validation.py` | CBOR tag checks for tagged and untagged rules at the root, in fields, arrays and choices |
| `tests/test_type_choices.py` | Inline type choices in fields, arrays and at the root; controlled primitives as the root rule; parenthesized types and controls on choices (#118); type sockets and choices from groups (`&(a: 0)`) in fields and arrays (#119) |
| `tests/test_diagnostics.py` | Structured diagnostics (#75): decoder item locations, data path, byte offsets and bounded hex, categories, expected/found, rule and schema location, embedded CBOR, `render_text` and `render_json` |
| `tests/test_cbor_tag.py` | `CBORTag` as a value (equality, hash, immutability, copy, pickle, tag-number checks), decode and encode round trips (nested, as a map key, canonical), the refused `(tag, value)` tuple, validator and EDN on tags, typed JSON (#78) |
| `tests/test_strict_decoding.py` | Strict single-item decoding, error offsets, duplicate map keys of every key type and depth (#92), CLI `--version` |
| `tests/test_literal_types.py` | Literal types (`1`, `1.5`, `"a"`, `h'01'`, `true`) in map fields, arrays, inline arrays, named rules and the root (#73) |
| `tests/test_tag_content.py` | what the content of tags 0, 24, 32, 33, 34, 35 and 36 must be (RFC 3339, URI reference, base64, regular expression, embedded CBOR, MIME message), and that the check runs at the root, in fields, elements, choices and nested tags (#169) |
| `tests/test_prelude_types.py` | RFC 8610 prelude types: `uri`, `tdate`, `time`, `number`, `decfrac` and other tagged rules (#130); `nint` and the bignum types `biguint`, `bignint`, `bigint`, `integer`, `unsigned` at the 64-bit boundaries (#99) |
| `tests/test_set_nested.py` | `set_nested` path creation and errors |
| `tests/test_cbor_diag_dump_extra.py` | Diagnostic dump edge cases and truncated input |
| `tests/test_array_length.py` | Array length from entry occurrences: missing and extra elements in rules, inline arrays, group references, choices and tags (#133) |
| `tests/test_array_matching.py` | Array elements assigned to entries in order: optional and repeated entries before others, group names, inline groups, group choices, error messages, linear time (#135) |
| `tests/test_socket_occurrences.py` | Occurrences on socket entries in arrays (#223): every occurrence at every length for a type socket and a group socket, sockets between other entries, structured and multi-element alternatives, `one-or-more<$s>`, empty sockets, and every array of up to four elements giving the same verdict with a socket as with the equivalent rule |
| `tests/test_cbor_control_args.py` | Embedded CBOR checked against the whole `.cbor` argument: generic instances, inline maps and arrays, tags; each error reported once (#127) |
| `tests/test_map_key_types.py` | Decoded map keys keep their CBOR type (`CBORKey`): byte-for-byte round trips, distinct `1` / `true` / `1.0` keys, duplicates and offsets, typed JSON, validation and EDN (#88) |
| `tests/test_json_annotations.py` | Typed JSON annotations read strictly (#79): base64 forms, unknown types, missing and extra keys, tag and simple values, map pairs, reserved keys, `annotations=False` / `--plain`, deep nesting, CLI exit |
| `tests/test_json_map_keys.py` | CBOR map keys in JSON conversion: typed `$cbor` map pairs for non-string keys, untyped key collisions, repeated JSON object keys, bundled samples round trip (#91) |
| `tests/test_ranges.py` | Numeric ranges (`0..10`, `0.0...1.0`, named bounds) at the root, in fields, array elements, choices, sockets, computed keys and generic arguments; integer vs float matching; malformed ranges; `.ge`/`.le` on floats in fields and arrays (#71) |
| `tests/test_cborseq.py` | `decode_sequence` (empty, several items, truncated and malformed items with offsets, `decode` still strict) and `.cborseq` in a root, field, element and rule (#77) |
| `tests/test_bits_regexp.py` | `.bits` on `uint` and `bstr` (argument forms, bit order, schema errors); `.regexp` read as XSD, one test per way XSD differs from Python, and the constructs it rejects (#182); hostile patterns (`(a+)+b`) return in linear time, and the state and step limits (#185) |
| `tests/test_set_controls.py` | `.eq`, `.ne`, `.and` and `.within` on primitives, choices, rules, tags, maps and arrays, in fields, elements and at the root; the bounded spelling `(uint .ge 0) .le 150`; the one-value rule for `.eq`/`.ne` (#181) |
| `tests/test_and_within.py` | `.and` / `.within` around generic structures (`non-empty<M>`) as a rule, field and array element; minimum counts of wildcard map members (`+ K => V`, `2*3 K => V`); an empty CoRIM `triples-map` (#120) |
| `tests/test_check_version_bump.py` | `.github/scripts/check_version_bump.py`: one-step bumps pass, missing or larger bumps and malformed `_version.py` fail (#125) |
| `tests/test_differential.py` | Differential testing against `pycddl` (the Rust `cddl` crate) (#81): every divergence on the conformance corpus and its mutants is explained in `docs/divergences.json`, stale explanations fail, minimized regression cases hold for this library; the `pycddl` part is skipped when it is not installed |
| `tests/test_map_repetition.py` | Repeated group choices in maps (#201): each repetition takes any alternative, required members of a present alternative, occurrence bounds, sockets, many alternatives; and `K => T` members that do not cut (#202), against `K: T` and `K ^=> T` |
| `tests/test_abnf.py` | ABNF for `.abnf` and `.abnfb` (#205): rules, repetition, ranges, case, `=/`, left and mutual recursion, ambiguity, the step limit, schema errors, bytes and text, computed grammars |
| `tests/test_computed_literals.py` | `.plus`, `.cat` and `.det` (#205): integers, floats, ranges, choices, names, generics, map keys, the RFC 9165 `interval<BASE>` example, dedenting, schema errors |
| `tests/test_text_controls.py` | The conversions, `.base10`, `.json`, `.printf` and `.join` (#205): accepted and rejected texts for each, stray bits, constrained results, format errors, ambiguous splits, schema errors |
| `tests/test_getting_started.py` | The getting-started guide (`docs/GETTING_STARTED.md`): its files are written out, its broken copies made with its own script, and every message and report it quotes must be what the tool prints now |
| `tests/test_cbor_items_extended.py` | Indefinite-length strings, arrays and maps, `undefined` and other simple values decode; `keep_encoding` records how an item was written; CDDL checks them (#168, #171) |
| `tests/test_check_findings.py` | Findings from type expressions carry their own details: `expected`/`actual`, the nested item's path, the embedded CBOR and tag contents (#153) |
| `tests/test_choice_resolution.py` | Choices are resolved by matching alone, with no first-alternative fallback (#74) |
| `tests/test_conformance.py` | The conformance matrix (`docs/conformance.json`) matches the code: every row names its tests, its probes are run, known gaps keep failing, and `docs/CONFORMANCE.md` is current (#86) |
| `tests/test_group_composition.py` | Group composition in maps and arrays, and wildcard occurrence bounds (#72, #151, #158) |
| `tests/test_item_refs.py` | References to data items by encoding (`#`, `#M`, `#M.N`, `#6(t)`, `#6.<type>(t)`) in the root rule, fields, elements, choices and named rules (#167) |
| `tests/test_modules.py` | Loading imported CDDL modules: `-M`, `--module`, names, cycles and missing modules (#121) |
| `tests/test_schema_diagnostics.py` | `schema_diagnostics` returns every schema problem as a `Diagnostic` (#156) |
| `tests/test_corpus.py` | Conformance corpus (#80): 605 cases from RFC 8610 sections, the cddlc suite, COSE, CoRIM, CoSWID, EDHOC and SUIT, each with the source's verdict, constructs and provenance; known gaps, divergences and source defects must keep disagreeing (see [docs/CORPUS.md](docs/CORPUS.md)) |
| `tests/test_require_deterministic.py` | `require_deterministic` (#95): each RFC 8949 §4.2.1 rule refused at the offset of the first violation (integer, length and tag heads, indefinite lengths, float widths and NaN, key order, bignums), deterministic vectors accepted, accepted bytes equal `encode(canonical=True)`, sequences, `validate()` and the CLI |
| `tests/test_cli_diagnostics.py` | `cddl-verify --diagnostics json` (#145): stdout is only the JSON document, schema, decode, missing and unreadable-input failures as diagnostics, `-o` and EDN, inferred-type warnings, ordering; `path_expr` notation |
| `tests/test_nested_arrays.py` | Arrays written inside arrays, map members and choices are checked element by element, with the path and offset of the element (#196) |
| `tests/test_properties.py` | Property-based tests (#82): arbitrary and damaged bytes, deep nesting and huge lengths for the decoder, encode/decode and JSON round trips, arbitrary schema text, and validation checked against a reference written in a few lines of Python; see [Property-based testing](#property-based-testing) |

---

## Setup

Install the package in editable mode with the test extra:

```bash
pip install -e ".[test]"
```

Without an installation the tests fall back to the source tree under `src/`,
so a plain checkout also works.

## Running the tests

### pytest (recommended)

```bash
pytest                    # uses testpaths = ["tests"] from pyproject.toml
pytest -v                 # verbose: one line per test
pytest tests/test_cbor_cddl_analyzer.py          # single file
pytest tests/test_cbor_cddl_analyzer.py::TestCDDLParsing           # single class
pytest tests/test_cbor_cddl_analyzer.py::TestCDDLParsing::test_simple_alias  # single test
```

### unittest (no extra dependencies)

```bash
python3 -m unittest discover -s tests -t .   # all tests
python3 -m unittest tests.test_cbor_cddl_analyzer                  # single module
python3 -m unittest tests.test_cbor_cddl_analyzer.TestCDDLParsing  # single class
python3 -m unittest tests.test_cbor_cddl_analyzer.TestCDDLParsing.test_simple_alias
```

> **Note on `-t .`** — the `-t .` flag sets the top-level directory to the
> repo root so Python resolves `tests.test_*` module names correctly.

### Direct execution

Each test file can also be run directly. If `cddl_verifier` is not installed,
it adds `src/` to `sys.path` itself:

```bash
python3 tests/test_cbor_cddl_analyzer.py
python3 tests/test_public_api.py
```

### Testing a built distribution

CI runs the suite against the installed wheel and sdist rather than the source
tree. To do the same locally:

```bash
python -m build                      # writes dist/*.whl and dist/*.tar.gz
python -m venv /tmp/venv
/tmp/venv/bin/pip install dist/*.whl -r requirements.txt
CDDL_VERIFIER_REQUIRE_INSTALLED=1 /tmp/venv/bin/python -m pytest tests/
```

`CDDL_VERIFIER_REQUIRE_INSTALLED=1` makes `tests/conftest.py` fail if
`cddl_verifier` would be imported from `src/` instead of the installation.

---

## Differential testing

`scripts/differential.py` validates the conformance corpus (every
single-module probe in `docs/conformance.json`) and mutants of its valid
examples (an item dropped, added or duplicated, a value replaced by one of
another type) with this library and with
[`pycddl`](https://pypi.org/project/pycddl/), the Rust `cddl` crate, which is
the independent implementation available from PyPI. `pycddl` runs in a
subprocess per schema because it can crash on recursive rules.

Each disagreement is a *divergence* with a `kind`:

| Kind | Meaning |
|------|---------|
| `parser` | one side rejects a schema the other accepts |
| `validation` | both accept the schema and disagree about the data |
| `crash` | `pycddl` crashed or timed out on the schema |

`docs/divergences.json` explains each one with a `cause`: `their-unsupported`
(the other implementation lacks the feature), `their-bug` (it contradicts the
RFC), `spec-ambiguous`, or `our-bug` (needs an `issue`, and the conformance
matrix lists that issue). An entry names divergences by `row`, `schema`,
`data`, `kind`, `ours` and `theirs`, any of which may be a list. Entries with
`fixture: true` are minimized cases with the RFC verdict in `expected`; they
are checked against this library on every run, with or without `pycddl`.

**What fails CI** (`tests/test_differential.py`, with `pycddl` installed as
part of the test requirements):

- a divergence that no entry explains;
- an entry that matches no divergence any more (fix it or delete it), except
  fixtures;
- an entry without a cause or reason, or an `our-bug` without an issue;
- a fixture on which this library disagrees with the RFC verdict.

```bash
pip install pycddl
python scripts/differential.py report --untriaged   # new divergences
python scripts/differential.py minimize             # shrink their data, then adjudicate against the RFC
python scripts/differential.py check                # the CI policy; exit 1 on failure
```

When a divergence is new, decide who is right from the RFC (not from either
implementation), fix this library if it is wrong or open an issue, and add an
entry; add a `fixture` for a divergence worth pinning.

### Benchmarks

`python scripts/benchmark.py` times parsing, resolution, validation, regular expressions, CBOR
and EDN at two sizes each and reports the growth; `--compare` and `--profile` find what changed
and where the time goes. See [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## Property-based testing

`tests/test_properties.py` uses [Hypothesis](https://hypothesis.readthedocs.io)
and is skipped when it is not installed (`pip install -e ".[test]"` has it). It
checks four things:

- **Arbitrary input fails in a controlled way.** Any bytes, bytes with one byte
  changed, removed or added, a hundred thousand levels of nesting and claimed
  lengths far beyond the input give a decoded value or a `CBORDecodeError`,
  never an `IndexError`, `RecursionError` or `MemoryError`. Any schema text
  (random text and random runs of CDDL tokens) gives a schema or a
  `SchemaError`, and `schema_diagnostics` never raises.
- **Supported values round-trip** through CBOR, canonical CBOR (stable, and
  independent of key order) and typed JSON.
- **Validation agrees with a reference.** Schemas are built from a small grammar
  (scalars, arrays with occurrences, maps with text or integer keys, wildcard
  maps, choices, ranges, literals, `.size`, tags, named rules), and `accepts()`
  in the test says whether data matches. Data is generated to match, or to
  nearly match with one element, member, length or tag off, and the validator
  must give the same answer, with a diagnostic when it says no. This is the
  test that finds validation that is too loose or too strict (#196).
- **Nothing hangs**: each example has a five second budget.

By default the examples are the same on every run, so a failure is never a
flake. To search further:

```bash
CDDL_VERIFIER_FUZZ_EXAMPLES=5000 CDDL_VERIFIER_FUZZ_RANDOM=1 pytest tests/test_properties.py
```

Hypothesis minimizes a failing example and prints it. Fix the cause, and keep
the example in `TestRegressions` (with the issue number) so it stays fixed.

## Project layout

```text
.
├── src/cddl_verifier/
│   ├── __init__.py         ← public API: validate, Validator, ValidationResult, …
│   ├── cbor.py             ← public CBOR helpers
│   ├── json_codec.py       ← public CBOR ↔ JSON conversion
│   ├── cli.py, __main__.py ← `cddl-verify` / `python -m cddl_verifier`
│   ├── _api.py             ← validate()/Validator implementation
│   ├── _analyzer.py        ← CBORAnalyzer: the shared state, composed from the mixins in `_validator/` (internal)
│   ├── _validator/         ← the analyzer's methods, one module per concern: reporting, validation, structures, containers, values, primitives, textcontrols, embedded, support (internal, #83)
│   ├── _schema.py          ← CDDLParser: the loaded, resolved schema as the tables the validator uses (internal, #83)
│   ├── _edn.py, _decode.py ← EDNGenerator; SimpleCBORDecoder (internal, #83)
│   ├── _cli.py, _log.py    ← the `cddl-verify` command; its colours and stderr logging (internal, #83)
│   ├── _diagnostics.py     ← Diagnostic data model and renderers (internal)
│   ├── _cbor.py            ← CBOR encoder/decoder (internal)
│   ├── _cbor_values.py     ← CBORTag, undefined, simple values, how an item was written (internal)
│   ├── _json_codec.py      ← JSON conversion (internal)
│   ├── _cddl/              ← CDDL lexer, parser, AST, resolver, modules, controls (internal):
│   │   ├── lexer.py, parser.py, ast.py, printer.py   ← syntax and the tree
│   │   ├── resolve.py, modules.py, prelude.py        ← names, schema checks, imports, RFC 8610 prelude
│   │   ├── legacy.py, query.py, typeexpr.py          ← tables and queries the validator reads
│   │   ├── computed.py, abnf.py, conversions.py      ← .plus/.cat/.det, .abnf, .b64u … .printf/.join
│   │   └── xsd_regex.py, tagcontent.py, items.py     ← .regexp, tag contents, #M.N references
│   └── _version.py         ← single source of the project version
├── CONTRIBUTING.md         ← pull request checklist, versioning, releases
├── MANIFEST.in             ← extra files (tests, test data, docs) for the sdist
├── pyproject.toml          ← package metadata + pytest configuration
├── cddl-schemas/, test-data/  ← sample schemas and CBOR used by the tests
├── docs/                   ← guides, the conformance matrix and its inputs (conformance.json, divergences.json)
├── scripts/                ← render_conformance.py, render_corpus.py, build_corpus.py, differential.py, benchmark.py
└── tests/
    ├── conftest.py         ← imports the installed package, or falls back to src/
    ├── __init__.py
    └── test_*.py
```

`pyproject.toml` configures pytest:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts   = "--tb=short -q"
```

---

## CI configuration

`.github/workflows/ci.yml` runs on pushes and pull requests to `main`:

1. **version-bump** (pull requests only) checks that `_version.py` is exactly
   one SemVer step above the version on `main`, using
   `.github/scripts/check_version_bump.py` (see CONTRIBUTING.md).
2. **build** builds the sdist and wheel and runs `twine check --strict`.
3. **test** installs the wheel on Python 3.9–3.13 (and the sdist on 3.11),
   checks `import cddl_verifier`, `cddl-verify --help` and `--version` from
   outside the checkout, then runs the suite with
   `CDDL_VERIFIER_REQUIRE_INSTALLED=1`.

`.github/workflows/publish_pypi.yml` repeats the build and installed-package
checks before publishing; see *Releasing* in CONTRIBUTING.md.

---

## Writing new tests

Prefer the public API in new tests:

```python
import unittest
from cddl_verifier import Validator

class TestMyFeature(unittest.TestCase):

    def test_validation_pass(self):
        validator = Validator("my-type = { &(id:0) => uint }")
        self.assertTrue(validator.validate({0: 1}).valid)

    def test_validation_fail(self):
        result = Validator("my-type = { &(id:0) => uint }").validate({0: "x"})
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].code, "validation")
```

Tests of internals import from the private modules, for example
`from cddl_verifier._schema import CDDLParser`. The command-line functions (`main`, `load_cddl`, `load_cbor`) live in `_cli`, `EDNGenerator` in `_edn`, `SimpleCBORDecoder` in `_decode` and `CDDLParser` in `_schema`; `_analyzer` still hands them out under the old import path.

pytest discovers any `TestCase` subclass automatically; no registration in a
`run_tests()` function is needed.

Every pull request also bumps `__version__` in
`src/cddl_verifier/_version.py` and updates the README/docs it affects (see
[CONTRIBUTING.md](CONTRIBUTING.md)).
`tests/test_strict_decoding.py` checks that the CLI reports that version.

Decoder error tests should assert the specific `CBORDecodeError` subclass and its
`offset`, not just a bare `Exception`.
