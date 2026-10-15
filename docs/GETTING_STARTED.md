# Getting started

This guide takes you from `pip install` to reading a failed validation. You
write a schema, make some CBOR, validate it, break it in the common ways, and
learn to find the cause of each failure from the report. Every command and
every piece of output below was produced by running it; a test
(`tests/test_getting_started.py`) runs the guide's files and commands and fails
when the output no longer matches.

- [1. Install](#1-install)
- [2. A schema and some data](#2-a-schema-and-some-data)
- [3. Validate](#3-validate)
- [4. Read a failure](#4-read-a-failure)
- [5. Triage flows](#5-triage-flows)
- [6. The same from Python](#6-the-same-from-python)
- [7. Use it in CI](#7-use-it-in-ci)
- [8. Where to go next](#8-where-to-go-next)

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install cddl-verifier
cddl-verify --version
```

Python 3.9 or newer; there are no runtime dependencies. `python -m cddl_verifier`
is the same tool as `cddl-verify`.

## 2. A schema and some data

Make a working directory and write this schema. It describes a device report: a
name, a four byte firmware version, a status, and a few optional members.

<!-- file: device.cddl -->
```cddl
; A device report: who it is, what it runs, how it is doing.
device-report = {
  ; short device name
  name: tstr .size (1..32),
  ; firmware version, 4 bytes
  firmware: bstr .size 4,
  status: status,
  ; seconds since boot
  ? uptime: uint,
  ? labels: [* label],
  ; time of the report, as a tag 1 epoch time
  ? issued: #6.1(uint),
}

status = &(ok: 0, degraded: 1, failed: 2)
label = tstr .regexp "[a-z][a-z0-9-]*"
```

CBOR is binary, so you need a tool to write it. The JSON converter does that: JSON has no
byte strings or tags, so they are written as small annotated objects
(`"$cbor": "bytes"` holds base64, `"$cbor": "tag"` holds a tag number and a value).

<!-- file: good.json -->
```json
{
  "name": "edge-01",
  "firmware": {"$cbor": "bytes", "$value": "AQIDBA=="},
  "status": 0,
  "uptime": 86400,
  "labels": ["lab", "rack-7"],
  "issued": {"$cbor": "tag", "$tag": 1, "$value": 1760000000}
}
```

```bash
python -m cddl_verifier.json_codec to-cbor good.json good.cbor
```

```text
[OK] Converted good.json to good.cbor (80 bytes)
```

(Real data comes from your device or service; from Python, `cddl_verifier.cbor.encode`
does the same. See [API.md](API.md#cddl_verifiercbor-encoding-and-decoding).)

### Broken copies

To try the failures below you need a few damaged copies of the data. This script writes them (it
is plain Python; the `$cbor` annotations are the same ones the converter reads):

<!-- file: make_cases.py -->
```python
import json
from pathlib import Path
from cddl_verifier.json_codec import json_to_cbor

good = json.loads(Path("good.json").read_text())

def bytes_(b64):
    return {"$cbor": "bytes", "$value": b64}

def variant(case, **changes):
    data = {**good, **changes}
    data = {k: v for k, v in data.items() if v is not None}     # None removes a member
    Path(f"{case}.cbor").write_bytes(json_to_cbor(json.dumps(data)))

variant("wrongtype", name=7)                                   # a number for the name
variant("missing", firmware=None)                              # a required member is absent
variant("unknown", colour="red")                               # a key the schema does not list
variant("toolong", name="x" * 40)                              # over .size
variant("badlabel", labels=["lab", "Rack 7"])                  # breaks the .regexp
variant("badstatus", status=3)                                 # not in the enumeration
variant("badfw", firmware=bytes_("AQID"))                      # 3 bytes, not 4
variant("wrongtag", issued={"$cbor": "tag", "$tag": 0, "$value": "2025-01-01T00:00:00Z"})
variant("notag", issued=1760000000)                            # the number, without tag 1
variant("multi", name=7, firmware=bytes_("AQID"), labels=["ok", "No"])   # three problems

good_bytes = Path("good.cbor").read_bytes()
Path("trunc.cbor").write_bytes(good_bytes[:20])                # cut short
Path("trailing.cbor").write_bytes(good_bytes + b"\x00")        # a byte too many
```

```bash
python make_cases.py
```

## 3. Validate

```bash
cddl-verify device.cddl good.cbor --type device-report
echo "exit status: $?"
```

```text
Loading CDDL schema: device.cddl
Loading CBOR data: good.cbor
Validating CBOR against CDDL (type: device-report)...
[OK] Validation successful
Generating EDN...

==================================================
EDN Output:
==================================================
/ device-report / {
  "name": "edge-01",
  "firmware": h'01020304',
  "status": 0,
  "uptime": 86400,
  "labels": [
    "lab",
    "rack-7"
  ],
  "issued": / #6.1(uint) / 1(1760000000)
}
exit status: 0
```

Validation passed, and the tool printed the data as annotated EDN (the readable form of CBOR, RFC 8949
section 8): the `/ ... /` parts are comments that name the rule an item was checked against.
`-o report.edn` writes the EDN to a file; `--no-annotate` drops the comments; `--edn-format` chooses how
keys are written (see [CLI.md](CLI.md#edn-output-formats)).

Two things to know before you rely on the exit status:

- **Always pass `--type`.** With `--type`, a validation failure exits with status `1`. Without it the first rule of the
  schema is used as a guess, failures are only warnings, the exit status is `0` and EDN is still printed. That is for
  exploring a file, not for checking it.
- **Exit status `1`** also means the schema could not be loaded, the file is missing, or the CBOR could not be
  decoded. Which of the three it was is in the message; section 5 tells them apart.

## 4. Read a failure

`wrongtype.cbor` is the data with the name changed from text to the number 7. Validate it:

```bash
cddl-verify device.cddl wrongtype.cbor --type device-report
```

```text
[FAIL] Validation failed:
  - Type mismatch for field 'name' in 'device-report': expected tstr .size (1..32), got uint 7
```

(The terminal also shows the same line as a coloured `[ERROR]` log. The `[FAIL]` block is the
summary.) The message says what was wrong and where. For anything more, or for a script,
ask for the report as JSON:

```bash
cddl-verify device.cddl wrongtype.cbor --type device-report --diagnostics json
```

```json
{
  "format_version": 1,
  "valid": false,
  "root_type": "device-report",
  "diagnostics": [
    {
      "severity": "error",
      "category": "type",
      "code": "validation",
      "message": "Type mismatch for field 'name' in 'device-report': expected tstr .size (1..32), got uint 7",
      "path": ["name"],
      "path_expr": "$.name",
      "offset": 6,
      "end_offset": 7,
      "hex": "07",
      "hex_truncated": false,
      "rule": "device-report",
      "schema_path": ["device-report", "name"],
      "schema_location": "device.cddl:2:1",
      "expected": "tstr .size (1..32)",
      "actual": "uint 7",
      "alternatives": [],
      "line": null,
      "column": null
    }
  ]
}
```

(Arrays are shortened here; the real output has one value per line.) With `--diagnostics json`, **stdout is exactly this
document and nothing else**; progress and log lines go to stderr.

The fields you will use most:

| Field | What it tells you |
|-------|-------------------|
| `category` | The kind of problem: `decode`, `schema`, `tag`, `type`, `value`, `missing`, `unknown`, `occurrence`, `nested` or `other`. Triage starts here (section 5). |
| `path_expr` | Where in the data: `$` is the root, `.name` a text key, `[2]` an array index, `<key 1>` an integer key. |
| `offset`, `end_offset`, `hex` | Where in the bytes: the item starts at byte `offset` (decimal), and `hex` is its first 16 bytes. |
| `expected`, `actual` | What the schema wanted and what the data had. |
| `rule`, `schema_path`, `schema_location` | Which rule of the schema was being checked, the route to the member, and the file, line and column of the rule. |

Validation does not stop at the first failure. One run lists every problem it found, in the order the
schema lists the members, and the order is the same on every run. Full field list:
[API.md](API.md#diagnostic).

## 5. Triage flows

### 5.1 Which kind of failure is it?

Start with the first line of the output.

| You see | The problem is in | Go to |
|---------|-------------------|-------|
| `Error decoding CBOR: ...` | the CBOR file (it is not one well-formed item) | [5.2](#52-the-cbor-cannot-be-decoded) |
| `Error loading CDDL file: ...` | the schema | [5.3](#53-the-schema-cannot-be-loaded) |
| `[FAIL] Validation failed:` | the data does not match the schema, or the schema is wrong about the data | [5.4](#54-the-data-does-not-match) |
| `[OK] Validation successful` but you expected a failure | the question you asked | [5.7](#57-it-passes-but-should-not-or-fails-but-should-not) |

In JSON the same split is `code`: `decode`, `schema` or `validation`.

### 5.2 The CBOR cannot be decoded

The tool decodes before it validates, so nothing is checked until the bytes are well-formed. The message gives the
byte offset of the problem.

```text
Error decoding CBOR: Unexpected end of data: need 8 byte(s), 5 available (at offset 15)
```

That file is `good.cbor` cut off after 20 bytes: a text string at offset 15 announces 8 bytes
and only 5 are left. The usual causes:

| Message | Cause | Fix |
|---------|-------|-----|
| `Unexpected end of data ... (at offset N)` | The file is truncated. | Re-transfer or re-export it; check how it was written (text mode, a size limit). |
| `Trailing data after CBOR item: N extra byte(s) (at offset N)` | More than one item, or junk after it. A CBOR *sequence* is not one item. | Cut the file at the offset, or read it with `decode_sequence` from Python. |
| `Not deterministically encoded: ...` | You passed `--require-deterministic` and the encoding is valid but not the one prescribed (RFC 8949 section 4.2.1). | Re-encode with `canonical=True` (`python -m cddl_verifier.json_codec to-cbor ... --canonical`). |
| Invalid UTF-8, duplicate map key, nesting too deep | The data is rejected on purpose. | Fix the producer. |

The deterministic check, on the file from section 2 (JSON order is not key order):

```bash
cddl-verify device.cddl good.cbor --type device-report --require-deterministic
```

```text
Error decoding CBOR: Not deterministically encoded: map keys are not in bytewise order of their encodings (0x66737461747573 comes after 0x686669726d77617265) (at offset 28)
```

Encoded with `--canonical`, the same data passes.

### 5.3 The schema cannot be loaded

Schemas are checked before any data is. Every problem is listed, with line and column:

```text
Error loading CDDL file:
  - broken.cddl:3:11: undefined name 'statuss'
  - broken.cddl:4:25: the argument of '.size' must be an unsigned integer, not a text string
```

`broken.cddl` is the schema of section 2 with a misspelled name and a text string as a `.size`. Fix them in the
order listed. In JSON they are `code: "schema"` diagnostics with `line`, `column` and `rule`, so a script never has
to parse stderr.

When the schema loads but does not mean what you intended, print what was understood:

```bash
cddl-verify device.cddl good.cbor --show-types
```

```text
device-report (map):
  name: name -> tstr .size (1..32)
  firmware: firmware -> bstr .size 4
  status: status -> status
  uptime: uptime -> uint (optional)
  labels: labels -> [ * label ] (optional)
  issued: issued -> #6.1(uint) (optional)
```

Check that each member has the key, type and occurrence you meant (`(optional)` is `?`).

### 5.4 The data does not match

Read `category`, then follow the row. Each example below is the schema of section 2 with one change to the data.

| `category` | Message (shortened) | Meaning | What to check |
|------------|---------------------|---------|---------------|
| `type` | `Type mismatch for field 'name' ...: expected tstr .size (1..32), got uint 7` | The item is not of the type. | The producer: wrong field, wrong encoding (a number for a string, a text string for a byte string). |
| `type` | `Field 'status' ... matches none of 0 / 1 / 2 (0: expected 0, got 3; ...)` | A choice or enumeration with no match. | Every alternative is listed with why it failed; either the value or the schema's list is out of date. |
| `value` | `Field 'name' ... violates .size: expected at most 32 UTF-8 bytes, got 40` | Right type, value outside a control (`.size`, `.regexp`, `.ge`, ...). | The limit in the schema, then the data. `.size` of text counts UTF-8 bytes, not characters. |
| `value` | `Element [1] of field 'labels' 'Rack 7' does not match .regexp /[a-z][a-z0-9-]*/` | A text string does not match the pattern. | The pattern matches the whole text, in XSD syntax; there is no `^` or `$`. |
| `missing` | `Missing required field 'firmware' (key firmware) in type 'device-report'` | A member without `?` is absent. | The data, or make the member optional if that is the intent. |
| `unknown` | `Unknown field 'colour' not in schema for type 'device-report'` | The map has a key the schema does not list. | A map is closed unless the schema says otherwise. Add the member, or allow extras with `* tstr => any` ([below](#allowing-extra-keys)). |
| `occurrence` | `Array type 'r@labels' has unexpected element [2]: expected at most 2 element(s), got 3` | Too many or too few array elements or map entries. | The occurrence (`?`, `*`, `+`, `n*m`) in the schema. |
| `tag` | `Field 'issued' ... requires CBOR tag 1, got tag 0` or `... but the data is not tagged` | A missing or different tag number. | Which tag the producer writes. A tag is not a JSON-style annotation: `1(1760000000)` and `1760000000` are different items. |
| `nested` | `Failed to decode nested CBOR in field 'p': ...` | A byte string that should hold CBOR (`bstr .cbor T`) does not decode. | The producer of the inner bytes. Problems inside an inner item that did decode are reported at their own path (`$.p.id`) with their own category. |
| `other` | Anything else | | Read the message. |

**Many failures at once.** Fix them in the order listed. Several often have one cause (a missing member
makes a later check fail). This data has three unrelated problems and all three are reported:

```text
[FAIL] Validation failed:
  - Type mismatch for field 'name' in 'device-report': expected tstr .size (1..32), got uint 7
  - Field 'firmware' in 'device-report' violates .size: expected exactly 4 bytes, got 3
  - Element [1] of field 'labels' 'No' does not match .regexp /[a-z][a-z0-9-]*/
```

#### Allowing extra keys

A wildcard member accepts keys the schema does not name. With it, `colour` is no longer an error:

```cddl
device-report = {
  name: tstr,
  * tstr => any,
}
```

#### Is it the data or the schema?

When the message is surprising, ask which side is wrong. `expected` and `actual` give both: if `actual` is what the
producer really sends and `expected` is not what you meant, the schema is wrong; read the rule with
`schema_location`. If `expected` is right, the producer is.

### 5.5 Find the bytes

`offset` is a decimal byte position in the file. The diagnostic dump prints every item with its offset in
*hex* in the left column, so `offset` 35 is line `0023`:

```bash
python -c "import sys; from cddl_verifier.cbor import diag_dump; print(diag_dump(open(sys.argv[1], 'rb').read()))" badstatus.cbor
```

```text
0001:   64                              # key: text(4)
0002:     6e61 6d65                     # "name"
...
001c:   66                              # key: text(6)
001d:     7374 6174 7573                # "status"
0023:   03                              # val: uint(3)
```

`hex` in the diagnostic already holds the first 16 bytes of the item (`03` here), which is usually enough to tell what the
producer wrote.

### 5.6 Many reports, or a script

Group by category and path to see what kind of failures there are before fixing any:

```python
from collections import Counter
from pathlib import Path
from cddl_verifier import Validator

validator = Validator(Path("device.cddl"))          # load the schema once

for name in ["good", "multi", "missing", "trunc"]:
    result = validator.validate(Path(f"{name}.cbor").read_bytes(), root_type="device-report")
    print(f"{name}: valid={result.valid}", dict(Counter(d.category for d in result.diagnostics)))
    for d in result.diagnostics:
        print(f"  [{d.category}] {d.path_expr} @ {d.offset}: expected {d.expected!r}, got {d.actual!r}")
```

```text
good: valid=True {}
multi: valid=False {'type': 1, 'value': 2}
  [type] $.name @ 6: expected 'tstr .size (1..32)', got 'uint 7'
  [value] $.firmware @ 16: expected 'exactly 4 bytes', got '3'
  [value] $.labels[1] @ 51: expected 'text matching .regexp /[a-z][a-z0-9-]*/', got '"No"'
missing: valid=False {'missing': 1}
  [missing] $ @ 0: expected "member 'firmware' (key firmware)", got 'absent'
trunc: valid=False {'decode': 1}
  [decode] $ @ 15: expected None, got None
```

A file that cannot be decoded does not raise: it is an invalid result with one `decode` diagnostic.

### 5.7 It passes but should not, or fails but should not

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Exit status `0` and a `[WARNING]` about "inferred type" | No `--type`: the first rule was guessed and its failures are warnings. | Pass `--type NAME`. |
| A rule you did not expect is the root | `validate` and the CLI default to the *first* rule of the file, which may be a generic or a helper. | Pass the root type explicitly: `root_type="device-report"`. |
| A `Warning:` line about a module that was not found, and names from it match anything | The schema imports a module (`;# import NAME as P`) that was not loaded. | Give the file with `-M DIR` or `--module NAME=FILE`; see [CLI.md](CLI.md#imported-modules). |
| Data with extra keys is rejected | Maps are closed. | Section 5.4, [Allowing extra keys](#allowing-extra-keys). |
| Data matching two alternatives of a choice is accepted | That is allowed by default. | `--unique-choices` makes it an error. |
| The message does not say why | | `--verbose` prints every step of the check to stderr, including which rule and field each line is about. Start from the line with `Type mismatch` or `.size` and read upward. |

```bash
cddl-verify device.cddl wrongtype.cbor --type device-report --verbose 2>&1 | grep -v "Added built-in"
```

```text
[DEBUG] [/device-report] Validating type 'device-report'
[DEBUG]   Expected: map, Got: dict
[DEBUG] [/device-report] Checking 6 field definitions
[DEBUG] [/device-report/name] [OK] Field present: key=name, type=tstr .size (1..32), value=7 [@0006:07 68 66 69]
...
[DEBUG] [/device-report/name] Type mismatch: expected tstr, got int
```

## 6. The same from Python

```python
from pathlib import Path
from cddl_verifier import validate, Validator

# One call: schema (text or Path), data (bytes or an already decoded value)
result = validate(Path("device.cddl"), Path("wrongtype.cbor").read_bytes(), root_type="device-report")
print(result.valid)
for d in result.diagnostics:
    print(d.code, d.category, d.path_expr, d.message)

# Many items against one schema, and EDN
validator = Validator(Path("device.cddl"))
print(validator.validate(Path("good.cbor").read_bytes(), root_type="device-report").valid)
print(validator.to_edn(Path("good.cbor").read_bytes(), root_type="device-report")[:40])
```

```text
False
validation type $.name Type mismatch for field 'name' in 'device-report': expected tstr .size (1..32), got uint 7
True
/ device-report / {
  "name": "edge-01",
```

A schema that cannot be loaded raises `SchemaError`; `schema_diagnostics(text)` returns its problems
as a list instead. Rendering: `result.to_json()` is the document of section 4, and
`render_text(result.diagnostics, verbose=True)` is the text form. See [API.md](API.md).

## 7. Use it in CI

The exit status is the contract: `0` valid, `1` anything else. Keep the report as a build artifact:

```bash
cddl-verify device.cddl good.cbor --type device-report --diagnostics json -o /dev/null > report.json
echo "exit status: $?"
python -c "import json; d = json.load(open('report.json')); print(d['valid'], len(d['diagnostics']))"
```

```text
exit status: 0
True 0
```

(`-o /dev/null` discards the EDN, `-o NUL` on Windows; without `-o` nothing but the report is written to stdout.)
For signed or hashed payloads add `--require-deterministic`. For recipes such as
checking a directory of files, see [RECIPES.md](RECIPES.md).

## 8. Where to go next

| You want | Read |
|----------|------|
| Every option and exit status | [CLI.md](CLI.md) |
| The Python API and every diagnostic field | [API.md](API.md) |
| What CDDL is supported, control by control | [CDDL_SUPPORT.md](CDDL_SUPPORT.md) and [CONFORMANCE.md](CONFORMANCE.md) |
| CoRIM and CoSWID schemas, with modules | [CORIM_SUPPORT.md](CORIM_SUPPORT.md) |
| Short task-oriented recipes | [RECIPES.md](RECIPES.md) |
| JSON conversion and canonical encoding | [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md) |
