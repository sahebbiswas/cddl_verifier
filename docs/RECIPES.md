# Recipes: common tasks

These end-to-end examples use the public API and CLI shipped by
`cddl-verifier`. The project supports a practical subset of CDDL/CBOR; see
[CDDL support and limitations](CDDL_SUPPORT.md) before relying on it for a
protocol's full conformance requirements.

## Choose your task

| I want to… | Start here |
|---|---|
| Install the package and check the CLI | [Install and verify](#1-install-and-verify) |
| Validate one CBOR item against a named CDDL rule | [Validate a file](#2-validate-a-cbor-file) |
| Validate data in an application | [Validate from Python](#3-validate-from-python) |
| Understand why a value failed | [Diagnose a failure](#4-diagnose-a-failure) |
| Convert CBOR and JSON without losing CBOR types | [Convert JSON and CBOR](#5-convert-json-and-cbor) |
| Inspect CoRIM / CoSWID data | [Work with attestation data](#6-inspect-corim-or-coswid-data) |
| Fail a CI job when validation fails | [Use it in CI](#7-use-it-in-ci) |

For complete options and API contracts, see the [CLI reference](CLI.md),
[Python API](API.md), [CDDL support matrix](CDDL_SUPPORT.md), and
[CoRIM / CoSWID guide](CORIM_SUPPORT.md).

## 1. Install and verify

**Prerequisite:** Python 3.9 or newer.

Use a virtual environment to keep the installation isolated:

```bash
python -m venv .venv
# Linux / macOS:
. .venv/bin/activate
# Windows PowerShell:
# .venv\\Scripts\\Activate.ps1

python -m pip install --upgrade pip
python -m pip install cddl-verifier
cddl-verify --version
```

The final command prints the installed version. The package has no runtime
dependencies. To install test tools for development, use
`python -m pip install "cddl-verifier[test]"`; the `[dev]` extra also installs
build and Twine tools.

If `cddl-verify` is not found, make sure the virtual environment is activated
and that installation completed successfully. You can invoke the same validator
through Python instead:

```bash
python -m cddl_verifier --help
```

Next: [CLI options](CLI.md).

## 2. Validate a CBOR file

**Prerequisite:** the installed package and a schema/data file pair. This
self-contained example creates a small schema and two CBOR fixtures using the
package's public encoder.

Create `person.cddl`:

```cddl
person = {
  &(name: 0) => tstr,
  &(age: 1) => uint
}
```

Create `person.cbor` and `person-invalid.cbor`:

```bash
python - <<'PY'
from pathlib import Path
from cddl_verifier.cbor import encode

Path("person.cbor").write_bytes(encode({0: "Alice", 1: 30}))
Path("person-invalid.cbor").write_bytes(encode({0: "Alice", 1: "thirty"}))
PY
```

Validate against the named root rule:

```bash
cddl-verify person.cddl person.cbor --type person
```

The command exits with status `0` for valid input and prints annotated EDN.
The exact formatting depends on the selected EDN format; the output identifies
the `person` rule and its `name` and `age` fields.

Now validate the bad fixture:

```bash
cddl-verify person.cddl person-invalid.cbor --type person
echo $?
```

The validation reports a type mismatch for `age` and exits with status `1`.
In scripts, prefer `--type person`: without `--type`, the first rule is
selected automatically, validation failures are warnings, and the process
currently exits successfully. Explicit root selection makes automation's
intent and failure behavior clear. The input must contain exactly one complete
CBOR item; trailing bytes and CBOR sequences are rejected.

Next: [CLI input and exit-status details](CLI.md#input-requirements).

## 3. Validate from Python

**Prerequisite:** `pip install cddl-verifier`, plus the schema and CBOR file
from the previous recipe.

```python
from pathlib import Path
from cddl_verifier import Validator, validate

schema = Path("person.cddl")
data = Path("person.cbor").read_bytes()

result = validate(schema, data, root_type="person")
if result.valid:
    print("valid")
else:
    for diagnostic in result.diagnostics:
        print(f"{diagnostic.code}: {diagnostic.message}")
```

For multiple inputs, parse the schema once with `Validator`:

```python
validator = Validator(schema)
for path in (Path("person.cbor"), Path("person-invalid.cbor")):
    result = validator.validate(path.read_bytes(), root_type="person")
    print(path, "valid" if result.valid else "invalid")
    for diagnostic in result.diagnostics:
        print(f"  {diagnostic.code}: {diagnostic.message}")
```

Invalid CBOR bytes return an invalid result with a `decode` diagnostic and
byte offset; ordinary schema errors (unreadable, malformed, or semantically
invalid CDDL) raise `SchemaError` when constructing the validator. See the
[API reference](API.md#cddl_verifier-validation) for result fields and exception
details.

Next: [Python API reference](API.md).

## 4. Diagnose a failure

**Prerequisite:** the `person.cddl` and `person-invalid.cbor` fixtures created in
[Recipe 2](#2-validate-a-cbor-file). Run that recipe first, or substitute your
own schema and CBOR input paths.

Start by asking the CLI to write annotated EDN to a file:

```bash
cddl-verify person.cddl person-invalid.cbor --type person --output person.edn
```

A validation failure still exits with status `1`; inspect the diagnostic on
stderr and the generated EDN when present to see the decoded structure and
schema field annotations. For byte-level inspection independent of the schema,
use the diagnostic dump API:

```bash
python - <<'PY'
from pathlib import Path
from cddl_verifier.cbor import diag_dump

print(diag_dump(Path("person-invalid.cbor").read_bytes()))
PY
```

A decode diagnostic points to a CBOR byte offset; a validation diagnostic
describes a mismatch between decoded data and the selected CDDL rule. The
diagnostic dump is a debugging view, not validation. Structured diagnostic
improvements are tracked in [#75](https://github.com/sahebbiswas/cddl_verifier/issues/75);
use only the fields and output available in your installed version.

Next: [CLI EDN formats](CLI.md#edn-output-formats) and
[CBOR diagnostic dump](CBOR_DIAGNOSTIC_DUMP.md).

## 5. Convert JSON and CBOR

**Prerequisite:** installed package and JSON input. Convert JSON to deterministic
CBOR, then inspect the result as typed JSON:

```bash
python -m cddl_verifier.json_codec to-cbor input.json output.cbor --canonical
python -m cddl_verifier.json_codec to-json output.cbor roundtrip.json --pretty --typed
```

Use `--typed` when converting CBOR to JSON for a round trip. JSON objects only
have string keys and JSON has no native byte-string or CBOR-tag type. Typed
annotations preserve byte strings, tags, and non-string map keys; without them,
conversion can lose CBOR-specific types. For example, typed output represents a
byte string with a `{"$cbor":"bytes","$value":"..."}` annotation and a map with
non-string keys as typed key/value pairs.

Next: [Canonical encoding and JSON conversion](CANONICAL_AND_JSON.md), including
the full annotation format and limitations.

## 6. Inspect CoRIM or CoSWID data

**Prerequisite:** a schema file for the format and a CBOR file containing one
complete item. CoRIM and CoSWID schemas are not automatically downloaded by the
validator; obtain the appropriate schema and confirm the expected root rule.

```bash
cddl-verify corim.cddl corim.cbor --show-types
cddl-verify corim.cddl corim.cbor --type corim-map --output corim.edn
```

The root type must match the representation in the file. For example, a CoRIM
wrapped in CBOR tag 501 may need a tagged root such as
`tagged-unsigned-corim-map` rather than the untagged `corim-map`. CoRIM/CoSWID
support is partial and does not imply full CDDL or protocol-semantic
conformance; consult the support guide before using results as a security
decision.

Next: [CoRIM / CoSWID support and limitations](CORIM_SUPPORT.md) and
[CDDL support matrix](CDDL_SUPPORT.md).

## 7. Use it in CI

Install the package in the job, then make the root explicit and preserve output
for debugging. This shell pattern fails the job if validation fails:

```bash
python -m pip install cddl-verifier
cddl-verify person.cddl person.cbor --type person --output person.edn \
  >validation.stdout 2>validation.stderr || {
    status=$?
    cat validation.stderr
    cat validation.stdout
    echo "cddl-verify failed with status $status" >&2
    exit "$status"
  }
```

Keep the schema and CBOR fixture as version-controlled test inputs. Configure
your CI system to upload `validation.stdout` and `validation.stderr` as artifacts
when the job fails. The `person.edn` output file is not created when explicit
`--type` validation fails. Do not omit `--type` in
automation if you need validation failures to make the command fail.

Next: [CLI exit status](CLI.md) and [testing guide](../TESTING.md).
