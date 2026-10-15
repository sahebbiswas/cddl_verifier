#!/usr/bin/env python3
"""The getting-started guide (docs/GETTING_STARTED.md) says what the tool does: run it and check.

The guide's files (marked ``<!-- file: NAME -->``) are written to a directory, the broken
copies are made with the guide's own script, and every message the guide quotes must be
what the tool prints now.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import json
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "NO_COLOR": "1"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def guide_files():
    """``{name: text}`` for every fenced block that follows a ``<!-- file: NAME -->`` marker."""
    return {name: body for name, body in
            re.findall(r"<!-- file: (\S+) -->\n```\w*\n(.*?)```", GUIDE, re.S)}


def run(*args, cwd):
    done = subprocess.run([sys.executable, *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    return done.returncode, ANSI.sub("", done.stdout), ANSI.sub("", done.stderr)


class Guide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = cls._tmp.name
        for name, text in guide_files().items():
            Path(cls.dir, name).write_text(text, encoding="utf-8")
        code, out, err = run("-m", "cddl_verifier.json_codec", "to-cbor", "good.json", "good.cbor", cwd=cls.dir)
        assert code == 0, err
        code, out, err = run("make_cases.py", cwd=cls.dir)
        assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def verify(self, name, *flags):
        return run("-m", "cddl_verifier", "device.cddl", f"{name}.cbor", "--type", "device-report", "-o",
                   os.devnull, *flags, cwd=self.dir)

    def test_the_guide_has_its_files(self):
        self.assertEqual(set(guide_files()), {"device.cddl", "good.json", "make_cases.py"})

    def test_good_data_is_valid_and_prints_edn(self):
        code, out, err = run("-m", "cddl_verifier", "device.cddl", "good.cbor", "--type", "device-report",
                             cwd=self.dir)
        self.assertEqual(code, 0, err)
        for line in ('/ device-report / {', '  "name": "edge-01",', '  "status": 0,',
                     '  "issued": / #6.1(uint) / 1(1760000000)'):
            self.assertIn(line, out)
            self.assertIn(line, GUIDE)

    def test_each_failure_says_what_the_guide_says(self):
        expected = {
            "wrongtype": ("type", "Type mismatch for field 'name' in 'device-report': "
                                  "expected tstr .size (1..32), got uint 7"),
            "badstatus": ("type", "Field 'status' in 'device-report' matches none of 0 / 1 / 2"),
            "toolong": ("value", "Field 'name' in 'device-report' violates .size: "
                                 "expected at most 32 UTF-8 bytes, got 40"),
            "badlabel": ("value", "Element [1] of field 'labels' 'Rack 7' does not match "
                                  ".regexp /[a-z][a-z0-9-]*/"),
            "badfw": ("value", "Field 'firmware' in 'device-report' violates .size: "
                               "expected exactly 4 bytes, got 3"),
            "missing": ("missing", "Missing required field 'firmware' (key firmware) in type 'device-report'"),
            "unknown": ("unknown", "Unknown field 'colour' not in schema for type 'device-report'"),
            "wrongtag": ("tag", "Field 'issued' in 'device-report' requires CBOR tag 1, got tag 0"),
            "notag": ("tag", "Field 'issued' in 'device-report' requires CBOR tag 1, but the data is not tagged"),
        }
        for name, (category, message) in expected.items():
            with self.subTest(name):
                code, out, err = self.verify(name)
                self.assertEqual(code, 1, err)
                self.assertIn("[FAIL] Validation failed:", err)
                self.assertIn(message, err)
                if name == "badstatus":  # the guide's table shortens the message
                    self.assertIn("matches none of 0 / 1 / 2 (0: expected 0, got 3;", GUIDE)
                    self.assertIn("matches none of 0 / 1 / 2 (0: expected 0, got 3;", err)
                elif name in ("wrongtype", "missing", "unknown", "multi"):
                    self.assertIn(message, GUIDE)
                code, out, err = self.verify(name, "--diagnostics", "json")
                report = json.loads(out)
                self.assertFalse(report["valid"])
                self.assertEqual([d["category"] for d in report["diagnostics"]], [category])

    def test_several_problems_are_reported_together(self):
        code, out, err = self.verify("multi")
        self.assertEqual(code, 1)
        for message in ("Type mismatch for field 'name' in 'device-report': expected tstr .size (1..32), got uint 7",
                        "Field 'firmware' in 'device-report' violates .size: expected exactly 4 bytes, got 3",
                        "Element [1] of field 'labels' 'No' does not match .regexp /[a-z][a-z0-9-]*/"):
            self.assertIn(message, err)
            self.assertIn(message, GUIDE)

    def test_the_json_report_has_the_documented_fields(self):
        code, out, err = self.verify("wrongtype", "--diagnostics", "json")
        report = json.loads(out)
        self.assertEqual((report["format_version"], report["valid"], report["root_type"]),
                         (1, False, "device-report"))
        diag = report["diagnostics"][0]
        self.assertEqual({key: diag[key] for key in ("category", "code", "path", "path_expr", "offset", "end_offset",
                                                     "hex", "rule", "schema_path", "schema_location", "expected",
                                                     "actual")},
                         {"category": "type", "code": "validation", "path": ["name"], "path_expr": "$.name",
                          "offset": 6, "end_offset": 7, "hex": "07", "rule": "device-report",
                          "schema_path": ["device-report", "name"], "schema_location": "device.cddl:2:1",
                          "expected": "tstr .size (1..32)", "actual": "uint 7"})

    def test_decode_failures(self):
        code, out, err = self.verify("trunc")
        self.assertEqual(code, 1)
        line = "Error decoding CBOR: Unexpected end of data: need 8 byte(s), 5 available (at offset 15)"
        self.assertIn(line, err)
        self.assertIn(line, GUIDE)
        code, out, err = self.verify("trailing")
        self.assertIn("Trailing data after CBOR item: 1 extra byte(s) (at offset 80)", err)
        code, out, err = self.verify("good", "--require-deterministic")
        self.assertEqual(code, 1)
        self.assertIn("map keys are not in bytewise order of their encodings (0x66737461747573 comes after "
                      "0x686669726d77617265) (at offset 28)", err)
        self.assertIn("(0x66737461747573 comes after 0x686669726d77617265) (at offset 28)", GUIDE)

    def test_a_schema_that_cannot_be_loaded(self):
        Path(self.dir, "broken.cddl").write_text(
            "device-report = {\n  name: tstr,\n  status: statuss,\n  ? tags: [* tstr .size \"x\"],\n}\n")
        code, out, err = run("-m", "cddl_verifier", "broken.cddl", "good.cbor", "--type", "device-report",
                             cwd=self.dir)
        self.assertEqual(code, 1)
        for line in ("broken.cddl:3:11: undefined name 'statuss'",
                     "broken.cddl:4:25: the argument of '.size' must be an unsigned integer, not a text string"):
            self.assertIn(line, err)
            self.assertIn(line, GUIDE)

    def test_without_a_type_failures_are_warnings(self):
        code, out, err = run("-m", "cddl_verifier", "device.cddl", "wrongtype.cbor", "-o", os.devnull, cwd=self.dir)
        self.assertEqual(code, 0)
        self.assertIn("Type mismatch for field 'name'", err)

    def test_show_types(self):
        code, out, err = run("-m", "cddl_verifier", "device.cddl", "good.cbor", "--show-types", cwd=self.dir)
        text = out + err
        for line in ("name: name -> tstr .size (1..32)", "uptime: uptime -> uint (optional)",
                     "issued: issued -> #6.1(uint) (optional)"):
            self.assertIn(line, text)
            self.assertIn(line, GUIDE)

    def test_the_diagnostic_dump_offset(self):
        code, out, err = run("-c", "import sys; from cddl_verifier.cbor import diag_dump; "
                                   "print(diag_dump(open(sys.argv[1], 'rb').read()))", "badstatus.cbor",
                             cwd=self.dir)
        self.assertIn("0023:   03                              # val: uint(3)", out)
        self.assertIn("0023:   03                              # val: uint(3)", GUIDE)

    def test_the_python_snippets(self):
        script = (Path(self.dir) / "triage.py")
        block = re.search(r"```python\n(from collections import Counter.*?)```", GUIDE, re.S).group(1)
        script.write_text(block, encoding="utf-8")
        code, out, err = run("triage.py", cwd=self.dir)
        self.assertEqual(code, 0, err)
        quoted = re.search(r"```text\n(good: valid=True.*?)```", GUIDE, re.S).group(1)
        self.assertEqual(out, quoted)
        block = re.search(r"```python\n(from pathlib import Path\nfrom cddl_verifier import validate, Validator.*?)```",
                          GUIDE, re.S).group(1)
        Path(self.dir, "api.py").write_text(block, encoding="utf-8")
        code, out, err = run("api.py", cwd=self.dir)
        self.assertEqual(code, 0, err)
        quoted = re.search(r"```text\n(False\nvalidation type.*?)```", GUIDE, re.S).group(1)
        self.assertEqual(out, quoted)


if __name__ == "__main__":
    unittest.main()
