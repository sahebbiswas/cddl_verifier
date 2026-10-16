"""``scripts/benchmark.py`` (#85) keeps working: every case builds, checks its own result and times."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("benchmark_script", ROOT / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


class Cases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = benchmark.run(quick=True)

    def test_every_case_runs_once_at_its_smallest_size(self):
        self.assertEqual([r["name"] for r in self.results], [c.name for c in benchmark.CASES])
        for row in self.results:
            self.assertGreater(row["min_ms"], 0, row["name"])
            self.assertEqual(row["runs"], 1)

    def test_names_are_unique_and_each_area_has_cases(self):
        names = [c.name for c in benchmark.CASES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({c.area for c in benchmark.CASES},
                         {"parse", "resolve", "validate", "regexp", "cbor", "edn"})

    def test_a_case_that_gives_the_wrong_result_is_an_error_not_a_timing(self):
        validator = benchmark.Validator("r = uint\n")
        with self.assertRaises(AssertionError):
            benchmark._ok(validator.validate("x").valid, "valid")

    def test_selecting_by_name(self):
        rows = benchmark.run(["regexp"], quick=True)
        self.assertTrue(rows)
        self.assertTrue(all("regexp" in r["name"] for r in rows))
        self.assertEqual(benchmark.run(["no such case"], quick=True), [])


class Reports(unittest.TestCase):
    def test_a_table_with_growth_and_a_comparison(self):
        rows = [{"name": "x", "n": 100, "area": "a", "min_ms": 1.0, "median_ms": 1.1, "runs": 5},
                {"name": "x", "n": 400, "area": "a", "min_ms": 16.0, "median_ms": 16.1, "runs": 5,
                 "growth": 2.0},
                {"name": "y", "n": None, "area": "a", "min_ms": 2.0, "median_ms": 2.0, "runs": 5}]
        baseline = {"x[100]": rows[0], "x[400]": dict(rows[1], min_ms=8.0)}
        text = benchmark.render(rows, baseline)
        self.assertIn("x[400]", text)
        self.assertIn("superlinear", text)
        self.assertIn("2.00x", text)
        self.assertIn("new", text)  # y has no baseline

    def test_json_then_compare_and_the_failure_threshold(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "base.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark.main(["--quick", "cbor.decode", "--json", str(path)]), 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({r["name"] for r in data["results"]},
                             {"cbor.decode", "cbor.decode-keep-encoding", "cbor.decode-deterministic"})
            with redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark.main(["--quick", "cbor.decode", "--compare", str(path),
                                                 "--fail-above", "1000"]), 0)
            for row in data["results"]:
                row["min_ms"] = 1e-9  # a baseline nothing can match
            path.write_text(json.dumps(data), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark.main(["--quick", "cbor.decode", "--compare", str(path),
                                                 "--fail-above", "2"]), 1)

    def test_list_and_profile(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(benchmark.main(["--list"]), 0)
        self.assertIn("validate.array-of-maps", out.getvalue())
        self.assertIn("cumulative", benchmark.profile("cbor.decode", 5))
        with self.assertRaises(SystemExit):
            benchmark.profile("no such case", 5)


if __name__ == "__main__":
    unittest.main()
