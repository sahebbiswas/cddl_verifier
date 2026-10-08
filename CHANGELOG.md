# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is defined once in `src/cddl_verifier/_version.py`. 0.1.0 is the first
public release ([#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)); the section below gets its date when the release is published.

## [Unreleased] - 0.1.0

### Changed
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
  limitations (`.size` [#67](https://github.com/sahebbiswas/cddl_verifier/issues/67),
  canonical floats [#66](https://github.com/sahebbiswas/cddl_verifier/issues/66)) and release steps.
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

### Removed
- `fix_indent_proper.py`, a one-off script that edited `cbor_cddl_analyzer.py`
  in place and no longer applies to the package layout.
