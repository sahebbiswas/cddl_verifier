# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is defined once in `_version.py`. Until 0.1.0 (the first public
release, [#84](https://github.com/sahebbiswas/cddl_verifier/issues/84)) is published, the version is `0.1.0.dev0`.

## [Unreleased] - 0.1.0

### Changed
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

### Added
- Versioning scheme: `_version.py` as the single version source, this
  changelog, `[project]` metadata in `pyproject.toml`, and a `--version` flag.
- `pip install .` installs a `cddl-verify` console script (same as
  `python cbor_cddl_analyzer.py`).
- `docs/CLI.md`, `docs/API.md` and `docs/CDDL_SUPPORT.md` reference pages; the
  README is now a short overview that links to them. EDN and diagnostic-dump
  samples were regenerated from real output.
- `tests/test_strict_decoding.py`: regression tests for scalars, maps, arrays,
  tags and nested data with trailing bytes, truncation at every prefix, and
  error offsets.
