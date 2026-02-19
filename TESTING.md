# CBOR-CDDL Analyzer — Test Suite

## Overview

Comprehensive unit test suite with **162 tests** across four files.

| File | Tests | Covers |
|---|---|---|
| `test_simple_cbor.py` | 63 | CBOR encode/decode, diagnostics, builder API |
| `test_cbor_cddl_analyzer.py` | 48 | CDDL parsing, validation, EDN generation, CoRIM |
| `test_canonical_and_json.py` | 25 | Canonical encoding (RFC 8949 §4.2), JSON conversion |
| `test_cbor_builder.py` | 26 | Iterative CBOR construction via builder pattern |

## Current Status

**162 / 162 tests passing (100% success rate)**

## Running the Tests

```bash
# All four test files at once
python3 -m unittest test_simple_cbor test_cbor_cddl_analyzer test_canonical_and_json test_cbor_builder

# Verbose
python3 -m unittest test_cbor_cddl_analyzer -v

# Single test class
python3 -m unittest test_cbor_cddl_analyzer.TestCDDLParsing

# Single test
python3 -m unittest test_cbor_cddl_analyzer.TestCDDLParsing.test_simple_alias
```

---

## test_cbor_cddl_analyzer.py — 48 tests

### 1. CDDL Parsing (9 tests)
- Simple type alias, CBOR tag definitions (`#6.501(...)`), `.cbor` control operator
- Type choice parsing (`$name /= ...`), IANA registered parameters, optional fields
- Size constraints (exact, range, min-only, max-only), multi-line field definitions

### 2. Type Resolution (3 tests)
- Simple alias, chained alias, tag inner-type extraction

### 3. CBOR Validation (4 tests)
- Simple map, optional fields, size constraints, array validation

### 4. EDN Generation (8 tests)
- `keyindex`, `keyname`, `both` formats; indentation; nested tag indentation;
  tag notation `tag_num(...)`; type name headers; `bytes<N>(...)` wrapper for nested CBOR

### 5. CoRIM Support (2 tests)
- Complex type-resolution chains, nested CBOR decoding with tag annotations

### 6. Edge Cases (6 tests)
- Empty map/array, nested empty structures, bytes encoding,
  undefined type graceful handling, circular alias prevention

### 7. Indentation Accuracy (5 tests)
- Simple map, nested map, tag, array, closing bracket alignment

---

## test_simple_cbor.py — 63 tests

- Encode/decode all primitive CBOR types (uint, nint, bstr, tstr, bool, null, float)
- Canonical encoding (RFC 8949 §4.2 — shortest form, sorted map keys)
- Diagnostic dump (`CBOR.diag()`) format and hex view
- Round-trip encode/decode for nested maps, arrays, tagged values
- Builder API (`CBOR.from_dict()`, `CBOR.from_list()`, etc.)
- Error handling for malformed or truncated input

---

## test_canonical_and_json.py — 25 tests

- `cbor_to_json` / `json_to_cbor` round-trips
- Type-annotated JSON conversion (`typed=True`)
- Bytes represented as Base64 in JSON
- CBOR tag preservation through JSON
- Integer key handling, `sort_keys` parameter

---

## test_cbor_builder.py — 26 tests

- `CBOR.builder()` entry point
- Append, extend, set, update operations
- Nested structure construction
- Canonical encoding of builder output
- Merge and copy operations

---

## Dependencies

**Required:** Python 3.7+, `cbor_cddl_analyzer.py`, `simple_cbor.py`, `cbor_json.py`

**Optional:** `cbor2` — if installed, `load_cbor()` uses it; otherwise falls back to `simple_cbor`.

```bash
pip install cbor2
```

---

## CI/CD

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: |
          python3 -m unittest \
            test_simple_cbor \
            test_cbor_cddl_analyzer \
            test_canonical_and_json \
            test_cbor_builder
```

---

## Adding Tests

```python
class TestMyFeature(unittest.TestCase):
    """Tests for <feature>."""

    def test_basic(self):
        cddl = CDDLParser("my-type = { 0: uint }")
        analyzer = CBORAnalyzer(cddl)
        self.assertTrue(analyzer.validate({0: 42}, "my-type"))

    def test_regression_issue_N(self):
        """Regression: <brief description of bug>."""
        ...
```

Always add a regression test when fixing a bug.

---

## Debugging Tips

```python
# Temporarily enable debug logging inside a test
import logging
logging.basicConfig(level=logging.DEBUG)

# Print actual vs expected on failure
def test_something(self):
    result = function_under_test()
    print(f"\nExpected: {repr(expected)}")
    print(f"Actual:   {repr(result)}")
    self.assertEqual(expected, result)
```