# Testing

## Overview

The tests live in `tests/`.  The suite runs under both **pytest**
and the standard-library **unittest** runner with no code changes required.

| File | Covers |
|------|--------|
| `tests/test_public_api.py` | Public API: `validate`, `Validator`, `cddl_verifier.cbor`, `json_codec`, `python -m cddl_verifier` |
| `tests/test_cbor_cddl_analyzer.py` | CDDL parsing, validation, EDN generation, CoRIM (internal classes) |
| `tests/test_cddl_parser.py` | CDDL lexer, AST and parser: node shapes, spans, syntax errors, printer round trip, bundled schemas, strict parsing through the API and CLI |
| `tests/test_resolve.py` | `_cddl/resolve.py`: semantic errors with positions (undefined and duplicate names, sockets, type/group misuse, generic arity, controls, ranges, occurrences, alias loops), imports, the resolved model, generic instantiation in the tables (#70) |
| `tests/test_type_query.py` | `_cddl/query.py`: questions about type text answered from the AST (#111); dotted names, decoded `.regexp`, byte literals, controls on literals, parenthesized types (#118), choices from groups (#119) |
| `tests/test_legacy_tables.py` | `CDDLParser` tables built from the AST; inline maps (#100), inline arrays (#133) and `key => type` members (#104) |
| `tests/test_simple_cbor.py` | CBOR encode/decode, diagnostics, round-trips |
| `tests/test_cbor_builder.py` | Iterative construction, nested access, merge |
| `tests/test_canonical_and_json.py` | Canonical encoding, JSON ↔ CBOR conversion |
| `tests/test_deterministic_encoding.py` | RFC 8949 §4.2 deterministic encoding golden vectors |
| `tests/test_size_control.py` | RFC 8610 `.size` on `tstr`/`bstr`/`uint`, arguments, nested and root contexts |
| `tests/test_tag_validation.py` | CBOR tag checks for tagged and untagged rules at the root, in fields, arrays and choices |
| `tests/test_type_choices.py` | Inline type choices in fields, arrays and at the root; controlled primitives as the root rule; parenthesized types and controls on choices (#118); type sockets and choices from groups (`&(a: 0)`) in fields and arrays (#119) |
| `tests/test_strict_decoding.py` | Strict single-item decoding, error offsets, CLI `--version` |
| `tests/test_literal_types.py` | Literal types (`1`, `1.5`, `"a"`, `h'01'`, `true`) in map fields, arrays, inline arrays, named rules and the root (#73) |
| `tests/test_prelude_types.py` | RFC 8610 prelude types: `uri`, `tdate`, `time`, `number`, `decfrac` and other tagged rules (#130); `nint` and the bignum types `biguint`, `bignint`, `bigint`, `integer`, `unsigned` at the 64-bit boundaries (#99) |
| `tests/test_set_nested.py` | `set_nested` path creation and errors |
| `tests/test_cbor_diag_dump_extra.py` | Diagnostic dump edge cases and truncated input |
| `tests/test_array_length.py` | Array length from entry occurrences: missing and extra elements in rules, inline arrays, group references, choices and tags (#133) |
| `tests/test_array_matching.py` | Array elements assigned to entries in order: optional and repeated entries before others, group names, inline groups, group choices, error messages, linear time (#135) |
| `tests/test_json_map_keys.py` | CBOR map keys in JSON conversion: typed `$cbor` map pairs for non-string keys, untyped key collisions, repeated JSON object keys, bundled samples round trip (#91) |
| `tests/test_ranges.py` | Numeric ranges (`0..10`, `0.0...1.0`, named bounds) at the root, in fields, array elements, choices, sockets, computed keys and generic arguments; integer vs float matching; malformed ranges; `.ge`/`.le` on floats in fields and arrays (#71) |
| `tests/test_check_version_bump.py` | `.github/scripts/check_version_bump.py`: one-step bumps pass, missing or larger bumps and malformed `_version.py` fail (#125) |

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

## Project layout

```text
.
├── src/cddl_verifier/
│   ├── __init__.py         ← public API: validate, Validator, ValidationResult, …
│   ├── cbor.py             ← public CBOR helpers
│   ├── json_codec.py       ← public CBOR ↔ JSON conversion
│   ├── cli.py, __main__.py ← `cddl-verify` / `python -m cddl_verifier`
│   ├── _api.py             ← validate()/Validator implementation
│   ├── _analyzer.py        ← CDDL parser, validator, EDN generator (internal)
│   ├── _cbor.py            ← CBOR encoder/decoder (internal)
│   ├── _json_codec.py      ← JSON conversion (internal)
│   └── _version.py         ← single source of the project version
├── CONTRIBUTING.md         ← pull request checklist, versioning, releases
├── MANIFEST.in             ← extra files (tests, test data, docs) for the sdist
├── pyproject.toml          ← package metadata + pytest configuration
├── cddl-schemas/, test-data/  ← sample schemas and CBOR used by the tests
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
`from cddl_verifier._analyzer import CDDLParser`.

pytest discovers any `TestCase` subclass automatically; no registration in a
`run_tests()` function is needed.

Every pull request also bumps `__version__` in
`src/cddl_verifier/_version.py` and updates the README/docs it affects (see
[CONTRIBUTING.md](CONTRIBUTING.md)).
`tests/test_strict_decoding.py` checks that the CLI reports that version.

Decoder error tests should assert the specific `CBORDecodeError` subclass and its
`offset`, not just a bare `Exception`.
