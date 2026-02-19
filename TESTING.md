# Testing

## Overview

162 tests across four files in `tests/`.  The suite runs under both **pytest**
and the standard-library **unittest** runner with no code changes required.

| File | Tests | Covers |
|------|------:|--------|
| `tests/test_cbor_cddl_analyzer.py` | 48 | CDDL parsing, validation, EDN generation, CoRIM |
| `tests/test_simple_cbor.py` | 63 | CBOR encode/decode, diagnostics, round-trips |
| `tests/test_canonical_and_json.py` | 25 | Canonical encoding, JSON ↔ CBOR conversion |
| `tests/test_cbor_builder.py` | 26 | Iterative construction, nested access, merge |

---

## Running the tests

### pytest (recommended)

```bash
pip install pytest
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
> repo root so Python resolves `tests.test_*` module names correctly.  It is
> only strictly needed when old root-level `test_*.py` files are also present
> (e.g. during a migration); once only `tests/` contains test files it can be
> omitted.

### Direct execution

Each test file can also be run directly; it adds the repo root to `sys.path`
automatically so source modules are always found:

```bash
python3 tests/test_cbor_cddl_analyzer.py
python3 tests/test_simple_cbor.py
```

---

## Project layout

```text
.
├── cbor_cddl_analyzer.py
├── simple_cbor.py
├── cbor_json.py
├── pyproject.toml          ← pytest configuration
└── tests/
    ├── conftest.py         ← adds repo root to sys.path for pytest
    ├── __init__.py
    ├── test_cbor_cddl_analyzer.py
    ├── test_simple_cbor.py
    ├── test_canonical_and_json.py
    └── test_cbor_builder.py
```

`pyproject.toml` configures pytest:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts   = "--tb=short -q"
```

`tests/conftest.py` inserts the repo root into `sys.path` once for the whole
pytest session so every test module can `import cbor_cddl_analyzer` etc.
without needing its own path manipulation.

---

## CI configuration

### GitHub Actions

```yaml
# .github/workflows/ci.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install pytest
        run: pip install pytest

      - name: Run tests
        run: pytest
```

If you prefer to avoid the `pip install pytest` step, replace the last two
steps with:

```yaml
      - name: Run tests (unittest, no extra deps)
        run: python3 -m unittest discover -s tests -t .
```

### GitLab CI

```yaml
# .gitlab-ci.yml
test:
  image: python:3.11
  script:
    - pip install pytest
    - pytest
```

---

## Writing new tests

Add a method to the relevant `TestCase` class, or create a new class in the
appropriate file:

```python
import unittest
from cbor_cddl_analyzer import CDDLParser, CBORAnalyzer

class TestMyFeature(unittest.TestCase):

    def test_basic_case(self):
        cddl = CDDLParser("my-type = { &(id:0) => uint }")
        self.assertIn("my-type", cddl.types)

    def test_validation_pass(self):
        cddl = CDDLParser("my-type = { &(id:0) => uint }")
        self.assertTrue(CBORAnalyzer(cddl).validate({0: 1}, "my-type"))

    def test_validation_fail(self):
        cddl = CDDLParser("my-type = { &(id:0) => uint }")
        self.assertFalse(CBORAnalyzer(cddl).validate({0: "x"}, "my-type"))
```

pytest discovers any `TestCase` subclass automatically; no registration in a
`run_tests()` function is needed.