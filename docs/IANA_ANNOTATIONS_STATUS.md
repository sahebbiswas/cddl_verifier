# Registered parameters and EDN annotations

CoRIM, CoSWID and COSE name their map keys with *registered parameters*:
`&(id: 0) => uint` is a member whose key is the integer `0` and whose name is `id`. The
validator checks the integer; the EDN output uses the name to annotate the data.

This page says what the annotations look like and where they appear. The commands below are
run on real files (`cddl-schemas/nested_iana.cddl` and `test-data/nested_iana.cbor` are in the
repository).

## Annotations at every depth

```bash
cddl-verify cddl-schemas/nested_iana.cddl test-data/nested_iana.cbor
```

```edn
/ record / {
  / id / 0: 12345,
  / data / 1: / data-block / {
    / value / 0: 42,
    / label / 1: "example",
    / metadata / 2: / metadata-info / {
      / created / 0: 1640995200,
      / author / 1: "Alice"
    }
  }
}
```

Each key carries its parameter name (`/ id / 0`), and each structured value carries the
name of the rule it was checked against (`/ data-block / {`). That holds in maps inside maps,
in array elements, in the content of tags and in the rules a choice picks:

```cddl
record = {
  &(id: 0) => uint,
  &(data: 1) => data-block,
  &(items: 2) => [* item],
  ? &(when: 4) => #6.1(uint),
}
```

```edn
/ record / {
  / id / 0: 1,
  / data / 1: / data-block / {
    / value / 0: 2,
    / label / 1: "x"
  },
  / items / 2: [
    / item / {
      / n / 0: 5
    },
    / item / {
      / n / 0: 6
    }
  ],
  / when / 4: / #6.1(uint) / 1(1760000000)
}
```

## Three ways to write the keys

`--edn-format` chooses how a registered key is written:

| Format | A member |
|--------|----------|
| `keyindex` (default) | `/ id / 0: 1` |
| `both` | `0 / id /: 1` |
| `keyname` | `"id": 1` |

`keyname` replaces the integer with the name as a text key. That is for reading, not for
round-tripping: the result is a different item than the data. `--no-annotate` drops every
comment and prints the plain EDN.

## What is not annotated

- A member with a readable text key is printed without a name comment, as
  `"name": ...` is already readable. Legacy comment-derived names can still
  annotate non-registered keys; this behavior is deprecated below.
- A byte string that holds CBOR (`bstr .cbor T`) is printed as a byte string. Nested CBOR is
  expanded when the schema says it sits inside a tag; see
  [EDN_FORMATTING_IMPROVEMENTS.md](EDN_FORMATTING_IMPROVEMENTS.md#nested-cbor).
- Annotations are comments in the output only. Validation never depends on them.

## Comment-derived names

A comment at the end of a member line (`0: tstr ; name`) is used as the field name in
the output when the member has no registered parameter. That behaviour is deprecated
([#108](https://github.com/sahebbiswas/cddl_verifier/issues/108)); give the member a
registered parameter or a name key instead.
