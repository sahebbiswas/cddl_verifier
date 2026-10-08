# Command-line reference

The project ships two command-line tools:

| Tool | Purpose |
|------|---------|
| `cddl-verify` | Validate a CBOR file against a CDDL schema and print annotated EDN |
| `python -m cddl_verifier.json_codec` | Convert between CBOR and JSON |

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
| `--edn-format {keyindex,keyname,both}` | EDN key format (default: `keyindex`) |
| `--show-types` | Print all parsed CDDL types and exit |
| `--verbose` | Enable detailed logging of validation and type resolution |
| `--version` | Print the version and exit |

Exit status: `0` on success, `1` when a file is missing, the CBOR cannot be
decoded, or validation against an explicit `--type` fails.

When `--type` is omitted, the first type defined in the CDDL file is used as the
root. Validation errors are then reported as warnings and unannotated EDN is
printed instead of exiting.

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
```

`--typed` keeps byte strings and tags as annotated JSON objects so the conversion
can be reversed without loss. See [CANONICAL_AND_JSON.md](CANONICAL_AND_JSON.md).
