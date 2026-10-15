#!/usr/bin/env python3
"""The conformance corpus in tests/corpus (#80) behaves as recorded.

Every case is a schema, CBOR data, the verdict the source gives and where it came
from (docs/CORPUS.md). A case the library agrees with must keep passing. A case it
disagrees with is recorded as a ``gap`` (an issue), a ``divergence`` or a
``source-defect`` (a reason), and must keep disagreeing: fixing a gap fails this test
until the case is updated, so the record cannot go stale.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _corpus  # noqa: E402

ROOT = _corpus.ROOT
SUITES = _corpus.load_suites()
MATRIX = _corpus.matrix_rows()
CASES = list(_corpus.iter_cases(SUITES))


def renderer():
    spec = importlib.util.spec_from_file_location("render_corpus", ROOT / "scripts" / "render_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Format(unittest.TestCase):
    def test_suites_are_there(self):
        self.assertGreaterEqual(len(SUITES), 7)
        self.assertGreaterEqual(len(CASES), 500)

    def test_suites(self):
        for suite in SUITES:
            with self.subTest(suite=suite["id"]):
                self.assertEqual(suite["_path"].stem, suite["id"])
                self.assertTrue(suite["title"])
                self.assertTrue(suite["cases"])
                self.assertIn(suite["provenance"], _corpus.PROVENANCE)
                self.assertTrue(suite["sources"])
                for source in suite["sources"]:
                    for key in ("name", "url", "license"):
                        self.assertTrue(source.get(key), f"{suite['id']}: source without {key}")
                    if "commit" in source:  # a repository is pinned to what was copied
                        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
                        self.assertRegex(source["date"], r"^\d{4}-\d\d-\d\d$")
                    else:
                        self.assertTrue(source.get("note"), f"{suite['id']}: {source['name']} is not pinned")

    def test_case_ids_are_unique(self):
        for suite in SUITES:
            ids = [entry["id"] for entry in suite["cases"]]
            self.assertEqual(len(ids), len(set(ids)), suite["id"])

    def test_cases(self):
        for suite, entry in CASES:
            with self.subTest(case=f"{suite['id']}:{entry['id']}"):
                self.assertIn(entry["expect"], _corpus.EXPECTATIONS)
                self.assertTrue(entry["source"], "a case says where it came from")
                self.assertIn(_corpus.status_of(entry), _corpus.STATUSES)
                self.assertIn(entry.get("provenance", suite["provenance"]), _corpus.PROVENANCE)
                if entry["expect"] == "schema-error":
                    self.assertNotIn("hex", entry)
                else:
                    self.assertRegex(entry["hex"], r"^([0-9a-f]{2})*$")
                if _corpus.status_of(entry) != "pass":
                    self.assertTrue(entry.get("reason"), "a case the library disagrees with says why")
                if _corpus.status_of(entry) == "gap":
                    self.assertIsInstance(entry.get("issue"), int, "a gap names its issue")
                else:
                    self.assertNotIn("issue", entry)
                for row in entry.get("constructs", suite.get("constructs", [])):
                    self.assertIn(row, MATRIX, f"{row} is not a row of docs/conformance.json")

    def test_files_exist(self):
        for suite, entry in CASES:
            for path in _corpus.schema_paths(suite, entry):
                self.assertTrue((_corpus.CORPUS / path).is_file(), f"{entry['id']}: {path}")
            for _name, path in _corpus.module_paths(suite, entry):
                self.assertTrue((_corpus.CORPUS / path).is_file(), f"{entry['id']}: {path}")

    def test_every_construct_in_the_matrix_that_is_unsupported_is_named_by_a_gap_or_left_out(self):
        # A gap case must belong to a construct of the matrix that is unsupported or partial, or
        # be a library bug with an issue of its own; either way the matrix row (if any) says so.
        for suite, entry in CASES:
            if _corpus.status_of(entry) != "gap":
                continue
            rows = [MATRIX[row] for row in entry.get("constructs", [])]
            listed = any(row["status"] in ("unsupported", "partial") and entry["issue"] in row["issues"]
                         for row in rows)
            if not listed:
                # not a limitation the matrix records: it must be a bug, and say so
                self.assertIn("issue", entry)


class Cases(unittest.TestCase):
    def test_every_case_is_as_recorded(self):
        for suite, entry in CASES:
            with self.subTest(case=f"{suite['id']}:{entry['id']}"):
                verdict, detail = _corpus.run(suite, entry)
                self.assertFalse(verdict.startswith("crash:"), f"the library crashed: {verdict} {detail}")
                if _corpus.status_of(entry) == "pass":
                    self.assertEqual(
                        verdict, entry["expect"],
                        f"the source says {entry['expect']}, the library says {verdict}: {detail[:300]}")
                else:
                    self.assertNotEqual(
                        verdict, entry["expect"],
                        f"this {_corpus.status_of(entry)} case now agrees with the source: "
                        f"update scripts/build_corpus.py, rebuild, and render docs/CORPUS.md")

    def test_a_schema_that_is_refused_is_refused_with_a_message(self):
        for suite, entry in CASES:
            if entry["expect"] == "schema-error":
                verdict, detail = _corpus.run(suite, entry)
                self.assertEqual(verdict, "schema-error", entry["id"])
                self.assertTrue(detail)

    def test_schema_files_are_not_changed_by_a_line_ending(self):
        for path in sorted((_corpus.CORPUS / "schemas").rglob("*.cddl")):
            self.assertNotIn(b"\r", path.read_bytes(), f"{path} has a carriage return")


class Verdicts(unittest.TestCase):
    """The corpus is not vacuous: a mutant of a case flips its verdict."""

    def test_both_verdicts_occur_in_every_suite_that_has_data(self):
        for suite in SUITES:
            verdicts = {entry["expect"] for entry in suite["cases"]}
            with self.subTest(suite=suite["id"]):
                if suite["id"] != "suit-manifest":  # its two negatives are all it has
                    self.assertIn("valid", verdicts)
                self.assertIn("invalid", verdicts)

    def test_the_library_says_both_things(self):
        said = {}
        for suite, entry in CASES:
            said.setdefault(suite["id"], set()).add(_corpus.run(suite, entry)[0])
        for suite_id, verdicts in said.items():
            with self.subTest(suite=suite_id):
                self.assertTrue({"valid", "invalid"} <= verdicts or suite_id == "suit-manifest",
                                f"{suite_id}: {verdicts}")


class Documentation(unittest.TestCase):
    def test_rendered_document_is_current(self):
        rendered = renderer().render(SUITES, MATRIX)
        self.assertEqual(
            (ROOT / "docs" / "CORPUS.md").read_text(encoding="utf-8"), rendered,
            "docs/CORPUS.md is out of date: run `python scripts/render_corpus.py`")

    def test_sources_of_data_converted_from_edn_agree(self):
        try:
            import cbor_diag
        except ImportError:
            self.skipTest("cbor-diag is not installed")
        checked = 0
        for suite, entry in CASES:
            if "edn" in entry and "hex" in entry:
                result = cbor_diag.diag2cbor(entry["edn"])
                data = result.encode("latin1") if isinstance(result, str) else bytes(result)
                self.assertEqual(data.hex(), entry["hex"], entry["id"])
                checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
