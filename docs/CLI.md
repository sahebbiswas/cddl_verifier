# Command-line reference

The project ships two command-line tools:

| Tool | Purpose |
|------|---------|
| `cddl-verify` | Validate a CBOR file against a CDDL schema and print annotated EDN |
| `python -m cddl_verifier.json_codec` | Convert between CBOR and JSON |

For complete workflows, see the [task-oriented recipes](RECIPES.md).

## `cddl-verify`

`cddl-verify` is installed by `pip install cddl-verifier`. `python -m cddl_verifier`
takes the same arguments.

```bash
# Decode CBOR and print annotated EDN on stdout
cddl-verify schema.cddl data.cbor

# Validate against a named root type, then print annotated EDN
cddl-verify schema.cddl data.cbor --type corim-map

# Write EDN to a file
cddl-verify schema.cddl data.cbor --type corim-map --output data.edn

# Use readable key names instead of integer indices
cddl-verify schema.cddl data.cbor --edn-format keyname

# Suppress field-name annotations
cddl-verify schema.cddl data.cbor --no-annotate

# Report problems as one JSON document on stdout (for scripts and CI)
cddl-verify schema.cddl data.cbor --type corim-map --diagnostics json

# Refuse CBOR that is not deterministically encoded (a signed payload, say)
cddl-verify schema.cddl data.cbor --type corim-map --require-deterministic

# Print all parsed CDDL types and exit (useful for debugging schemas)
cddl-verify schema.cddl data.cbor --show-types

# Enable verbose logging of type resolution and validation steps
cddl-verify schema.cddl data.cbor --type corim-map --verbose

# Print the tool version
cddl-verify --version
```

### Options

| Flag | Description |
|------|-------------|
| `cddl_file` | Path to the CDDL schema file (positional) |
| `cbor_file` | Path to the CBOR binary file (positional) |
| `-o / --output PATH` | Write EDN to a file instead of stdout |
| `-t / --type TYPE` | Root CDDL type name; validation failures are fatal |
| `--no-annotate` | Suppress field-name comments in EDN output |
| `--require-deterministic` | Fail when the CBOR is not deterministically encoded (RFC 8949 §4.2.1), with the offset of the first violation; see [API.md](API.md#requiring-deterministic-encoding) for the rules |
| `--unique-choices` | Fail when data matches more than one map, array or tag alternative of a choice |
| `--comment-names` | Name a member after its trailing comment (`0: tstr ; name`) in messages and EDN annotations. Off by default: comments carry no meaning in CDDL (RFC 8610), so a member is named by its key; a `&(name: 0)` label always wins over a comment; see [CDDL_SUPPORT.md](CDDL_SUPPORT.md#member-names) |
| `--edn-format {keyindex,keyname,both}` | EDN key format (default: `keyindex`) |
| `-M DIR`, `--module-path DIR` | Directory searched for imported CDDL modules (`<name>.cddl`); repeatable. The schema file's own directory is always searched, last |
| `--module NAME=FILE` | CDDL file for the imported module `NAME`; repeatable |
| `--diagnostics {text,json}` | How problems are reported (default: `text`); see [Diagnostics](#diagnostics) |
| `--show-types` | Print all parsed CDDL types and exit (not with `--diagnostics json`) |
| `--verbose` | Enable detailed logging of validation and type resolution |
| `--version` | Print the version and exit |

Exit status: `0` on success, `1` when a file is missing, the schema cannot be
loaded, the CBOR cannot be decoded (or, with `--require-deterministic`, is not
deterministically encoded), or validation against an explicit `--type` fails. `--diagnostics json` uses the same statuses.

When `--type` is omitted, the first type defined in the CDDL file is used as the
root. Validation errors are then reported as warnings and unannotated EDN is
printed instead of exiting.

### Diagnostics

By default problems are printed as messages on stderr. `--diagnostics json`
makes the result machine-readable:

- **stdout is exactly one JSON document** and nothing else, whatever happens.
  Progress lines, warnings and `--verbose` logs go to stderr.
- **EDN is written only with `-o FILE`**, and only when there is something to
  show (the data decoded, and validated if `--type` was given). Without `-o`
  nothing but the document is printed.
- The exit status is the one of the text mode (see above).
- `--show-types` prints to stderr only and cannot be combined with
  `--diagnostics json`.

The document is the one `ValidationResult.to_json()` writes, so the fields are
described in [API.md](API.md#diagnostic): `format_version`, `valid` (`true`,
`false`, or `null` when no root type was checked), `root_type`, and
`diagnostics`. A schema that cannot be loaded gives its problems as
`code: "schema"` diagnostics (all of them, not just the first), CBOR that cannot
be decoded gives one `code: "decode"` diagnostic, and a file that is missing is
reported the same way, so a script never has to parse stderr.

**Nested validation failure.** With `schema.cddl` as `doc = { 1: [* uint], name: tstr }`
and `data.cbor` holding `{1: [1, "x"], "name": "x"}`:

```bash
cddl-verify schema.cddl data.cbor --type doc --diagnostics json 2>/dev/null
```

```json
{
  "format_version": 1,
  "valid": false,
  "root_type": "doc",
  "diagnostics": [
    {
      "severity": "error",
      "category": "type",
      "code": "validation",
      "message": "Element [1] of field '1' has wrong type: expected uint, got str",
      "path": [1, 1],
      "path_expr": "$<key 1>[1]",
      "offset": 4,
      "end_offset": 6,
      "hex": "61 78",
      "hex_truncated": false,
      "rule": "doc",
      "schema_path": ["doc", "1", "[1]"],
      "schema_location": "schema.cddl:1:1",
      "expected": "uint",
      "actual": "tstr",
      "alternatives": [],
      "line": null,
      "column": null
    }
  ]
}
```

(The real output has one value per line; arrays are shortened here.)

**Decode failure.** For `data.cbor` cut short after `a2 01 82 01`:

```json
{
  "format_version": 1,
  "valid": false,
  "root_type": null,
  "diagnostics": [
    {
      "severity": "error",
      "category": "decode",
      "code": "decode",
      "message": "Unexpected end of data (at offset 4)",
      "path": [],
      "path_expr": "$",
      "offset": 4,
      "end_offset": null,
      "hex": null,
      "hex_truncated": false,
      "rule": null,
      "schema_path": [],
      "schema_location": null,
      "expected": null,
      "actual": null,
      "alternatives": [],
      "line": null,
      "column": null
    }
  ]
}
```

In text mode the same failures are listed on stderr:

```text
[FAIL] Validation failed:
  - Element [1] of field '1' has wrong type: expected uint, got str
```

**Reading a path.** `path` is the list of steps from the root, as the decoder
returns them. An integer map key and an array index look alike there, so
`path_expr` writes the path in an unambiguous notation:

| Step | `path_expr` | Example |
|------|-------------|---------|
| root | `$` | `$` |
| array index | `[2]` | `$[1][2]`: element 2 of element 1 |
| text key (a name) | `.name` | `$.name` |
| text key that is not a name | `["odd key"]` | `$["a b"]` |
| any other key | `<key 1>`, `<key h'ab'>`, `<key true>` | `$<key 1>[1]`: element 1 of the array at integer key 1 |

**Order.** Diagnostics are in the order the schema is checked: the members of a
map in the order the schema lists them, then keys the schema does not know in the
order they appear in the data. The order is the same on every run for the same
schema and data. Validation does not stop at the first failure, so the document
lists every problem found in one pass; only a decode failure or a schema that
cannot be loaded stops earlier, and then it is the only diagnostic (or, for a
schema, all of its problems).

**From a diagnostic to the bytes.** `offset` and `end_offset` are decimal byte
positions in the CBOR input; `hex` is the first 16 bytes of the item (`hex_truncated`
tells when it is longer). To see the item in context, print the diagnostic dump
(its left column is the offset in *hex*; `offset` 4 is line `0004`):

```bash
python -c "import sys; from cddl_verifier.cbor import diag_dump; print(diag_dump(open(sys.argv[1], 'rb').read()))" data.cbor
```

```text
0003:     01                            # [0] uint(1)
0004:     61                            # [1] text(1)
0005:       78                          # "x"
```

To keep the report and the annotated EDN of the same run, ask for both. The
report is on stdout and the EDN goes to the file:

```bash
cddl-verify schema.cddl data.cbor --type doc --diagnostics json -o data.edn > report.json
```

If validation fails with an explicit `--type`, no EDN is written (as in text
mode). Without `--type` the first rule is used as a guess: its failures are then
`"severity": "warning"`, the exit status is `0`, and unannotated EDN is written.

### Deterministic encoding

`--require-deterministic` rejects input that is well-formed but not written the
one way RFC 8949 section 4.2.1 prescribes, before the schema is checked. It is
for payloads whose bytes are signed or hashed, where two encodings of the same
value would be two different messages.

```
$ cddl-verify schema.cddl data.cbor --type doc --require-deterministic
Error decoding CBOR: Not deterministically encoded: integer is not in its shortest form (additional information 24) (at offset 3)
```

The offset is the start of the first item that breaks a rule. With
`--diagnostics json` the same failure is one `decode` diagnostic with that
`offset`. See [API.md](API.md#requiring-deterministic-encoding) for the rules.

### Imported modules

A schema can import other CDDL files with the comment syntax of the CDDL
modules draft (draft-ietf-cbor-cddl-modules):

```cddl
;# import rfc9393 as coswid
r = { tag: coswid.tag-id }
```

`cddl-verify` looks for `rfc9393.cddl` in each `--module-path` directory and
then next to the schema file, unless `--module rfc9393=path/to/file.cddl` names
it. The module's rules are checked and become `coswid.<rule>`. A module that
is not found prints a `Warning:` line on stderr and the names used from it
match any value.

### Input requirements

The CBOR file must contain **exactly one** CBOR data item. An empty file, a
truncated item, or extra bytes after the item (for example, a concatenated CBOR
sequence) makes the tool exit with status 1 and an error that gives the byte
offset:

```
Error decoding CBOR: Trailing data after CBOR item: 1 extra byte(s) (at offset 1)
```

See [API.md](API.md#strict-single-item-decoding) for the full list of decode errors.

### EDN output formats

For the schema `person = { &(name: 0) => tstr, &(age: 1) => uint }` and the
data `{0: "Alice", 1: 30}`:

**`keyindex`** (default): integer keys with field-name comments.

```edn
/ person / {
  / name / 0: "Alice",
  / age / 1: 30
}
```

**`keyname`**: keys replaced by quoted names (IANA registered fields only).

```edn
/ person / {
  "name": "Alice",
  "age": 30
}
```

**`both`**: integer key and name together.

```edn
/ person / {
  0 / name /: "Alice",
  1 / age /: 30
}
```

See [EDN_FORMATTING_IMPROVEMENTS.md](EDN_FORMATTING_IMPROVEMENTS.md) for details on
annotation placement and the `bytes<N>(...)` wrapper used for nested CBOR fields.

## `python -m cddl_verifier.json_codec`

```bash
python -m cddl_verifier.json_codec to-json  input.cbor output.json --pretty --typed
python -m cddl_verifier.json_codec to-cbor  input.json output.cbor --canonical
python -m cddl_verifier.json_codec to-cbor  input.json output.cbor --plain
```

`to-cbor` reads `{"$cbor": ...}` objects as type annotations and rejects a malformed
one, naming its place (`$.items[2].data`). `--plain` reads the JSON as plain data, so a
`"$cbor"` key is an ordinary key; use it for JSON that must not be able to make byte
strings or tags.

`--typed` keeps byte strings and tags as annotated JSON objects so the conversion
can be reversed without loss. See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md).
