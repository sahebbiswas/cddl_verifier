#!/usr/bin/env python3
"""Differential testing against an independent CDDL implementation (#81).

``scripts/differential.py`` validates the conformance corpus (and mutants of
it) with this library and with ``pycddl`` (the Rust ``cddl`` crate), and
``docs/divergences.json`` explains every disagreement. The test that needs
``pycddl`` is skipped without it (``pip install pycddl``); the rest checks the
record, the policy and the tools, and pins the minimized regression cases
against this library alone.
"""

import importlib.util
import json
import logging
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator, cbor


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "differential", ROOT / "scripts" / "differential.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["differential"] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()


class Quiet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        warnings.simplefilter("ignore")

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)
        warnings.resetwarnings()


class Record(Quiet):
    def test_entries_are_well_formed(self):
        record = tool.load_record()
        _, _, malformed = tool.evaluate([], record)
        self.assertEqual(malformed, [])

    def test_our_bugs_link_open_matrix_issues(self):
        matrix = json.loads(tool.MATRIX.read_text(encoding="utf-8"))
        tracked = {issue for row in matrix["rows"] for issue in row.get("issues", ())}
        for entry in tool.load_record()["entries"]:
            if entry["cause"] == "our-bug":
                with self.subTest(entry=entry["reason"][:50]):
                    self.assertIn(entry["issue"], tracked,
                                  "the conformance matrix should list the issue that tracks this")

    def test_rows_exist(self):
        matrix = json.loads(tool.MATRIX.read_text(encoding="utf-8"))
        ids = {row["id"] for row in matrix["rows"]}
        for entry in tool.load_record()["entries"]:
            rows = entry.get("row", [])
            for row in rows if isinstance(rows, list) else [rows]:
                self.assertIn(row, ids)

    def test_fixtures_hold_for_this_library(self):
        """The minimized cases state what the RFC says; this library agrees."""
        fixtures = [e for e in tool.load_record()["entries"] if e.get("fixture")]
        self.assertGreaterEqual(len(fixtures), 10)
        for entry in fixtures:
            with self.subTest(schema=entry["schema"], data=entry["data"]):
                raw = bytes.fromhex(entry["data"])
                cbor.decode(raw)  # a well-formed item
                ok = Validator(entry["schema"]).validate(raw).valid
                self.assertEqual("valid" if ok else "invalid", entry["expected"])


class Policy(Quiet):
    def divergence(self, **fields):
        base = {"row": "r.one", "schema": "r = int", "data": "00", "kind": "validation",
                "ours": "valid", "theirs": "invalid"}
        return {**base, **fields}

    def record(self, *entries):
        return {"entries": list(entries)}

    def entry(self, **fields):
        return {"cause": "their-bug", "reason": "why", **fields}

    def test_a_divergence_nobody_explained_fails(self):
        unexplained, stale, bad = tool.evaluate([self.divergence()], self.record())
        self.assertEqual((len(unexplained), stale, bad), (1, [], []))

    def test_an_entry_explains_what_it_names(self):
        found = [self.divergence(), self.divergence(data="01")]
        unexplained, stale, _ = tool.evaluate(found, self.record(self.entry(row="r.one")))
        self.assertEqual((unexplained, stale), ([], []))
        unexplained, _, _ = tool.evaluate(found, self.record(self.entry(row="r.one", data="00")))
        self.assertEqual([d["data"] for d in unexplained], ["01"])

    def test_fields_may_list_alternatives(self):
        found = [self.divergence(row="r.two")]
        unexplained, _, _ = tool.evaluate(found, self.record(self.entry(row=["r.one", "r.two"])))
        self.assertEqual(unexplained, [])

    def test_an_entry_that_matches_nothing_is_stale(self):
        _, stale, _ = tool.evaluate([], self.record(self.entry(row="r.one")))
        self.assertEqual(len(stale), 1)

    def test_a_fixture_is_not_stale(self):
        entry = self.entry(schema="r = int", data="00", expected="valid", fixture=True)
        self.assertEqual(tool.evaluate([], self.record(entry))[1], [])

    def test_entries_need_a_cause_a_reason_and_for_our_bugs_an_issue(self):
        for entry in ({"row": "r.one", "reason": "why"},
                      {"row": "r.one", "cause": "their-bug"},
                      {"row": "r.one", "cause": "our-bug", "reason": "why"},
                      {"row": "r.one", "cause": "nonsense", "reason": "why"}):
            with self.subTest(entry=entry):
                _, _, bad = tool.evaluate([], self.record(entry))
                self.assertTrue(bad)

    def test_expected_needs_the_case(self):
        _, _, bad = tool.evaluate([], self.record(self.entry(row="r.one", expected="valid")))
        self.assertTrue(bad)


class Tools(Quiet):
    def test_mutants_change_one_thing(self):
        variants = tool.mutants([1, "a"])
        self.assertIn([1], variants)
        self.assertIn(["a"], variants)
        self.assertIn([1, "a", "a"], variants)
        self.assertNotIn([1, "a"], variants)

    def test_mutants_of_maps_and_tags(self):
        self.assertIn({}, tool.mutants({1: 2}))
        self.assertIn({1: 2, 99999: 0}, tool.mutants({1: 2}))
        self.assertIn(5, tool.mutants((6, 5)))

    def test_shrinks_are_smaller_and_end(self):
        value = {1: [1, "abc"], 2: 300}
        seen = 0
        while True:
            smaller = [s for s in tool.shrinks(value) if s != value]
            if not smaller:
                break
            value, seen = smaller[0], seen + 1
            self.assertLess(seen, 50)
        self.assertEqual(value, {})

    def test_malformed_worker_output_is_a_crash(self):
        class Proc:
            def __init__(self, stdout, returncode=0):
                self.stdout, self.returncode = stdout, returncode
        good = '{"results": [{"valid": true}]}'
        self.assertEqual(tool._worker_outcome(Proc(good), 1), json.loads(good))
        self.assertEqual(tool._worker_outcome(Proc("warning\n" + good), 1), json.loads(good))
        self.assertEqual(tool._worker_outcome(Proc('{"parse_error": "x"}'), 3), {"parse_error": "x"})
        for bad in (Proc("not json"), Proc(""), Proc("[]"), Proc(good, returncode=-11),
                    Proc('{"results": []}')):
            with self.subTest(stdout=bad.stdout, rc=bad.returncode):
                self.assertIsNone(tool._worker_outcome(bad, 1))

    def test_corpus_covers_the_probes_and_has_no_duplicates(self):
        cases = tool.corpus()
        keys = [(c["schema"], c["data"]) for c in cases]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreater(len(cases), len(tool.corpus(with_mutants=False)))
        for case in cases[:50]:
            cbor.decode(bytes.fromhex(case["data"]))


@unittest.skipUnless(tool.available(), "pycddl is not installed (pip install pycddl)")
class Differential(Quiet):
    def test_every_divergence_is_explained_and_every_explanation_applies(self):
        found = tool.divergences(tool.corpus())
        unexplained, stale, malformed = tool.evaluate(found, tool.load_record())
        report = [tool._describe(d) for d in unexplained]
        report += [f"stale: {json.dumps(e)[:160]}" for e in stale]
        self.assertEqual((report, malformed), ([], []),
                         "explain new divergences in docs/divergences.json "
                         "(python scripts/differential.py report --untriaged)")

    def test_minimizing_keeps_the_disagreement(self):
        found = tool.divergences([{"row": "syntax.array", "schema": "r = [* int, tstr]",
                                   "data": "8301026161"}])
        self.assertEqual(len(found), 1)
        small = tool.minimize(found[0])
        self.assertEqual(small["data"], "8160")


if __name__ == "__main__":
    unittest.main()
