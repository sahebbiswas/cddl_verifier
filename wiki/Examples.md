# Examples

## Show parsed CDDL types

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor --show-types
```

## Validate and generate annotated EDN

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor \
  --validate --type person --output output.edn
```

Generated EDN contains comments with field names, e.g.:

```edn
{
  0: "Alice Johnson",  / name /
  1: 28,                / age /
  2: "alice@example.com",  / email /
}
```

## Generate EDN without validation

```bash
python cbor_cddl_analyzer.py example_schema.cddl example_data.cbor
```
