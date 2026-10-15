#!/usr/bin/env python3
"""Rebuild the generated suites of the conformance corpus in ``tests/corpus`` (#80).

The corpus is data: schemas copied from published sources, CBOR examples taken
from the same sources (or derived from them), the verdict the source gives, and
where it came from. ``tests/test_corpus.py`` runs it. This script is how the
generated suites were made; it needs clones of the upstream repositories and
``pip install cbor-diag`` (to turn the diagnostic notation of the upstream
examples into CBOR). It is not needed to run the tests.

Usage::

    python scripts/build_corpus.py --sources DIR [--only SUITE ...]

``DIR`` holds shallow clones named after what they are::

    cddlc           https://github.com/cabo/cddlc                         (test/, data/)
    corim           https://github.com/ietf-rats-wg/draft-ietf-rats-corim  (cddl/)
    cose-examples   https://github.com/cose-wg/Examples
    edhoc           https://github.com/lake-wg/edhoc                       (test-vectors-15/)
    suit-manifest   https://github.com/suit-wg/manifest-spec

Every suite records the commit it was taken from, so a rebuild shows what
upstream changed. After building, the script runs this library over every case:
a disagreement with the source's verdict must be explained in the tables below
(a ``gap`` with its issue, a ``divergence`` or a ``source-defect`` with the
reason), or the script stops. 
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _corpus  # noqa: E402
from cddl_verifier import cbor as cbor_api  # noqa: E402
from cddl_verifier._cbor_values import CBORTag  # noqa: E402

CORPUS = _corpus.CORPUS
SUITES = _corpus.SUITES
SCHEMAS = CORPUS / "schemas"

IETF_NOTE = ("CDDL and examples published in IETF documents: IETF Trust Legal Provisions "
             "(code components under the Revised BSD License)")


# ------------------------------------------------------------------ helpers

def git_commit(repo: Path) -> dict:
    out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%H%n%cs"],
                         capture_output=True, text=True, check=True).stdout.split()
    return {"commit": out[0], "date": out[1]}


def source(name, url, repo, license_, path=None):
    entry = {"name": name, "url": url, **git_commit(repo), "license": license_}
    if path:
        entry["path"] = path
    return entry


def edn_to_cbor(text: str) -> bytes:
    import cbor_diag
    result = cbor_diag.diag2cbor(text)
    return result.encode("latin1") if isinstance(result, str) else bytes(result)


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def schema_file(relative: str, text: str) -> str:
    write_text(SCHEMAS / relative, text if text.endswith("\n") else text + "\n")
    return "schemas/" + relative


def case(case_id, expect, source_text, hex_data=None, **extra):
    entry = {"id": case_id}
    entry.update({k: v for k, v in extra.items() if k in ("schema", "root", "encoding", "modules")})
    if hex_data is not None:
        entry["hex"] = hex_data
    entry["expect"] = expect
    entry.update({k: v for k, v in extra.items()
                  if k not in ("schema", "root", "encoding", "modules") and v not in (None, [], "")})
    entry["source"] = source_text
    return entry


def merged(base: dict, updates: dict) -> dict:
    return {**base, **updates}


def encode(value) -> bytes:
    return cbor_api.encode(value)


def encode_sequence(items) -> bytes:
    return b"".join(encode(item) for item in items)


# ------------------------------------------------------------------ explanations
#
# What this library does differently from a source, and why. A case the library
# disagrees with that no function here explains stops the build.

def explain_cddlc(entry, detail):
    name = entry["id"].split("/")[1]
    notes = {
        "13a-size": ("divergence", None,
                     "RFC 8610 §3.8.1 defines `.size` for byte strings, text strings and unsigned "
                     "integers; this library does not accept `int` as the target of `.size` "
                     "(`uint .size N` works), where cddlc applies it to the non-negative integers."),
        "16-feature": ("divergence", None, "RFC 9165 leaves which features are enabled to the tool; this "
                       "library has no way to name them, so every `.feature` counts as enabled and the "
                       "base type alone decides."),
        "6-bigint": ("divergence", None,
                     "The RFC 8610 prelude defines `bigint = biguint / bignint` (tags 2 and 3 only) "
                     "and this library follows it: an integer that fits in 64 bits is an `int` or "
                     "`integer`, not a `bigint`. The cddlc test also accepts such integers."),
    }
    notes["16a-feature"] = notes["16-feature"]
    for control, label in (("18-abnf", ".abnf"), ("20-plus-i", ".plus"), ("21-cat", ".cat")):
        notes[control] = ("gap", 76, f"`{label}` is parsed but not enforced.")
    for det in ("22-det", "22a-det", "22b-det", "22c-det", "22d-det", "22e-det"):
        notes[det] = ("gap", 76, "`.det` is parsed but not enforced.")
    return notes.get(name)


EXPLAIN = {"cddlc-tests": explain_cddlc}


def classify(suite):
    """Run the library over *suite* and record what it disagrees with."""
    explain = EXPLAIN.get(suite["id"], lambda entry, detail: None)
    stuck = []
    for entry in suite["cases"]:
        entry.pop("status", None)
        for key in ("issue", "reason"):
            entry.pop(key, None)
        verdict, detail = _corpus.run(suite, entry)
        if verdict == entry["expect"]:
            continue
        found = explain(entry, detail)
        if not found:
            stuck.append((entry["id"], entry["expect"], verdict, detail[:200]))
            continue
        status, issue, reason = found
        entry["status"] = status
        if issue:
            entry["issue"] = issue
        entry["reason"] = reason
    if stuck:
        print(f"{suite['id']}: the library disagrees with the source and nothing explains it:",
              file=sys.stderr)
        for item in stuck:
            print("  ", *item, file=sys.stderr)
        raise SystemExit(1)


def write_suite(suite):
    suite["cases"].sort(key=lambda entry: entry["id"])
    classify(suite)
    for entry in suite["cases"]:
        entry.pop("_", None)
    write_text(SUITES / f"{suite['id']}.json", json.dumps(suite, indent=1, ensure_ascii=False) + "\n")
    counts = {}
    for entry in suite["cases"]:
        key = entry.get("status", "pass")
        counts[key] = counts.get(key, 0) + 1
    print(f"{suite['id']}: {len(suite['cases'])} cases {counts}")


# ------------------------------------------------------------------ cddlc

CDDLC_CONSTRUCTS = {
    "0-any": ["prelude.any"], "1": ["syntax.int-literal"], "2-int": ["prelude.integers"],
    "2-nint": ["prelude.integers"], "2-uint": ["prelude.integers"], "3-bstr": ["prelude.bytes"],
    "4-tstr": ["prelude.text"], "6-bigint": ["prelude.bignums"], "6-tag": ["syntax.tag"],
    "7-bool": ["prelude.bool"], "7-float16": ["prelude.floats"], "8-op": ["control.regexp"],
    "10-rangetrue": ["syntax.range"], "10a-rangefalse": ["syntax.range"],
    "11-badrange": ["syntax.range"], "12-unspecrange": ["syntax.range"],
    "13-size": ["control.size"], "13a-size": ["control.size"],
    "14a-bits-bstr": ["control.bits"], "14b-bits-uint": ["control.bits"],
    "15-default": ["control.default"], "16-feature": ["control.feature"],
    "16a-feature": ["control.feature"], "17-regexp": ["control.regexp"], "18-abnf": ["control.abnf"],
    "20-plus-i": ["control.plus"], "21-cat": ["control.cat"], "22-det": ["control.det"],
    "22a-det": ["control.det"], "22b-det": ["control.det"], "22c-det": ["control.det"],
    "22d-det": ["control.det"], "22e-det": ["control.det"],
}


def build_cddlc(sources: Path):
    repo = sources / "cddlc"
    cases = []
    for path in sorted((repo / "test").glob("*.cddl")):
        name = path.stem
        text = read(path)
        relative = schema_file(f"cddlc/{path.name}", text)
        constructs = CDDLC_CONSTRUCTS[name]
        if name in ("11-badrange", "12-unspecrange"):
            cases.append(case(f"cddlc/{name}", "schema-error", f"test/{path.name}: a schema cddlc rejects",
                              schema=relative, constructs=constructs))
            continue
        for index, match in enumerate(re.finditer(r"^;;([+-]) ?(.*)$", text, re.M), 1):
            sign, edn = match.group(1), match.group(2).strip()
            try:
                data = edn_to_cbor(edn)
            except Exception:
                continue  # `;;+ /foo/`: a comment, not an item
            cases.append(case(f"cddlc/{name}/{index:02d}", "valid" if sign == "+" else "invalid",
                              f"test/{path.name}: `;;{sign} {edn}`", data.hex(), schema=relative,
                              edn=edn, constructs=constructs))
    return {"id": "cddlc-tests", "title": "cddlc test suite: RFC 8610 and RFC 9165 constructs",
            "sources": [source("cddlc test suite", "https://github.com/cabo/cddlc", repo, "MIT", "test/")],
            "provenance": "converted", "cases": cases}


# ------------------------------------------------------------------ COSE

COSE_ALL = ("RFC8152", "CWT", "sign-tests", "sign1-tests", "mac-tests", "mac0-tests",
            "encrypted-tests", "enveloped-tests")
COSE_FIRST = ("aes-ccm-examples", "aes-gcm-examples", "aes-wrap-examples", "cbc-mac-examples",
              "chacha-poly-examples", "countersign", "countersign1", "ecdh-direct-examples",
              "ecdh-wrap-examples", "ecdsa-examples", "eddsa-examples", "hashsig", "hkdf-aes-examples",
              "hkdf-hmac-sha-examples", "hmac-examples", "rsa-oaep-examples", "rsa-pss-examples",
              "x509-examples", "X25519-tests")


def cose_constructed():
    """Messages built from the rules of RFC 9052's CDDL, with the verdict the rules give."""
    sig = b"\x00" * 8
    prot_alg = encode({1: -7})
    ok_sign1 = [b"", {}, b"hi", sig]
    rows = [
        ("sign1-minimal", "valid", CBORTag(18, ok_sign1), "a tagged COSE_Sign1 with an empty protected header"),
        ("sign1-untagged", "valid", ok_sign1, "an untagged COSE_Sign1 is a COSE_Untagged_Message"),
        ("sign1-protected-alg", "valid", CBORTag(18, [prot_alg, {4: b"11"}, b"hi", sig]),
         "the protected header is a byte string holding a map (`bstr .cbor header_map`)"),
        ("sign1-detached", "valid", CBORTag(18, [prot_alg, {}, None, sig]), "payload may be nil"),
        ("sign1-three-elements", "invalid", CBORTag(18, [b"", {}, sig]), "COSE_Sign1 has four elements"),
        ("sign1-payload-int", "invalid", CBORTag(18, [b"", {}, 5, sig]), "payload is `bstr / nil`"),
        ("sign1-protected-map", "invalid", CBORTag(18, [{}, {}, b"hi", sig]),
         "the protected header is serialized: a map is not allowed"),
        ("sign1-unprotected-bytes", "invalid", CBORTag(18, [b"", b"", b"hi", sig]),
         "the unprotected header is a map"),
        ("sign1-protected-not-a-map", "invalid", CBORTag(18, [b"\x01", {}, b"hi", sig]),
         "the protected header holds CBOR that is not a map (`bstr .cbor header_map`)"),
        ("sign1-alg-bytes", "valid", CBORTag(18, [encode({1: b""}), {}, b"hi", sig]),
         "the alg header is written `? 1 => int / tstr` and `=>` does not cut (RFC 8610 §3.5.4), so "
         "`* label => values` takes an alg that is not an int or text string: the CDDL cannot forbid it"),
        ("sign1-signature-text", "invalid", CBORTag(18, [b"", {}, b"hi", "sig"]), "signature is a byte string"),
        ("tag-around-map", "invalid", CBORTag(18, {1: 2}), "tag 18 wraps a COSE_Sign1 array"),
        ("mac0-minimal", "valid", CBORTag(17, [b"", {}, b"hi", sig]), "a tagged COSE_Mac0"),
        ("mac-no-recipients", "invalid", CBORTag(97, [b"", {}, b"hi", sig, []]), "COSE_Mac needs `[+ COSE_recipient]`"),
        ("mac-one-recipient", "valid", CBORTag(97, [b"", {}, b"hi", sig, [[b"", {}, b""]]]),
         "a COSE_Mac with one recipient"),
        ("encrypt0-minimal", "valid", CBORTag(16, [b"", {5: b"\x00" * 12}, b"\xcc" * 4]), "a tagged COSE_Encrypt0"),
        ("encrypt0-ciphertext-int", "invalid", CBORTag(16, [b"", {}, 3]), "ciphertext is `bstr / nil`"),
        ("sign-no-signatures", "invalid", CBORTag(98, [b"", {}, b"hi", []]), "COSE_Sign needs `[+ COSE_Signature]`"),
        ("sign-one-signature", "valid", CBORTag(98, [b"", {}, b"hi", [[b"", {}, sig]]]),
         "a COSE_Sign with one COSE_Signature"),
        ("key-ec2", "valid", {1: 2, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32}, "a COSE_Key (EC2)", "COSE_Key"),
        ("key-no-kty", "invalid", {2: b"11"}, "COSE_Key requires `kty` (1)", "COSE_Key"),
        ("key-kty-bytes", "invalid", {1: b"\x02"}, "`kty` is `tstr / int`", "COSE_Key"),
        ("keyset", "valid", [{1: 1}], "a COSE_KeySet", "COSE_KeySet"),
        ("keyset-empty", "invalid", [], "COSE_KeySet is `[+ COSE_Key]`", "COSE_KeySet"),
    ]
    return rows


def build_cose(sources: Path):
    examples = sources / "cose-examples"
    cddlc = sources / "cddlc"
    schema = schema_file("ietf/rfc9052.cddl", read(cddlc / "data" / "rfc9052.cddl"))
    cases = []
    constructs = ["syntax.tag", "control.cbor", "syntax.type-choice", "syntax.array", "syntax.map"]
    for directory in COSE_ALL + COSE_FIRST:
        files = sorted((examples / directory).glob("*.json"), key=lambda p: p.name)
        if directory in COSE_FIRST:
            files = files[:1]
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            output = document.get("output") if isinstance(document, dict) else None
            if not isinstance(output, dict) or not output.get("cbor"):
                continue
            failures = (document.get("input") or {}).get("failures") or {}
            title = document.get("title", "")
            note = None
            if "ChangeCBORTag" in failures:
                verdict, note = "invalid", f"the tag is changed to {failures['ChangeCBORTag']}"
            elif document.get("fail"):
                verdict = "valid"
                note = ("a failure test of the COSE layer (" + ", ".join(sorted(failures)) +
                        "): the structure is still valid CDDL") if failures else \
                    "a failure test of the COSE layer (the signature or key): the structure is valid CDDL"
            else:
                verdict = "valid"
            cases.append(case(f"cose-wg/{directory}/{path.stem}", verdict,
                              f"{directory}/{path.name}: {title}".strip(), output["cbor"].lower(),
                              constructs=constructs, note=note, provenance="verbatim"))
    for row in cose_constructed():
        name, expect, value, why = row[:4]
        root = row[4] if len(row) > 4 else None
        cases.append(case(f"constructed/{name}", expect, f"RFC 9052 CDDL: {why}", encode(value).hex(),
                          root=root, constructs=constructs, provenance="constructed"))
    return {"id": "cose-rfc9052", "title": "COSE messages and keys (RFC 9052)", "schema": schema,
            "sources": [source("COSE WG examples", "https://github.com/cose-wg/Examples", examples, "Unlicense"),
                        source("RFC 9052 CDDL, extracted by cddlc", "https://github.com/cabo/cddlc",
                               cddlc, "MIT (the extraction); " + IETF_NOTE, "data/rfc9052.cddl")],
            "provenance": "verbatim", "cases": cases}


# ------------------------------------------------------------------ CoRIM

def corim_fragments(corim: Path):
    text = read(corim / "cddl" / "corim-frags.mk")
    values = {}
    for match in re.finditer(r"^(\w+)\s*([:+]?)=\s*(.*)$", text, re.M):
        name, op, value = match.groups()
        values[name] = (values.get(name, "") + " " + value) if op == "+" else value

    def expand(name):
        return re.sub(r"\$\((\w+)\)", lambda m: expand(m.group(1)), values[name])

    return {kind: expand(var).split() for kind, var in (
        ("comid", "COMID_FRAGS"), ("cotl", "COTL_FRAGS"), ("corim", "CORIM_FRAGS"),
        ("intrep-acs", "INTREP_ACS_FRAGS"), ("intrep-rel", "INTREP_REL_FRAGS"))}


def build_corim(sources: Path):
    corim, cddlc = sources / "corim", sources / "cddlc"
    info = git_commit(corim)
    url = "https://github.com/ietf-rats-wg/draft-ietf-rats-corim"
    schemas = {}
    for kind, parts in corim_fragments(corim).items():
        header = (f"; CoRIM {kind} schema\n; Concatenation, in this order, of cddl/{' cddl/'.join(parts)}\n"
                  f"; from {url} at commit {info['commit']}.\n; The text below this header is unchanged.\n\n")
        body = "".join(read(corim / "cddl" / part).rstrip("\n") + "\n\n" for part in parts)
        schemas[kind] = schema_file(f"ietf/corim/{kind}.cddl", header + body)
    modules = {
        "rfc9393": schema_file("ietf/rfc9393.cddl", read(cddlc / "data" / "rfc9393.cddl")),
        "rfc9164": schema_file("ietf/rfc9164.cddl", read(cddlc / "data" / "rfc9164.cddl")),
        "measured-component": schema_file(
            "ietf/measured-component-stub.cddl",
            "; A stand-in for the module `measured-component` of draft-ietf-rats-eat-measured-component,\n"
            "; which the CoRIM schemas import as `eatmc` and which is not bundled here. It gives\n"
            "; `eatmc.digest` the shape the CoRIM examples use ([alg-id, bytes]); nothing else of the\n"
            "; module is defined.\ndigest = [ alg: int / text, val: bytes ]\n")}
    constructs = ["syntax.modules", "syntax.sockets", "syntax.tag", "syntax.map", "syntax.group-choice",
                  "syntax.type-extend", "syntax.generics"]
    cases = []
    examples = corim / "cddl" / "examples"
    for kind, pattern in (("comid", "comid-*"), ("cotl", "cotl-*"), ("corim", "corim-*")):
        for path in sorted(examples.glob(pattern + ".diag")):
            try:
                data = edn_to_cbor(read(path))
            except Exception:
                continue  # needs a template or a tool this converter lacks
            cases.append(case(f"corim/{kind}/{path.stem}", "valid", f"cddl/examples/{path.name}",
                              data.hex(), schema=schemas[kind], constructs=constructs,
                              provenance="converted",
                              note="the example is EDN upstream, converted to CBOR with cbor-diag"))
    for kind in ("intrep-acs", "intrep-rel"):
        cases.append(case(f"corim/{kind}/schema", "schema-error",
                          "cddl/stagingarea.cddl names `dd`, which no fragment defines",
                          schema=schemas[kind], constructs=["semantic.names"], provenance="verbatim",
                          note="an undefined name in the upstream schema at this commit (RFC 8610 §2: "
                               "every name must be defined)"))
    base = lambda kind, name: bytes.fromhex(next(c["hex"] for c in cases if c["id"] == f"corim/{kind}/{name}"))
    comid = cbor_api.decode(base("comid", "comid-1"))
    signed = cbor_api.decode(base("corim", "corim-1"))

    def mutate(value, fn):
        import copy
        copy_ = copy.deepcopy(value)
        return fn(copy_) or copy_

    def drop(key):
        return lambda m: m.pop(key)

    derived = [
        ("comid", "comid-1-no-tag-identity", comid, drop(1), "concise-mid-tag requires `tag-identity` (1)"),
        ("comid", "comid-1-no-triples", comid, drop(4), "concise-mid-tag requires `triples` (4)"),
        ("comid", "comid-1-unknown-key", comid, lambda m: m.update({99: 1}),
         "the map is closed: `$$concise-mid-tag-extension` has no extensions"),
        ("comid", "comid-1-tag-id-int", comid, lambda m: m[1].update({0: 5}), "`tag-id` is text or a UUID"),
        ("comid", "comid-1-role-unknown", comid, lambda m: m[2][0].update({2: [9]}),
         "`comid-role-type-choice` has roles 0, 1 and 2"),
        ("comid", "comid-1-entities-empty", comid, lambda m: m.update({2: []}),
         "`entities` is `[+ comid-entity-map]`"),
    ]
    for kind, name, value, fn, why in derived:
        cases.append(case(f"corim/{kind}/{name}", "invalid", f"cddl/examples/comid-1.diag, then: {why}",
                          encode(mutate(value, fn)).hex(), schema=schemas[kind], constructs=constructs,
                          provenance="derived"))
    cases.append(case("corim/corim/corim-1-other-tag", "invalid",
                      "cddl/examples/corim-1.diag with its tag number changed to 599",
                      encode(CBORTag(599, signed.value)).hex() if isinstance(signed, CBORTag) else
                      encode(signed).hex(), schema=schemas["corim"], constructs=constructs,
                      provenance="derived"))
    return {"id": "corim", "title": "CoRIM and CoMID (draft-ietf-rats-corim)", "modules": modules,
            "sources": [{"name": "CoRIM schema fragments and examples", "url": url, **info,
                         "license": IETF_NOTE, "path": "cddl/"},
                        source("RFC 9393 and RFC 9164 CDDL, extracted by cddlc",
                               "https://github.com/cabo/cddlc", cddlc, "MIT (the extraction); " + IETF_NOTE,
                               "data/")],
            "provenance": "verbatim", "cases": cases}


# ------------------------------------------------------------------ CoSWID

def build_coswid(sources: Path):
    cddlc = sources / "cddlc"
    schema = schema_file("ietf/rfc9393.cddl", read(cddlc / "data" / "rfc9393.cddl"))
    entity = {31: "ACME Inc.", 33: 1}
    minimal = {0: "example.acme.roadrunner-1.0", 12: 0, 1: "ACME RoadRunner", 2: entity, 6: {}}
    uuid_id = merged(minimal, {0: bytes(range(16))})
    constructs = ["syntax.map", "syntax.sockets", "syntax.generics", "syntax.group-choice",
                  "syntax.type-choice", "syntax.range"]
    rows = [
        ("minimal", "valid", minimal, "the required members and an empty payload"),
        ("tag-id-uuid", "valid", uuid_id, "`tag-id` may be a 16 byte string"),
        ("tag-id-bytes-15", "invalid", merged(minimal, {0: bytes(15)}), "`tag-id` is `text / bstr .size 16`"),
        ("evidence", "valid", {0: "t", 12: 0, 1: "n", 2: entity, 3: {}}, "evidence instead of payload"),
        ("no-payload-or-evidence", "valid", {0: "t", 12: 0, 1: "n", 2: entity},
         "`? payload-or-evidence` is optional"),
        ("both-payload-and-evidence", "invalid", merged(minimal, {3: {}}),
         "`payload-or-evidence` is one of the two"),
        ("no-tag-id", "invalid", {k: v for k, v in minimal.items() if k != 0}, "`tag-id` is required"),
        ("no-entity", "invalid", {k: v for k, v in minimal.items() if k != 2}, "`entity` is required"),
        ("software-name-int", "invalid", merged(minimal, {1: 5}), "`software-name` is text"),
        ("tag-version-text", "invalid", merged(minimal, {12: "1"}), "`tag-version` is an integer"),
        ("entities-array", "valid", merged(minimal, {2: [entity, {31: "Other", 33: [1, 2]}]}),
         "`one-or-more<entity-entry>`: an array of two or more"),
        ("entities-array-of-one", "invalid", merged(minimal, {2: [entity]}),
         "`one-or-more<T>` is `T / [2* T]`: an array of one is not allowed"),
        ("role-unknown-int", "valid", merged(minimal, {2: {31: "E", 33: 77}}), "`$role` ends with `int / text`"),
        ("role-missing", "invalid", merged(minimal, {2: {31: "E"}}), "`role` is required in an entity"),
        ("version-scheme-semver", "valid", merged(minimal, {14: 16384}), "`$version-scheme` accepts semver (16384)"),
        ("version-scheme-bytes", "invalid", merged(minimal, {14: b"x"}), "`$version-scheme` is an int or text"),
        ("link", "valid", merged(minimal, {4: {38: CBORTag(32, "https://acme.example/"), 40: 6}}),
         "a link entry with `href` (an `any-uri`, tag 32) and `rel`"),
        ("link-href-text", "invalid", merged(minimal, {4: {38: "https://acme.example/", 40: 6}}),
         "`any-uri = uri` is tag 32: a plain text string is not one"),
        ("link-no-href", "invalid", merged(minimal, {4: {40: 6}}), "`href` is required in a link"),
        ("payload-file", "valid", merged(minimal, {6: {17: {24: "rr.exe", 20: 1024, 7: [1, b"\x00" * 32]}}}),
         "a file with a name, a size and a hash"),
        ("payload-file-no-name", "invalid", merged(minimal, {6: {17: {20: 1024}}}), "`fs-name` is required in a file"),
        ("hash-wrong-shape", "invalid", merged(minimal, {6: {17: {24: "x", 7: [b"\x00" * 32]}}}),
         "`hash-entry` is `[hash-alg-id: int, hash-value: bytes]`"),
        ("corpus-float", "invalid", merged(minimal, {8: 1.5}),
         "`corpus` is a boolean, and a float is no global attribute either"),
        ("corpus-int-is-a-global-attribute", "valid", merged(minimal, {8: 1}),
         "a member without a cut does not claim its key (RFC 8610 §3.5.4): `8: 1` fails `corpus => bool` "
         "and matches `label => one-or-more<int>` of `any-attribute`"),
        ("global-attribute", "valid", merged(minimal, {15: "en", -5: "x"}),
         "`lang` and other labels (`global-attributes`)"),
        ("not-a-map", "invalid", [minimal], "a CoSWID tag is a map"),
    ]
    cases = [case(f"constructed/{name}", expect, f"RFC 9393 CDDL: {why}", encode(value).hex(),
                  constructs=constructs, provenance="constructed") for name, expect, value, why in rows]
    return {"id": "coswid-rfc9393", "title": "Concise Software Identification tags (RFC 9393)",
            "schema": schema,
            "sources": [source("RFC 9393 CDDL, extracted by cddlc", "https://github.com/cabo/cddlc", cddlc,
                               "MIT (the extraction); " + IETF_NOTE, "data/rfc9393.cddl")],
            "provenance": "constructed", "cases": cases}


# ------------------------------------------------------------------ EDHOC

EDHOC_ADAPTER = """\
; Adapter rules for the corpus, not part of RFC 9528. The CDDL of RFC 9528 defines the
; messages as groups, and EDHOC messages are CBOR sequences; a sequence is checked as
; the array of its items (`encoding: "sequence"`), so each message is wrapped in an array.
message_1_seq = [message_1]
message_2_seq = [message_2]
message_3_seq = [message_3]
message_4_seq = [message_4]
plaintext_2_seq = [PLAINTEXT_2]
plaintext_3_seq = [PLAINTEXT_3]
plaintext_4_seq = [PLAINTEXT_4]
error_seq = [error]
"""

EDHOC_ITEMS = {"message_1": "message_1_seq", "message_2": "message_2_seq", "message_3": "message_3_seq",
               "message_4": "message_4_seq", "PLAINTEXT_2": "plaintext_2_seq",
               "PLAINTEXT_3": "plaintext_3_seq", "PLAINTEXT_4": "plaintext_4_seq"}

# The invalid messages of the traces (RFC 9529, "Invalid Traces"): the CDDL's verdict on each.
# (title, schema root, what the CDDL says, deterministic, why)
EDHOC_INVALID = {
    "Surplus array encoding of message": ("message_1_seq", "invalid", False,
                                          "the message is one array, not a sequence"),
    "Surplus bstr encoding of connection identifier": ("message_1_seq", "valid", False,
                                                       "`C_I : bstr / -24..23` allows a byte string: "
                                                       "EDHOC's own rule against it is not in the CDDL"),
    "Surplus array encoding of ciphersuite": ("message_1_seq", "invalid", False,
                                              "`suites = [ 2* int ] / int`: an array of one is not allowed"),
    "Text string encoding of ephemeral key": ("message_1_seq", "invalid", False, "G_X is a byte string"),
    "Wrong number of CBOR sequence elements": ("message_2_seq", "invalid", False,
                                               "message_2 is one byte string"),
    "Surplus map encoding of ID_CRED field": ("plaintext_2_seq", "valid", False,
                                              "`ID_CRED_R : header_map / bstr / -24..23` allows a map: "
                                              "EDHOC's own rule against it is not in the CDDL"),
    "Surplus bstr encoding of ID_CRED field": ("plaintext_2_seq", "valid", False,
                                               "`ID_CRED_R` allows a byte string: EDHOC's own rule "
                                               "against it is not in the CDDL"),
    "Error in length of ephemeral key": ("message_1_seq", "valid", False,
                                         "the length of G_X depends on the cipher suite, not on the CDDL"),
    "Error in elliptic curve representation": ("message_1_seq", "valid", False,
                                               "a cryptographic check on G_X, not part of the CDDL"),
    "Error in elliptic curve point": ("message_1_seq", "valid", False,
                                      "a cryptographic check on G_X, not part of the CDDL"),
    "Curve point of low order": ("message_1_seq", "valid", False,
                                 "a cryptographic check on G_X, not part of the CDDL"),
    "Error in length of MAC": ("plaintext_2_seq", "valid", False,
                               "the length of the MAC depends on the cipher suite, not on the CDDL"),
    "Error in elliptic curve encoding": ("message_1_seq", "valid", False,
                                         "the leading zeros of G_X are a rule of the encoding of keys, "
                                         "not of the CDDL"),
    "Unnecessary long encoding": ("message_1_seq", "invalid", True,
                                  "valid CDDL, but not deterministically encoded (RFC 8949 §4.2.1), "
                                  "which EDHOC requires: checked with `require_deterministic`"),
    "Indefinite-length array encoding": ("message_1_seq", "invalid", True,
                                         "valid CDDL, but not deterministically encoded (RFC 8949 §4.2.1), "
                                         "which EDHOC requires: checked with `require_deterministic`"),
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_edhoc(sources: Path):
    edhoc, cddlc = sources / "edhoc", sources / "cddlc"
    rfc9528 = schema_file("ietf/rfc9528.cddl", read(cddlc / "data" / "rfc9528.cddl"))
    rfc9052 = schema_file("ietf/rfc9052.cddl", read(cddlc / "data" / "rfc9052.cddl"))
    adapter = schema_file("adapters/edhoc.cddl", EDHOC_ADAPTER)
    text = read(edhoc / "draft-ietf-lake-traces.md")
    constructs = ["syntax.group", "syntax.occurrence", "syntax.range", "syntax.type-choice"]
    cases = []

    def hex_of(block):
        return bytes.fromhex(re.sub(r"\s+", "", block))

    invalid_at = text.index("# Invalid Traces")
    traces = re.split(r"^# (Authentication with [^\n]*?)(?: #)?(?: \{#[^}]*\})?$", text[:invalid_at], flags=re.M)
    for number, (title, body) in enumerate(zip(traces[1::2], traces[2::2]), 1):
        for match in re.finditer(r"^~{8}\n(\w+) \(CBOR Sequence\) \(\d+ bytes?\)\n((?:[0-9a-fA-F ]+\n)*)~{8}$",
                                 body, re.M):
            name, block = match.groups()
            if name in EDHOC_ITEMS:
                count = sum(1 for c in cases if c["id"].startswith(f"trace-{number}/{name}"))
                suffix = "" if not count else f"-{count + 1}"
                cases.append(case(f"trace-{number}/{name}{suffix}", "valid",
                                  f"draft-ietf-lake-traces.md, trace {number} ({title.strip()}): {name}",
                                  hex_of(block).hex(), root=EDHOC_ITEMS[name], encoding="sequence",
                                  constructs=constructs, provenance="verbatim"))
    invalid = text[invalid_at:]
    seen = set()
    for match in re.finditer(r"^### ([^\n]+)\n(.*?)^~{8}\nInvalid (\w+) \(\d+ bytes?\)\n((?:[0-9a-fA-F ]+\n)*)~{8}$",
                             invalid, re.M | re.S):
        title, prose, name, block = match.groups()
        title = title.strip()
        root, expect, deterministic, why = EDHOC_INVALID[title]
        seen.add(title)
        reason = " ".join(prose.split())[:240]
        extra = {"deterministic": True} if deterministic else {}
        cases.append(case(f"invalid/{slug(title)}", expect,
                          f"draft-ietf-lake-traces.md, Invalid Traces: {title}", hex_of(block).hex(),
                          root=root, encoding="sequence",
                          constructs=constructs + (["cbor.canonical"] if deterministic else []),
                          provenance="verbatim", note=f"{why}. The trace says: {reason}", **extra))
        if deterministic:  # the same bytes, without the check: the CDDL alone accepts them
            cases.append(case(f"invalid/{slug(title)}-cddl-only", "valid",
                              f"draft-ietf-lake-traces.md, Invalid Traces: {title}", hex_of(block).hex(),
                              root=root, encoding="sequence", constructs=constructs, provenance="verbatim",
                              note="without `require_deterministic` only the CDDL is checked"))
    missing = set(EDHOC_INVALID) - seen
    if missing:
        raise SystemExit(f"edhoc: invalid traces not found: {sorted(missing)}")
    message_1 = cbor_api.decode_sequence(bytes.fromhex(
        next(c["hex"] for c in cases if c["id"] == "trace-1/message_1")))
    method, suites, g_x, c_i = message_1[:4]
    rows = [
        ("suites-array", "valid", [method, [2, 3], g_x, c_i], "`suites = [ 2* int ] / int`: two or more"),
        ("suites-array-of-one", "invalid", [method, [2], g_x, c_i], "an array of one suite is not `[ 2* int ]`"),
        ("method-text", "invalid", ["x", suites, g_x, c_i], "METHOD is an int"),
        ("no-c-i", "invalid", [method, suites, g_x], "C_I is required"),
        ("c-i-out-of-range", "invalid", [method, suites, g_x, 24], "`C_I : bstr / -24..23`"),
        ("c-i-bstr", "valid", [method, suites, g_x, b"\x01"], "`C_I : bstr / -24..23`"),
        ("ead", "valid", [method, suites, g_x, c_i, 1, b"\x00"], "`EAD_1` with a label and a value"),
        ("ead-label-only", "valid", [method, suites, g_x, c_i, -1], "`? ead_value`: the value is optional"),
        ("ead-label-text", "invalid", [method, suites, g_x, c_i, "x"], "`ead_label : int`"),
        ("empty-sequence", "invalid", [], "the message has four items at least"),
    ]
    for name, expect, items, why in rows:
        cases.append(case(f"constructed/message_1-{name}", expect, f"RFC 9528 CDDL: {why}",
                          encode_sequence(items).hex(), root="message_1_seq", encoding="sequence",
                          constructs=constructs, provenance="derived"))
    return {"id": "edhoc-rfc9528", "title": "EDHOC messages (RFC 9528, traces of RFC 9529)",
            "schema": [rfc9528, rfc9052, adapter],
            "sources": [source("EDHOC traces", "https://github.com/lake-wg/edhoc", edhoc,
                               IETF_NOTE, "draft-ietf-lake-traces.md"),
                        source("RFC 9528 and RFC 9052 CDDL, extracted by cddlc",
                               "https://github.com/cabo/cddlc", cddlc, "MIT (the extraction); " + IETF_NOTE,
                               "data/")],
            "provenance": "verbatim", "cases": cases}


# ------------------------------------------------------------------ SUIT

def build_suit(sources: Path):
    suit, cddlc = sources / "suit-manifest", sources / "cddlc"
    schema = schema_file("ietf/suit-manifest.cddl", read(suit / "draft-ietf-suit-manifest.cddl"))
    rfc9052 = schema_file("ietf/rfc9052.cddl", read(cddlc / "data" / "rfc9052.cddl"))
    constructs = ["syntax.group-choice", "syntax.occurrence", "syntax.sockets", "control.cbor",
                  "control.bits", "syntax.tag"]
    cases = []
    for path in sorted((suit / "examples").glob("example*.txt")):
        for index, match in enumerate(re.finditer(r"^(Envelope[^\n]*):\n\n~~~\n(.*?)\n~~~",
                                                  read(path), re.M | re.S), 1):
            title, block = match.groups()
            data = bytes.fromhex(re.sub(r"\s+", "", block))
            cases.append(case(f"suit/{path.stem}/{index}", "valid", f"examples/{path.name}: {title}",
                              data.hex(), constructs=constructs, provenance="verbatim"))
    first = bytes.fromhex(cases[0]["hex"])
    envelope = cbor_api.decode(first)
    cases.append(case("suit/other-tag", "invalid", "examples/example0.txt, with the envelope tag 107 changed to 108",
                      encode(CBORTag(108, envelope.value)).hex(), constructs=constructs,
                      provenance="derived"))
    cases.append(case("suit/truncated", "invalid", "examples/example0.txt, cut short",
                      first[:-3].hex(), constructs=constructs, provenance="derived"))
    return {"id": "suit-manifest", "title": "SUIT manifest envelopes (draft-ietf-suit-manifest)",
            "schema": [schema, rfc9052],
            "sources": [source("SUIT manifest schema and examples", "https://github.com/suit-wg/manifest-spec",
                               suit, IETF_NOTE),
                        source("RFC 9052 CDDL, extracted by cddlc (the COSE part of the manifest's schema)",
                               "https://github.com/cabo/cddlc", cddlc, "MIT (the extraction); " + IETF_NOTE,
                               "data/rfc9052.cddl")],
            "provenance": "verbatim", "cases": cases}


# ------------------------------------------------------------------ RFC 8610 sections

def build_rfc8610(sources: Path):
    """Cases written for this corpus, one schema per construct of RFC 8610, with the
    verdict the section gives. They are modelled on the sections named, not extracted
    from them."""
    tag32 = CBORTag(32, "https://example.org/")
    address = ("address = { delivery }\n"
               "delivery = (\n  street: tstr, ? number: uint, city //\n  po-box: uint, city //\n"
               "  per-pickup: true )\ncity = ( name: tstr, zip-code: uint )")
    city = {"name": "Mainz", "zip-code": 55116}
    groups = [
        ("§3.5.1 maps are closed", "syntax.map", "r = { name: tstr, ? age: uint }", [
            ({"name": "Ada"}, "valid", "the optional member may be absent"),
            ({"name": "Ada", "age": 36}, "valid", "both members"),
            ({"age": 36}, "invalid", "a required member is missing"),
            ({"name": "Ada", "x": 1}, "invalid", "a map lists every key it allows"),
            ({"name": 1}, "invalid", "the value has the wrong type"),
            ([], "invalid", "a map is not an array")]),
        ("§3.3 groups used in a map", "syntax.group", "person = { pii }\npii = (age: int, name: tstr)", [
            ({"age": 3, "name": "x"}, "valid", "the entries of the group are the entries of the map"),
            ({"age": 3}, "invalid", "an entry of the group is missing")]),
        ("§2.2.2 group choice (the postal address)", "syntax.group-choice", address, [
            ({"street": "Hegelstr.", **city}, "valid", "the first alternative"),
            ({"street": "Hegelstr.", "number": 4, **city}, "valid", "with its optional member"),
            ({"po-box": 5, **city}, "valid", "the second alternative"),
            ({"per-pickup": True}, "valid", "the third alternative has no city"),
            ({"per-pickup": True, **city}, "invalid", "the third alternative takes no city"),
            ({"per-pickup": False}, "invalid", "`per-pickup` is `true`"),
            ({"street": "x", "po-box": 1, **city}, "invalid", "alternatives do not mix"),
            ({"street": "x"}, "invalid", "the first alternative needs `city`")]),
        ("§2.2.2 type choice", "syntax.type-choice",
         'r = "bow tie" / "necktie" / "Internet attire"', [
             ("necktie", "valid", "a literal of the choice"),
             ("scarf", "invalid", "no literal matches"),
             (5, "invalid", "the wrong type")]),
        ("§2.2.2 numeric choice", "syntax.type-choice", "protocol = 6 / 17", [
            (6, "valid", "a literal of the choice"), (17, "valid", "a literal of the choice"),
            (7, "invalid", "no literal matches")]),
        ("§3.4 arrays: entries in order, with an optional entry before a repeated one", "syntax.array",
         "r = [ name: tstr, ? age: uint, * bool ]", [
             (["a"], "valid", "only the required entry"),
             (["a", 3], "valid", "the optional entry"),
             (["a", 3, True, False], "valid", "repeated entries"),
             (["a", True], "valid", "the optional entry is skipped and `* bool` takes the value"),
             ([3], "invalid", "the first entry is a text string"),
             ([], "invalid", "a required entry is missing")]),
        ("§3.2 occurrence bounds", "syntax.occurrence", "r = [ 2*3 int ]", [
            ([1], "invalid", "one is below the minimum"), ([1, 2], "valid", "the minimum"),
            ([1, 2, 3], "valid", "the maximum"), ([1, 2, 3, 4], "invalid", "above the maximum")]),
        ("§3.5.4 a cut", "syntax.cut", 'r = { ? "a" ^ => int, * tstr => any }', [
            ({"a": 1}, "valid", "the member matches"),
            ({}, "valid", "the member is optional"),
            ({"b": "x"}, "valid", "the wildcard takes other keys"),
            ({"a": "x"}, "invalid", "the cut stops the wildcard from claiming the key")]),
        ("§3.5.4 no cut", "syntax.map", 'r = { ? "a" => int, * tstr => any }', [
            ({"a": 1}, "valid", "the member matches"),
            ({"a": "x"}, "valid", "without a cut the wildcard may claim the key"),
            ({"a": 1, "b": "x"}, "valid", "both members")]),
        ("§3.7 unwrap", "syntax.unwrap", "pair = [int, int]\nr = [~pair, tstr]", [
            ([1, 2, "x"], "valid", "the entries of `pair` are spliced in"),
            ([[1, 2], "x"], "invalid", "the pair is not an element of its own")]),
        ("§2.2.2.2 choice from a group", "syntax.choice-from", "r = &(a: 1, b: 2)", [
            (1, "valid", "a value of the group"), (2, "valid", "a value of the group"),
            ("a", "invalid", "the names are not values")]),
        ("§3.8.3 integer range", "syntax.range", "r = 1..5", [
            (1, "valid", "inclusive lower bound"), (5, "valid", "inclusive upper bound"),
            (0, "invalid", "below"), (6, "invalid", "above"), (2.5, "invalid", "a float is not an integer")]),
        ("§3.8.3 exclusive range", "syntax.range", "r = 1...5", [
            (4, "valid", "inside"), (5, "invalid", "the upper bound is excluded")]),
        ("§3.8.3 float range", "syntax.range", "r = 0.0..1.0", [
            (0.5, "valid", "inside"), (1.5, "invalid", "above"), (1, "invalid", "an integer is not a float")]),
        ("§3.8.1 .size on a byte string", "control.size", "r = bstr .size (2..4)", [
            (b"ab", "valid", "two bytes"), (b"a", "invalid", "one byte"), (b"abcde", "invalid", "five bytes")]),
        ("§3.8.4 .cbor", "control.cbor", "r = bstr .cbor [int, int]", [
            (encode([1, 2]), "valid", "a byte string holding the array"),
            (encode([1, "x"]), "invalid", "the array inside does not match"),
            (b"\xff", "invalid", "not CBOR at all")]),
        ("§3.6 tags", "syntax.tag", "r = #6.32(tstr)", [
            (tag32, "valid", "tag 32 around text"), (CBORTag(32, 1), "invalid", "tag 32 around an int"),
            (CBORTag(33, "x"), "invalid", "another tag"), ("https://example.org/", "invalid", "no tag")]),
        ("§3.9 generics", "syntax.generics", "pair<K, V> = [K, V]\nr = pair<int, tstr>", [
            ([1, "a"], "valid", "`K` is int and `V` is tstr"), (["a", 1], "invalid", "the other way round")]),
        ("§3.9 sockets", "syntax.sockets", "$ext /= int\n$ext /= tstr\nr = $ext", [
            (1, "valid", "an extension"), ("a", "valid", "an extension"), (True, "invalid", "none extends it")]),
        ("§2.2.2 extending a type", "syntax.type-extend", "r = int\nr /= tstr", [
            (1, "valid", "the original"), ("a", "valid", "the extension"), (True, "invalid", "neither")]),
        ("§2.1 recursion", "syntax.recursion", "tree = [* tree]", [
            ([], "valid", "a leaf"), ([[], [[]]], "valid", "nested"), ([1], "invalid", "an int is not a tree")]),
        ("Appendix D time and tdate", "prelude.time", "r = time / tdate", [
            (CBORTag(1, 1363896240), "valid", "epoch time as an integer"),
            (CBORTag(1, 1363896240.5), "valid", "epoch time as a float"),
            (CBORTag(0, "2013-03-21T20:04:00Z"), "valid", "a date and time string"),
            (CBORTag(1, "x"), "invalid", "epoch time is a number"), (1363896240, "invalid", "untagged")]),
        ("§3.1 literals", "syntax.text-literal", 'r = "a" / 1 / true / null / h\'01\'', [
            ("a", "valid", "a text literal"), (1, "valid", "an integer literal"), (True, "valid", "true"),
            (None, "valid", "null"), (b"\x01", "valid", "a byte string literal"), (2, "invalid", "another int"),
            (False, "invalid", "false"), ("b", "invalid", "another text")]),
        ("§3.8.5 .and", "control.and", "r = uint .and (0..10)", [
            (7, "valid", "both match"), (11, "invalid", "outside the range"), (-1, "invalid", "not a uint")]),
        ("§3.8.1 .size on an integer", "control.size", "r = uint .size 1", [
            (255, "valid", "fits one byte"), (256, "invalid", "needs two bytes")]),
    ]
    cases = []
    for number, (title, construct, schema, rows) in enumerate(groups, 1):
        relative = schema_file(f"rfc8610/{number:02d}-{slug_(title)}.cddl", f"; {title}\n{schema}\n")
        root = "r" if re.search(r"^r\s*(=|/=)", schema, re.M) else re.match(r"[\w-]+", schema).group(0)
        if "<" in root:
            root = root.split("<")[0]
        for index, (value, expect, why) in enumerate(rows, 1):
            cases.append(case(f"rfc8610/{number:02d}/{index:02d}", expect,
                              f"RFC 8610 {title}: {why}", encode(value).hex(), schema=relative,
                              root=root, constructs=[construct], provenance="constructed"))
    return {"id": "rfc8610-sections", "title": "RFC 8610 sections: one schema per construct",
            "sources": [{"name": "RFC 8610", "url": "https://www.rfc-editor.org/rfc/rfc8610",
                         "license": "written for this corpus; modelled on the sections named",
                         "note": "not extracted from the RFC text"}],
            "provenance": "constructed", "cases": cases}


def slug_(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


BUILDERS = {"cddlc-tests": build_cddlc, "rfc8610-sections": build_rfc8610, "cose-rfc9052": build_cose, "corim": build_corim,
            "coswid-rfc9393": build_coswid, "edhoc-rfc9528": build_edhoc, "suit-manifest": build_suit}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--only", nargs="*", choices=sorted(BUILDERS))
    args = parser.parse_args()
    for name, builder in BUILDERS.items():
        if args.only and name not in args.only:
            continue
        write_suite(builder(args.sources))


if __name__ == "__main__":
    main()
