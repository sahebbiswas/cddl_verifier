[![Python CI](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml/badge.svg)](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml)

# cddl_verifier

A Python toolkit for working with CBOR (Concise Binary Object Representation) data and
CDDL (Concise Data Definition Language) schemas. It covers the full workflow:
encoding and decoding CBOR, validating data against a CDDL schema, generating annotated
EDN (Extended Diagnostic Notation) output, and converting between CBOR and JSON.

## Modules

| Module | Purpose |
|--------|---------|
| `cbor_cddl_analyzer.py` | CDDL schema parser, CBOR validator, annotated EDN generator, and the `cddl-verify` CLI |
| `simple_cbor.py` | CBOR encoder, strict decoder, diagnostic dumper, and builder |
| `cbor_json.py` | CBOR ↔ JSON conversion with type preservation |
| `_version.py` | Single source of truth for the project version |

## Installation

```bash
pip install .            # installs the modules and the `cddl-verify` command
pip install ".[cbor2]"   # optionally also installs cbor2
```

There are no required dependencies. You can also run the scripts directly from a
checkout without installing (`python cbor_cddl_analyzer.py …`). `cbor2` is only used
by the `load_cbor_file()` helper; the CLI and `simple_cbor` always use the bundled
decoder.

## Quick start

Validate a CBOR file against a CDDL type and print annotated EDN:

```bash
cddl-verify schema.cddl data.cbor --type corim-map
```

The same from Python, using the current implementation classes:

> **Provisional API.** `CDDLParser`, `CBORAnalyzer` and `EDNGenerator` are
> internal classes and will change during the CDDL AST/resolution work. A stable
> `cddl_verifier` package with a `validate()` / `Validator` facade is planned for
> the 0.1.0 release ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)); prefer that once it is available.

```python
from cbor_cddl_analyzer import CDDLParser, CBORAnalyzer, EDNGenerator
from simple_cbor import CBOR

cddl = CDDLParser(open("schema.cddl").read())
data = CBOR.loads(open("data.cbor", "rb").read())   # exactly one CBOR item

analyzer = CBORAnalyzer(cddl)
if not analyzer.validate(data, "corim-map"):
    print(analyzer.get_errors())
print(EDNGenerator(cddl).generate(data, "corim-map"))
```

Encode, decode and convert CBOR:

```python
from simple_cbor import cbor_encode, cbor_decode
from cbor_json import cbor_to_json

raw = cbor_encode({0: "Alice", 1: b"\x01"}, canonical=True)
assert cbor_decode(raw) == {0: "Alice", 1: b"\x01"}
print(cbor_to_json(raw, typed=True, pretty=True))
```

Decoding is strict: the input must be exactly one complete CBOR item. Empty,
truncated, or trailing-byte input raises `CBORDecodeError` with the byte offset of
the problem.

## Documentation

| Document | Contents |
|----------|----------|
| [docs/CLI.md](docs/CLI.md) | `cddl-verify` and `cbor_json.py` options, input rules, EDN output formats |
| [docs/API.md](docs/API.md) | Python API for all modules, including decode errors and JSON conversion |
| [docs/CDDL_SUPPORT.md](docs/CDDL_SUPPORT.md) | Supported CDDL syntax, types, and validation behaviour |
| [docs/CORIM_SUPPORT.md](docs/CORIM_SUPPORT.md) | CoRIM / CoSWID schema support |
| [docs/CBOR_BUILDER_QUICK_REF.md](docs/CBOR_BUILDER_QUICK_REF.md), [docs/ITERATIVE_CONSTRUCTION.md](docs/ITERATIVE_CONSTRUCTION.md) | Builder API in depth |
| [docs/CBOR_DIAGNOSTIC_DUMP.md](docs/CBOR_DIAGNOSTIC_DUMP.md) | Diagnostic dump format |
| [docs/CANONICAL_AND_JSON.md](docs/CANONICAL_AND_JSON.md) | Canonical encoding and JSON round-trips |
| [docs/](docs/) | EDN formatting, tag notation, and annotation notes |
| [TESTING.md](TESTING.md) | Running and writing tests, CI setup |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Testing

```bash
pytest                                       # requires: pip install pytest
python3 -m unittest discover -s tests -t .  # no extra dependencies
```

See [TESTING.md](TESTING.md) for details.

## Limitations

- The supported CDDL subset covers practical attestation schemas (CoRIM, CoSWID);
  it does not implement the full RFC 8610 grammar.
- Indefinite-length CBOR items and the `undefined` simple value are not supported by
  the bundled encoder/decoder (decoding them raises `CBORUnsupportedError`).
- CBOR sequences (multiple concatenated items) are rejected by the decoder; a
  dedicated sequence API is not yet available.

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). While the
major version is `0`, a minor bump (`0.x.0`) may contain breaking changes and a
patch bump (`0.x.y`) is for backward-compatible fixes.

0.1.0 will be the first public release ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)). Until then the version is a
[PEP 440](https://peps.python.org/pep-0440/) development release, `0.1.0.devN`,
and changes are listed under *Unreleased* in the changelog.

- The version is defined once, as `__version__` in [`_version.py`](_version.py).
  `pyproject.toml` reads it, `simple_cbor.__version__` re-exports it, and
  `cddl-verify --version` prints it. Don't repeat the version number elsewhere.
- Each change merged to `main` bumps the version (`0.1.0.devN` → `devN+1` before
  the first release) and adds an entry to
  [CHANGELOG.md](CHANGELOG.md) (format: [Keep a Changelog](https://keepachangelog.com/)).
  Follow-up commits before the merge go into the same, still-unreleased version.
- Update this README and `docs/` in the same change as the code they describe.
