#!/usr/bin/env python3
"""The conformance matrix (docs/conformance.json) matches what the code does (#86).

Every row names the tests that cover it, and its probes are run: a construct
marked ``supported`` must accept its valid examples and reject its invalid
ones, and a known gap (``accepted_but_invalid`` / ``rejected_but_valid``) must
still behave as recorded. Fixing a gap therefore fails this test until the
row is updated, so the matrix and docs/CONFORMANCE.md cannot drift from the
implementation. Constructs, controls and prelude names the library knows must
all have a row.
"""

import importlib.util
import json
import logging
import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "src"))

import unittest

from cddl_verifier import CBORDecodeError, SchemaError, Validator
from cddl_verifier import cbor as cbor_api
from cddl_verifier import json_codec
from cddl_verifier._cbor_values import UNDEFINED, CBORSimple, CBORTag
from cddl_verifier._cddl.prelude import parse_prelude
from cddl_verifier._cddl.resolve import KNOWN_CONTROLS

MATRIX = ROOT / "docs" / "conformance.json"
RENDERED = ROOT / "docs" / "CONFORMANCE.md"

STATUSES = {"supported", "partial", "unsupported", "out-of-scope"}
LAYER_STATUSES = {"supported", "partial", "unsupported", "n/a"}
PROBE_KEYS = {"valid", "invalid", "accepted_but_invalid", "rejected_but_valid"}


def load_matrix():
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def renderer():
    spec = importlib.util.spec_from_file_location(
        "render_conformance", ROOT / "scripts" / "render_conformance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def value(node):
    """A probe value from its JSON form (see docs/CONFORMANCE.md, "Updating")."""
    if isinstance(node, list):
        return [value(item) for item in node]
    if isinstance(node, dict):
        if "$bytes" in node:
            return bytes.fromhex(node["$bytes"])
        if "$undefined" in node:
            return UNDEFINED
        if "$simple" in node:
            return CBORSimple(node["$simple"])
        if "$float" in node:
            return float(node["$float"])
        if "$tag" in node:
            return CBORTag(node["$tag"], value(node["$value"]))
        if "$map" in node:
            return {key_of(value(k)): value(v) for k, v in node["$map"]}
        raise AssertionError(f"unknown probe value {node!r}")
    return node


def key_of(key):
    return tuple(key) if isinstance(key, list) else key


def validate(group, data):
    if isinstance(data, bytes):
        data = cbor_api.encode(data)  # bytes passed to validate() are CBOR, not a byte string
    validator = Validator(group["schema"], modules=group.get("modules"))
    return validator.validate(data, group.get("root")).valid


class Matrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        warnings.simplefilter("ignore")
        cls.matrix = load_matrix()
        cls.rows = cls.matrix["rows"]

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)
        warnings.resetwarnings()

    def test_rows_are_well_formed(self):
        ids = [row["id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate row ids")
        for row in self.rows:
            with self.subTest(row=row["id"]):
                self.assertIn(row["status"], STATUSES)
                self.assertEqual(list(row["layers"]), self.matrix["layers"][row["area"]])
                for layer, state in row["layers"].items():
                    self.assertIn(state, LAYER_STATUSES, layer)
                self.assertTrue(row["construct"] and row["spec"])
                self.assertTrue(row["tests"], "name the tests that cover this row")
                for test in row["tests"]:
                    self.assertTrue((ROOT / test).is_file(), f"{test} does not exist")

    def test_status_agrees_with_layers_and_evidence(self):
        for row in self.rows:
            with self.subTest(row=row["id"]):
                states = {s for s in row["layers"].values() if s != "n/a"}
                gaps = any(g.get(k) for g in row.get("probes", ())
                           for k in ("accepted_but_invalid", "rejected_but_valid"))
                if row["status"] == "supported":
                    self.assertEqual(states - {"supported"}, set(), "a supported row has no weak layer")
                    self.assertFalse(gaps, "a supported row has no known gap")
                else:
                    self.assertTrue(states - {"supported"} or row["status"] == "out-of-scope",
                                    "a row that is not supported has a weak layer")
                    self.assertTrue(row["notes"], "say what is missing")
                    if row["status"] != "out-of-scope":
                        self.assertTrue(row["issues"], "link the issue that tracks it")

    def test_supported_rows_have_examples_both_ways(self):
        for row in self.rows:
            if row["status"] != "supported" or row["area"] == "cbor":
                continue
            with self.subTest(row=row["id"]):
                probes = row.get("probes", ())
                if not probes and row.get("schema_errors"):
                    continue
                valid = [x for g in probes for x in g.get("valid", ())]
                invalid = [x for g in probes for x in g.get("invalid", ())]
                self.assertTrue(valid or invalid, "no probe")
                if row["id"] not in ("prelude.any", "syntax.comments"):
                    self.assertTrue(invalid, "no example the construct must reject")
                if row["id"] != "syntax.comments":
                    self.assertTrue(valid, "no example the construct must accept")

    def test_probes_behave_as_recorded(self):
        for row in self.rows:
            for index, group in enumerate(row.get("probes", ())):
                self.assertLessEqual(set(group) - {"schema", "root", "modules"}, PROBE_KEYS)
                for key, expected in (("valid", True), ("invalid", False),
                                      ("accepted_but_invalid", True), ("rejected_but_valid", False)):
                    for node in group.get(key, ()):
                        data = value(node)
                        with self.subTest(row=row["id"], group=index, kind=key, data=node):
                            actual = validate(group, data)
                            self.assertEqual(
                                actual, expected,
                                f"{row['id']}: {group['schema']!r} {key} {node!r} now "
                                f"{'accepted' if actual else 'rejected'}"
                                + (" - the gap is fixed: update the row" if key in PROBE_KEYS - {'valid', 'invalid'} else ""))

    def test_schema_errors_are_rejected(self):
        for row in self.rows:
            for schema in row.get("schema_errors", ()):
                with self.subTest(row=row["id"], schema=schema):
                    with self.assertRaises(SchemaError):
                        Validator(schema)

    def test_cbor_items(self):
        for row in self.rows:
            for item in row.get("cbor", ()):
                raw = bytes.fromhex(item["hex"])
                with self.subTest(row=row["id"], hex=item["hex"]):
                    if item["decode"] == "error":
                        with self.assertRaises(CBORDecodeError):
                            cbor_api.decode(raw)
                        continue
                    decoded = cbor_api.decode(raw)
                    if "roundtrip" in item:
                        self.assertEqual(cbor_api.encode(decoded) == raw, item["roundtrip"], "encode(decode(x))")
                    if "canonical" in item:
                        self.assertEqual(cbor_api.encode(decoded, canonical=True) == raw,
                                         item["canonical"], "canonical encoding")
                    if "json_roundtrip" in item:
                        text = json_codec.cbor_to_json(raw, typed=True)
                        self.assertEqual(json_codec.json_to_cbor(text) == raw, item["json_roundtrip"], "typed JSON")
                    if item.get("edn"):
                        self.assertTrue(Validator("r = any").to_edn(raw))


class Coverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_matrix()["rows"]

    def constructs(self, area):
        return [row["construct"] for row in self.rows if row["area"] == area]

    def test_every_control_has_a_row(self):
        listed = {row["id"].split(".", 1)[1] for row in self.rows if row["area"] == "control"}
        self.assertEqual(listed, set(KNOWN_CONTROLS))

    def test_every_prelude_name_has_a_row(self):
        names = {rule.name for rule in parse_prelude().rules}
        covered = {name for row in self.rows if row["area"] == "prelude" for name in row["covers"]}
        self.assertEqual(names - covered, set(), "prelude types without a row")
        self.assertEqual(covered - names, set(), "rows for types the prelude does not have")

    def test_issue_references_are_numbers(self):
        for row in self.rows:
            for issue in row["issues"]:
                self.assertIsInstance(issue, int)

    def test_rendered_document_is_current(self):
        rendered = renderer().render(load_matrix())
        self.assertEqual(
            RENDERED.read_text(encoding="utf-8"), rendered,
            "docs/CONFORMANCE.md is out of date: run `python scripts/render_conformance.py`")

    def test_documents_link_to_the_matrix(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue("CONFORMANCE.md" in readme, "README.md should link to docs/CONFORMANCE.md")
        support = (ROOT / "docs" / "CDDL_SUPPORT.md").read_text(encoding="utf-8")
        self.assertTrue("CONFORMANCE.md" in support, "CDDL_SUPPORT.md should link to the matrix")


if __name__ == "__main__":
    unittest.main()
