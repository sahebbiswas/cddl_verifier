# Usage

Basic command-line usage:

```bash
# Generate annotated EDN to stdout
python cbor_cddl_analyzer.py schema.cddl data.cbor

# Save EDN to a file
python cbor_cddl_analyzer.py schema.cddl data.cbor --output data.edn

# Validate CBOR against CDDL schema
python cbor_cddl_analyzer.py schema.cddl data.cbor --validate --type person

# Generate EDN without annotations
python cbor_cddl_analyzer.py schema.cddl data.cbor --no-annotate
```

## Command-line options (summary)

- `-o, --output PATH`  Output EDN file (default: stdout)
- `-t, --type TYPE`    Root type name from CDDL for validation
- `-v, --validate`     Validate CBOR against CDDL
- `-a, --annotate`     Annotate EDN with field names (default: True)
- `--no-annotate`      Disable annotations in EDN output
- `--show-types`       Show parsed CDDL types and exit

For more detailed examples, see `Examples.md`.
