# EDN output format

`cddl-verify` prints data as annotated EDN (Extended Diagnostic Notation, RFC 8949 section 8).
The comments come from the schema; the data is unchanged. This page describes where each
kind of annotation goes. The examples are the real output for `cddl-schemas/unified.cddl` and
`test-data/minimal-corim.cbor` (a CoRIM in tag 501).

```bash
cddl-verify cddl-schemas/unified.cddl test-data/minimal-corim.cbor --type tagged-unsigned-corim-map
```

```edn
/ tagged-unsigned-corim-map / 501(
  / corim-map / {
    / id / 0: "urn:example:corim:minimal-example",
    / tags / 1: [
      / tagged-concise-mid-tag / 506(
        bytes<60>(
          / concise-mid-tag / {
            / tag-identity / 1: / tag-identity-map / {
              / tag-id / 0: "urn:example:comid:12345"
            },
            / triples / 4: / triples-map / {
              / reference-triples / 0: [
                / reference-triple-record / [
                  / environment-map / {
                    / group / 2: / tagged-uuid-type / 37(h'01010101010101010101010101010101')
                  },
                  [
                    / measurement-map / {
                      / mval / 1: / measurement-values-map / {
                        / svn / 1: 7
                      }
                    }
                  ]
                ]
              ]
            }
          }
        )
      )
    ]
  }
)
```

## Annotations are on the left

Every annotation is a `/ comment /` that comes before what it describes, so reading down
the left edge gives the names:

| What | Looks like |
|------|-----------|
| A map member with a registered parameter | `/ id / 0: "..."` |
| A tag | `/ tagged-concise-mid-tag / 506(` |
| A map or array | `/ corim-map / {`, `/ reference-triple-record / [` |
| A value that is a tag with a named type | `/ tagged-uuid-type / 37(h'...')` |

The name in a map or array comment is the CDDL rule the value was checked against. For the
formats of the keys, see [IANA_ANNOTATIONS_STATUS.md](IANA_ANNOTATIONS_STATUS.md#three-ways-to-write-the-keys)
and `--edn-format` in [CLI.md](CLI.md#edn-output-formats). `--no-annotate` prints the same
data without any comment.

## Nested CBOR

When the content of a tag is a byte string that holds CBOR, and the schema says what the
CBOR is, the output shows the decoded item inside a `bytes<N>(...)` wrapper. `N` is the length
of the byte string. In the example, tag 506 holds 60 bytes that decode to a `concise-mid-tag`:

```edn
506(
  bytes<60>(
    {
      ...
    }
  )
)
```

The wrapper tells you the item is encoded twice: the bytes are the encoding of the map inside.
A byte string that is not under a tag, even with `.cbor` in the schema, is printed as
`h'...'`; it is still checked against the schema, as `bstr .cbor T` says.

## When the data does not match

With an explicit `--type`, EDN is written only when validation succeeds. Without
`--type`, the first rule is used and a failure is a warning, so EDN is still written, without
annotations. With `--type` and a failure nothing is written. With `--diagnostics json`, EDN is
written only with `-o FILE`.
