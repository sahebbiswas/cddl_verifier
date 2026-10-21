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
        <<
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
        >>
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
items without any comment, and with `keyname` or `both` the keys stay as in the data.

## Nested CBOR

When a byte string holds CBOR and the schema says what the CBOR is (`bstr .cbor T`), the
output shows the decoded item between `<<` and `>>`, the notation for embedded CBOR in
RFC 8610 Appendix G.3. This works wherever the byte string is: under a tag, as a map member,
as an array element, in a choice (`bstr .cbor T / nil`), or as the root. In the example, tag
506 holds a byte string that decodes to a `concise-mid-tag`:

```edn
506(
  <<
    {
      ...
    }
  >>
)
```

The notation tells you the item is encoded twice: the bytes are the encoding of what is
inside. A `bstr .cborseq` byte string holds a CBOR sequence (RFC 8742) and is written
`<< 1, 2 >>`, with the items separated by commas. The output is a diagnostic notation, not
a format to read back: RFC 8949 section 8 gives it no grammar.

`<< >>` stands for the encoding of the items inside it. An item that is not in preferred
serialization is shown with the encoding indicators of RFC 8949 section 8.1 (#237), so the
bytes can still be read back from the notation: `_0` to `_3` after a number or string whose
head has an argument of 1, 2, 4 or 8 bytes where a shorter head would do (`1_0` is `1801`,
`"a"_0` is `780161`, `1_1` is `190001`), the width of a float (`1.5_3` is a double
precision float, shown for a float wider than it needs), and `_` after the opening bracket
or brace of an array or map of indefinite length (`[_ 1, 2]` is `9f0102ff`; `[_0 ...]` an array
with a 1-byte count). The same indicators appear on items outside `<< >>`. An indefinite
string with one chunk inside `<< >>` is `(_ "a")` or `(_ h'01')`; an empty one is `""_` or `''_`.

The decoder does not record the chunks of an indefinite string beyond the first or the width
of a tag number, and a NaN with a payload cannot be written as `NaN`. Embedded CBOR that
needs them, trailing bytes, and bytes that are not CBOR at all are printed as `h'...'`, so
the output never shows something other than what is in the data. Outside `<< >>` an
indefinite string with chunks is written as the string it holds. The data is still checked
against the schema, as `bstr .cbor T` says.
`--no-annotate` (`annotate=False`) expands the same items: only the comments, the type headers
and the member names go (#236), so a map member, an array element, a tag or a choice prints as
`<< ... >>` exactly as it does with annotation.

## When the data does not match

With an explicit `--type`, EDN is written only when validation succeeds. Without
`--type`, the first rule is used and a failure is a warning, so EDN is still written, without
annotations. With `--type` and a failure nothing is written. With `--diagnostics json`, EDN is
written only with `-o FILE`.
