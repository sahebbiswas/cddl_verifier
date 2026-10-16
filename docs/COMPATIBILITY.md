# Compatibility and stability

From 1.0.0 the public API follows [Semantic Versioning 2.0.0](https://semver.org/):
a release that keeps the major version does not break code that uses the public API as
this documentation describes it. This page says what that covers, what it does not, and
how things are removed.

## What is public

| Surface | Covered |
|---------|---------|
| `cddl_verifier` | `validate`, `Validator`, `ValidationResult`, `Diagnostic`, `render_text`, `render_json`, `schema_diagnostics`, `SchemaError`, `CBORDecodeError`, `__version__` |
| `cddl_verifier.cbor` | everything in its `__all__`: `encode`, `decode`, `decode_sequence`, `diag_dump`, `CBOR`, `CBORKey`, `CBORTag`, `CBORSimple`, `UNDEFINED`, `float_width`, `head_info`, the decode errors and the `cbor_*` aliases |
| `cddl_verifier.json_codec` | `cbor_to_json`, `json_to_cbor`, the two file helpers, `CBORJSONEncoder` and the `python -m cddl_verifier.json_codec` command |
| `cddl-verify` | the command and `python -m cddl_verifier`: options, exit statuses, and what is written to stdout, stderr and `--output` |

Names, parameters (and their defaults), return types and exceptions of these are
stable. Modules whose names start with an underscore (`_analyzer`, `_schema`, `_edn`,
`_validator`, `_cddl`, ...) are internal, and so are the classes `CDDLParser`,
`CBORAnalyzer` and `EDNGenerator`, `CBORLocations` and the `*_with_locations` methods
of `CBOR`. They can change in any release.

## What is guaranteed, and what is not

Stable within a major version:

- **Verdicts.** Whether a schema is accepted and whether data is valid does not change
  for a schema and data that were handled correctly. A bug fix that makes the library
  agree with RFC 8610, RFC 8949 or the other RFCs it implements is a patch or minor
  release, even when input that was wrongly accepted or rejected before now gets the
  right answer. Each such change is called out in the release notes under
  **Behaviour change**.
- **Machine-readable output.** The `--diagnostics json` document and
  `ValidationResult.to_json()` (`format_version: 1`, the field names and their types),
  the `Diagnostic` fields, and the `code` and `category` values. New fields and new
  `code` values can be added; a field is not renamed, retyped or removed within
  `format_version` 1.
- **Typed JSON.** The `$cbor` annotation format of `cbor_to_json(typed=True)` and
  `json_to_cbor`: documents written by one 1.x release are read by every later 1.x
  release. A future incompatible form would use a new key beside `$cbor`.
- **Encoding.** `encode` writes the same bytes for the same value (shortest-form heads
  and floats, definite lengths; `canonical=True` is RFC 8949 section 4.2.1).
- **Exit statuses** of `cddl-verify`: `0` success, `1` failure.

Not covered (they can change in a minor or patch release):

- The **wording of messages** (`Diagnostic.message`, error text, `render_text`). Match on
  `code`, `path` and the other fields, not on the text.
- The **EDN text** (layout, annotation placement, comment style) and the **diagnostic
  dump** layout. They are for reading; the data they show is not affected.
- The order of diagnostics for the same problem set is deterministic for a given
  release, but is not promised across releases.
- Performance, and the limits that guard against hostile input (nesting depth, work
  budgets): they can be tuned. Input that was accepted near a limit may be refused if a
  limit is lowered for safety; the release notes say so.
- Anything marked internal above, and behaviour the documentation does not describe.

## Deprecation

A public name, parameter or option is not removed without notice. It is deprecated in a
minor release first (a `DeprecationWarning` for the Python API, a note on stderr for the
CLI, and a line in the docs and the release notes) and removed in the next major release
at the earliest.

## Python versions

The supported versions are the ones tested in CI and named in `pyproject.toml`
(3.9 to 3.13 at 1.0.0). Support for a version is dropped in a minor release once the
Python project no longer supports it, and the release notes say so. There are no runtime
dependencies, and adding one is a minor release at least.

## Versions and releases

Every change to `main` bumps the version (see [CONTRIBUTING.md](../CONTRIBUTING.md)):
patch for fixes, minor for new features and options, major for an incompatible change to
the public API. Not every version is published to PyPI. The history of a release is in
its [release notes](https://github.com/sahebbiswas/cddl_verifier/releases), which start
with the **Behaviour change** items.
