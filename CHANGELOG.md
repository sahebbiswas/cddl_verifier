# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is defined once in `_version.py`.

## [0.2.0] - 2026-10-08

### Changed
- **Breaking:** `CBOR.load()`, `CBOR.loads()`, `cbor_decode()` and
  `load_cbor_bytes()` now decode exactly one complete CBOR data item. Trailing
  bytes after the item (including concatenated CBOR sequences), which were
  previously ignored silently, are rejected with `CBORTrailingDataError`
  (#64). The analyzer CLI and nested `.cbor` validation inherit this behaviour.
- Decoder errors are now `CBORDecodeError` (a `ValueError` subclass) carrying the
  byte `offset` of the problem: `CBORTruncatedError` for empty or truncated
  input, `CBORTrailingDataError` for trailing bytes, `CBORUnsupportedError`
  (also a `NotImplementedError`) for indefinite-length items and `undefined`.
  Invalid UTF-8, reserved additional-info values, duplicate map keys and
  excessive nesting also raise `CBORDecodeError` instead of leaking
  `UnicodeDecodeError` / `RecursionError`.
- Decoding accepts `bytearray` and `memoryview`; non-bytes input raises
  `TypeError`.

### Added
- Versioning scheme: `_version.py` as the single version source, this
  changelog, `[project]` metadata in `pyproject.toml` (with a
  `cbor-cddl-analyzer` console script), and `cbor_cddl_analyzer.py --version`.
- `tests/test_strict_decoding.py`: regression tests for scalars, maps, arrays,
  tags and nested data with trailing bytes, truncation at every prefix, and
  error offsets.

## [0.1.0] - 2026-10-07

### Added
- Baseline release prior to the versioning scheme: CDDL parser and CBOR
  validator with annotated EDN output, `simple_cbor` encoder/decoder/diagnostic
  dumper/builder, and `cbor_json` conversion.
