# Testing

## Overview

The tests live in `tests/`.  The suite runs under both **pytest**
and the standard-library **unittest** runner with no code changes required.

| File | Covers |
|------|--------|
| `tests/test_public_api.py` | Public API: `validate`, `Validator`, `cddl_verifier.cbor`, `json_codec`, `python -m cddl_verifier` |
| `tests/test_cbor_cddl_analyzer.py` | CDDL parsing, validation, EDN generation, CoRIM (internal classes) |
| `tests/test_simple_cbor.py` | CBOR encode/decode, diagnostics, round-trips |
| `tests/test_cbor_builder.py` | Iterative construction, nested access, merge |
| `tests/test_canonical_and_json.py` | Canonical encoding, JSON ↔ CBOR conversion |
| `tests/test_deterministic_encoding.py` | RFC 8949 §4.2 deterministic encoding golden vectors |
| `tests/test_size_control.py` | RFC 8610 `.size` on `tstr`/`bstr`/`uint`, arguments, nested and root contexts |
| `tests/test_tag_validation.py` | CBOR tag checks for tagged and untagged rules at the root, in fields, arrays and choices |
| `tests/test_type_choices.py` | Inline type choices in fields, arrays and at the root; controlled primitives as the root rule |
| `tests/test_strict_decoding.py` | Strict single-item decoding, error offsets, CLI `--version` |
| `tests/test_set_nested.py` | `set_nested` path creation and errors |
| `tests/test_cbor_diag_dump_extra.py` | Diagnostic dump edge cases and truncated input |

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
├── CHANGELOG.md
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

1. **build** builds the sdist and wheel and runs `twine check --strict`.
2. **test** installs the wheel on Python 3.9–3.13 (and the sdist on 3.11),
   checks `import cddl_verifier`, `cddl-verify --help` and `--version` from
   outside the checkout, then runs the suite with
   `CDDL_VERIFIER_REQUIRE_INSTALLED=1`.

`.github/workflows/publish_pypi.yml` repeats the build and installed-package
checks before publishing; see the README's *Releasing* section.

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

When a change alters behaviour, also bump `__version__` in
`src/cddl_verifier/_version.py`, add a `CHANGELOG.md` entry, and update the
README/docs (see the README's *Versioning* section).
`tests/test_strict_decoding.py` checks that the CLI reports that version.

Decoder error tests should assert the specific `CBORDecodeError` subclass and its
`offset`, not just a bare `Exception`.
