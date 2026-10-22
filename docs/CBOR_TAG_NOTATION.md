# CBOR Tag Notation in EDN Output

## Feature

EDN output uses the standard CBOR tag notation `tag_number(content)` to show tagged data, so it is clear which CBOR tags wrap which content. The tag numbers on this page are those of `draft-ietf-rats-corim` (`cddl-schemas/unified.cddl`), and the examples are the output of the tool for the CoRIM built in [Complete Example](#complete-example); `tests/test_corim_docs.py` checks both.

## Syntax

```edn
tag_number(
  content
)
```

Where:
- `tag_number` is the CBOR tag value (0-18446744073709551615)
- `content` is the tagged data (can be any CBOR type)

The CDDL type of the tag is a comment in front of it (`/ tagged-concise-mid-tag / 506(...)`) when the schema names it.

## Tags of a CoRIM

| Tag | CDDL name | Content |
|-----|-----------|---------|
| 501 | `tagged-unsigned-corim-map` | the CoRIM map |
| 18 | `signed-corim` | a COSE_Sign1 whose payload is the encoded tag 501 |
| 505 | `tagged-concise-swid-tag` | a CoSWID, `bytes .cbor` |
| 506 | `tagged-concise-mid-tag` | a CoMID, `bytes .cbor` |
| 508 | `tagged-concise-tl-tag` | a CoTL, `bytes .cbor` |
| 550 | `tagged-ueid-type` | a UEID (`ueid-type`, a byte string) |
| 552 | `tagged-svn` | a security version number |
| 111 | `tagged-oid-type` | an OID in its BER encoding (a byte string) |
| 37 | `tagged-uuid-type` | a UUID (a byte string) |
| 32 | `uri` | a URI (RFC 8610 prelude) |

Full registry: https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml

## Examples

### Tagged maps (CoRIM structure)

The CoRIM is a map in tag 501, and each CoMID in its tags array is tag 506. The content of
tags 505, 506 and 508 is a byte string holding the *encoded* item (`bytes .cbor`), which EDN
shows between `<<` and `>>` ([EDN_FORMATTING_IMPROVEMENTS.md](EDN_FORMATTING_IMPROVEMENTS.md)); the [Complete Example](#complete-example) below has them all. Its first lines:

```edn
/ tagged-unsigned-corim-map / 501(
  / corim-map / {
    / id / 0: "urn:example:corim:acme-roadrunner",
    / tags / 1: [
      / tagged-concise-mid-tag / 506(
        <<
          / concise-mid-tag / {
          ...
        >>
      )
    ]
  }
)
```

### Common tags

Tags on values inside the map, as the output shows them:

```edn
/ reg-id / 1: / uri / 32("https://acme.example"),
/ class-id / 0: / tagged-oid-type / 111(h'2b06010401'),
/ instance / 1: / tagged-ueid-type / 550(h'0102020202020202020202020202020202'),
/ svn / 1: / tagged-svn / 552(7)
```

## Complete Example

The data is built with the library (`CBORTag(506, encode(comid))` for each CoMID, `CBORTag(501, corim)` for the CoRIM):

```bash
cddl-verify cddl-schemas/unified.cddl corim.cbor --type corim
```

```edn
/ tagged-unsigned-corim-map / 501(
  / corim-map / {
    / id / 0: "urn:example:corim:acme-roadrunner",
    / tags / 1: [
      / tagged-concise-mid-tag / 506(
        <<
          / concise-mid-tag / {
            / language / 0: "en-GB",
            / tag-identity / 1: / tag-identity-map / {
              / tag-id / 0: "urn:example:comid:acme-roadrunner"
            },
            / entities / 2: [
              / comid-entity-map / {
                / entity-name / 0: "ACME Ltd.",
                / reg-id / 1: / uri / 32("https://acme.example"),
                / role / 2: [
                  0,
                  1
                ]
              }
            ],
            / triples / 4: / triples-map / {
              / reference-triples / 0: [
                / reference-triple-record / [
                  / environment-map / {
                    / class / 0: / class-map / {
                      / class-id / 0: / tagged-oid-type / 111(h'2b06010401'),
                      / vendor / 1: "ACME",
                      / model / 2: "RoadRunner"
                    },
                    / instance / 1: / tagged-ueid-type / 550(h'0102020202020202020202020202020202')
                  },
                  [
                    / measurement-map / {
                      / mkey / 0: "BL",
                      / mval / 1: / measurement-values-map / {
                        / version / 0: / version-map / {
                          / version / 0: "2.1.0"
                        },
                        / svn / 1: / tagged-svn / 552(7)
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

### Tag annotations

- **501** `tagged-unsigned-corim-map` wraps the whole CoRIM.
- **506** `tagged-concise-mid-tag` wraps the CoMID as embedded CBOR (`<< >>`).
- **32** `uri` wraps the entity's `reg-id`.
- **111** `tagged-oid-type` wraps the `class-id` of the environment.
- **550** `tagged-ueid-type` wraps the `instance`.
- **552** `tagged-svn` wraps the measured `svn`.

## How It Works

### Detection

When the decoder meets a tagged value, it returns a `CBORTag`:

```python
CBORTag(tag_number, value)
```

### Generation

The EDN generator writes the tag number, then the EDN of the content, in parentheses. For a tag whose content is `bytes .cbor T` (505, 506, 508), the embedded item is decoded, checked against `T` and shown in `<< >>`, so the output has the tag, the byte string and the structure inside it:

1. The data is wrapped in tag **506**.
2. The CDDL type is `tagged-concise-mid-tag`.
3. The tag holds a byte string of CBOR.
4. That CBOR decodes to a `concise-mid-tag`.

A byte string that is not in preferred serialization, or is not CBOR at all, is kept as `h'...'` or shown with its encoding indicators, so the output is never something other than the data.

## Benefits

- **Tag visibility.** The tag number is in front of the value (`32("https://acme.example")`), not in a comment or separate metadata.
- **Standard notation.** The `tag_number(content)` form of CBOR diagnostic notation (RFC 8949 section 8, RFC 8610 Appendix G).
- **Clear boundaries.** The opening `tag(` and closing `)` delimit the tagged content, nested tags included.
- **Tag and type together.** The tag number and the CDDL type name are on the same line.

## Usage

Tag notation is part of the EDN output:

```bash
cddl-verify schema.cddl data.cbor --type corim --edn-format keyindex
```

## Validation

Looking at the notation helps check that:
- The right tags are used (506 for a CoMID, not 505, which is a CoSWID).
- Tags wrap the right content (tag 32 wraps a text string, tag 506 a byte string of CBOR).
- Tags are nested correctly.
- No tag is missing or extra.

The data is also validated against the schema first, which checks the same things.
