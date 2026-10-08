[![Python CI](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml/badge.svg)](https://github.com/sahebbiswas/cddl_verifier/actions/workflows/ci.yml)

# cddl-verifier

Validate CBOR (RFC 8949) data against CDDL (RFC 8610) schemas, render it as
annotated EDN (Extended Diagnostic Notation), and convert between CBOR and JSON.
It is built for attestation formats such as CoRIM and CoSWID.

> **Scope.** cddl-verifier supports a practical subset of CDDL and CBOR. It does
> **not** claim full RFC 8610 or RFC 8949 conformance yet; see
> [Limitations](#limitations) and [docs/CDDL_SUPPORT.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CDDL_SUPPORT.md).

| Surface | Name |
|---------|------|
| PyPI distribution | `cddl-verifier` |
| Python import package | `cddl_verifier` |
| Command-line tool | `cddl-verify` |

## Installation

```bash
pip install cddl-verifier
```

Requires Python 3.9 or newer. There are no runtime dependencies: CBOR encoding
and decoding are bundled. Extras for contributors: `pip install "cddl-verifier[test]"`
(pytest) and `[dev]` (pytest, build, twine).

## Quick start

### Command line

```bash
cddl-verify schema.cddl data.cbor --type corim-map                     # validate, print annotated EDN
cddl-verify schema.cddl data.cbor --type corim-map --output data.edn   # write EDN to a file
cddl-verify schema.cddl data.cbor --show-types                         # list the parsed CDDL types
```

The exit status is `1` when the CBOR cannot be decoded or, with `--type`, when
validation fails. Without `--type` the first rule is used and validation errors
are only warnings (exit status `0`).
`python -m cddl_verifier` takes the same arguments. See [docs/CLI.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CLI.md).

### Python

```python
from pathlib import Path
from cddl_verifier import validate

result = validate(Path("schema.cddl"), Path("data.cbor").read_bytes(),
                  root_type="corim-map")
if result.valid:
    print("valid")
else:
    for diagnostic in result.diagnostics:
        print(diagnostic.code, diagnostic.message)
```

- `schema` is CDDL text (`str`) or a path (`pathlib.Path`).
- `data` is CBOR bytes or an already-decoded Python value.
- `root_type` defaults to the first rule in the schema.
- CBOR that cannot be decoded gives an invalid result with a `"decode"`
  diagnostic and its byte offset; it does not raise.

To check many items against one schema, or to render EDN, use `Validator`:

```python
from cddl_verifier import Validator

validator = Validator(Path("schema.cddl"))
result = validator.validate(cbor_bytes, root_type="corim-map")
print(validator.to_edn(cbor_bytes, root_type="corim-map"))
```

CBOR and JSON helpers:

```python
from cddl_verifier.cbor import encode, decode
from cddl_verifier.json_codec import cbor_to_json

raw = encode({0: "Alice", 1: b"\x01"}, canonical=True)
assert decode(raw) == {0: "Alice", 1: b"\x01"}
print(cbor_to_json(raw, typed=True, pretty=True))
```

Decoding is strict: the input must be exactly one complete CBOR item. Empty,
truncated or trailing-byte input raises `CBORDecodeError` with the byte offset
of the problem. See [docs/API.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/API.md) for the full API.

## What is supported

- CDDL maps, arrays, type aliases, groups, type choices (`/=`), sockets (`//=`),
  optional fields, occurrence indicators, IANA-registered parameters
  (`&(name: n)`), CBOR tags (`#6.n(...)`), `.cbor`, `.size`, `.regexp` and
  value-range controls on integers. Details: [docs/CDDL_SUPPORT.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CDDL_SUPPORT.md).
- Real CoRIM and CoSWID schemas: [docs/CORIM_SUPPORT.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CORIM_SUPPORT.md).
- CBOR encoding and strict decoding with byte offsets in errors; duplicate map
  keys, invalid UTF-8 and excessive nesting are rejected.
- Annotated EDN output with field names from the schema.
- CBOR ↔ JSON conversion. `typed=True` keeps byte strings and tags, but
  non-string map keys (such as the integer keys in CoRIM maps) still become JSON
  strings, so maps with such keys do not round-trip exactly.

## Limitations

- **CDDL**: a practical subset, not the full RFC 8610 grammar. Malformed schemas
  are often accepted without an error. A standards-oriented parser is planned
  ([#69](https://github.com/sahebbiswas/cddl_verifier/issues/69),
  [#70](https://github.com/sahebbiswas/cddl_verifier/issues/70)).
- **`.size`**: counts characters instead of UTF-8 bytes on `tstr`, and is not
  enforced on `uint` ([#67](https://github.com/sahebbiswas/cddl_verifier/issues/67)).
- **Canonical encoding**: implements the RFC 8949 §4.2.1 core deterministic
  profile (see [CANONICAL_AND_JSON.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CANONICAL_AND_JSON.md)).
  The optional §4.2.2 reduction of integral floats to integers is not applied,
  and non-canonical mode still writes floats as 8-byte doubles.
- **CBOR**: indefinite-length items and `undefined` are not supported
  (`CBORUnsupportedError`). CBOR sequences are rejected; a sequence API is not
  available yet.
- **Value types**: tags decode to `(tag, value)` tuples and arrays or maps used
  as map keys decode to tuples. These are provisional and expected to change
  ([#78](https://github.com/sahebbiswas/cddl_verifier/issues/78),
  [#88](https://github.com/sahebbiswas/cddl_verifier/issues/88)).

## Documentation

| Document | Contents |
|----------|----------|
| [docs/CLI.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CLI.md) | `cddl-verify` and JSON converter options, input rules, EDN output formats |
| [docs/API.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/API.md) | Python API: `validate`, `Validator`, CBOR and JSON helpers |
| [docs/CDDL_SUPPORT.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CDDL_SUPPORT.md) | Supported CDDL syntax, types, and validation behaviour |
| [docs/CORIM_SUPPORT.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CORIM_SUPPORT.md) | CoRIM / CoSWID schema support |
| [docs/CBOR_BUILDER_QUICK_REF.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CBOR_BUILDER_QUICK_REF.md), [docs/ITERATIVE_CONSTRUCTION.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/ITERATIVE_CONSTRUCTION.md) | `CBOR` builder API in depth |
| [docs/CBOR_DIAGNOSTIC_DUMP.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CBOR_DIAGNOSTIC_DUMP.md) | Diagnostic dump format |
| [docs/CANONICAL_AND_JSON.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/docs/CANONICAL_AND_JSON.md) | Canonical encoding and JSON round-trips |
| [docs/](https://github.com/sahebbiswas/cddl_verifier/tree/main/docs) | EDN formatting, tag notation, and annotation notes |
| [TESTING.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/TESTING.md) | Running and writing tests, CI setup |
| [CHANGELOG.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/CHANGELOG.md) | Release history |

## Development

```bash
git clone https://github.com/sahebbiswas/cddl_verifier
cd cddl_verifier
pip install -e ".[test]"
pytest
```

The package source is in `src/cddl_verifier/`. Modules whose names start with
an underscore are internal. See [TESTING.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/TESTING.md).

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). While the
major version is `0`, a minor bump (`0.x.0`) may contain breaking changes and a
patch bump (`0.x.y`) is for backward-compatible fixes. The stable surface is the
public API above (`cddl_verifier`, `cddl_verifier.cbor`,
`cddl_verifier.json_codec`) and the `cddl-verify` CLI; underscore modules are not
covered.

- The version is defined once, as `__version__` in
  [`src/cddl_verifier/_version.py`](https://github.com/sahebbiswas/cddl_verifier/blob/main/src/cddl_verifier/_version.py).
  `pyproject.toml` reads it, `cddl_verifier.__version__` exposes it, and
  `cddl-verify --version` prints it. Don't repeat the version number elsewhere.
- A change that alters behaviour bumps the version and adds an entry to
  [CHANGELOG.md](https://github.com/sahebbiswas/cddl_verifier/blob/main/CHANGELOG.md) (format: [Keep a Changelog](https://keepachangelog.com/)).
  Follow-up commits before a release go into the same, still-unreleased version.
- Update this README and `docs/` in the same change as the code they describe.

## Releasing

Releases go to PyPI through [`publish_pypi.yml`](https://github.com/sahebbiswas/cddl_verifier/blob/main/.github/workflows/publish_pypi.yml),
which uses PyPI Trusted Publishing (no API tokens are stored).

1. Set the version in `_version.py`, date its CHANGELOG section, and merge to `main`.
2. Optional dry run: start the workflow manually (Actions → *Publish to PyPI /
   TestPyPI* → *Run workflow*); manual runs only upload to TestPyPI. A version can
   only be uploaded once per index, so use a pre-release such as `0.1.1rc1` for
   trial uploads. A release fails if its version is already on TestPyPI.
3. Publish a GitHub release with tag `vX.Y.Z` matching the version. The workflow
   builds and checks the distributions, tests the wheel and sdist, uploads to
   TestPyPI, installs from there, and then uploads to PyPI.

One-time setup: on pypi.org and test.pypi.org, add a (pending) trusted publisher
for repository `sahebbiswas/cddl_verifier`, workflow `publish_pypi.yml`, and
environment `pypi` / `testpypi` respectively (or "any" environment). GitHub
creates the environments on first use; add a required reviewer to `pypi` to gate
production uploads.
