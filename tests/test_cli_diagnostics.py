"""``cddl-verify --diagnostics json`` and the ``path_expr`` notation (#145)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cddl_verifier import validate
from cddl_verifier.cli import main

SCHEMA = "doc = { 1: [* uint], name: tstr }\n"
GOOD = bytes.fromhex("a201820102 646e616d65 6178".replace(" ", ""))
NESTED_BAD = bytes.fromhex("a2018201 6178 646e616d65 6178".replace(" ", ""))
TWO_BAD = bytes.fromhex("a2018201 6178 646e616d65 05".replace(" ", ""))
TRUNCATED = bytes.fromhex("a2018201")


class CliCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)

    def write(self, name, content):
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def run_cli(self, schema, data, *flags, schema_name="s.cddl"):
        cddl = self.write(schema_name, schema)
        cbor = self.write("d.cbor", data)
        out, err = io.StringIO(), io.StringIO()
        status = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                main([str(cddl), str(cbor), *flags])
            except SystemExit as e:
                status = e.code or 0
        return status, out.getvalue(), err.getvalue()

    def report(self, schema, data, *flags):
        status, out, err = self.run_cli(schema, data, "--diagnostics", "json", *flags)
        return status, json.loads(out), out, err  # stdout must be only the document


class TestPathExpr(unittest.TestCase):
    def diags(self, schema, data):
        return validate(schema, data).diagnostics

    def test_array_index_then_text_key(self):
        diag = self.diags("r = [* { a: uint }]", bytes.fromhex("82a1616101a161616178"))[0]
        self.assertEqual(diag.path_expr, "$[1].a")
        self.assertEqual(diag.to_dict()["path_expr"], "$[1].a")
        self.assertEqual(diag.to_dict()["path"], [1, "a"])

    def test_integer_key_is_not_an_index(self):
        diag = self.diags(SCHEMA, NESTED_BAD)[0]
        self.assertEqual(diag.path, (1, 1))
        self.assertEqual(diag.path_expr, "$<key 1>[1]")

    def test_text_key(self):
        diag = self.diags(SCHEMA, TWO_BAD)[-1]
        self.assertEqual(diag.path_expr, "$.name")

    def test_text_key_that_is_not_a_name(self):
        data = bytes.fromhex("a1 63612062 05".replace(" ", ""))
        diag = self.diags('r = { "a b": tstr }', data)[0]
        self.assertEqual(diag.path_expr, '$["a b"]')

    def test_other_key_types(self):
        cases = {
            "r = { h'ab' => tstr }": ("a141ab05", "$<key h'ab'>"),
            "r = { true => tstr }": ("a1f505", "$<key true>"),
            "r = { -1 => tstr }": ("a12005", "$<key -1>"),
        }
        for schema, (hexed, expected) in cases.items():
            with self.subTest(schema):
                diag = self.diags(schema, bytes.fromhex(hexed))[0]
                self.assertEqual(diag.path_expr, expected)

    def test_root_and_json_stay_plain(self):
        diag = self.diags("n = uint", bytes.fromhex("6178"))[0]
        self.assertEqual(diag.path_expr, "$")
        text = json.dumps(diag.to_dict())
        self.assertIn('"path_expr": "$"', text)

    def test_embedded_cbor_continues_the_path(self):
        data = bytes.fromhex("a1014482016178")
        diag = self.diags("r = { 1: bstr .cbor [* uint] }", data)[0]
        self.assertEqual(diag.path_expr, "$<key 1>[1]")

    def test_verbose_text_uses_the_notation(self):
        diag = self.diags(SCHEMA, NESTED_BAD)[0]
        self.assertIn("at data $<key 1>[1], offset 4..6", diag.format())


class TestJsonMode(CliCase):
    def test_valid_data_is_only_the_document(self):
        status, doc, out, err = self.report(SCHEMA, GOOD, "--type", "doc")
        self.assertEqual(status, 0)
        self.assertEqual(out, json.dumps(doc, indent=2) + "\n")
        self.assertEqual((doc["valid"], doc["root_type"], doc["diagnostics"]), (True, "doc", []))
        self.assertIn("[OK] Validation successful", err)
        self.assertNotIn("EDN Output", out)

    def test_nested_failure(self):
        status, doc, out, _ = self.report(SCHEMA, NESTED_BAD, "--type", "doc")
        self.assertEqual(status, 1)
        self.assertFalse(doc["valid"])
        (diag,) = doc["diagnostics"]
        self.assertEqual(diag["path"], [1, 1])
        self.assertEqual(diag["path_expr"], "$<key 1>[1]")
        self.assertEqual((diag["offset"], diag["end_offset"], diag["hex"]), (4, 6, "61 78"))
        self.assertEqual(diag["code"], "validation")

    def test_every_failure_in_schema_order(self):
        _, doc, _, _ = self.report(SCHEMA, TWO_BAD, "--type", "doc")
        self.assertEqual([d["path_expr"] for d in doc["diagnostics"]], ["$<key 1>[1]", "$.name"])

    def test_output_is_deterministic(self):
        runs = {self.run_cli(SCHEMA, TWO_BAD, "--diagnostics", "json", "--type", "doc")[1]
                for _ in range(3)}
        self.assertEqual(len(runs), 1)

    def test_decode_failure(self):
        status, doc, _, _ = self.report(SCHEMA, TRUNCATED, "--type", "doc")
        self.assertEqual(status, 1)
        (diag,) = doc["diagnostics"]
        self.assertEqual((diag["code"], diag["category"], diag["offset"]), ("decode", "decode", 4))
        self.assertEqual(diag["path_expr"], "$")
        self.assertFalse(doc["valid"])

    def test_missing_offsets_are_null(self):
        # a decoded Python value has no bytes, so no offsets, and the path is still there
        diag = validate(SCHEMA, {1: [1, "x"], "name": "x"}).diagnostics[0]
        as_dict = diag.to_dict()
        self.assertEqual((as_dict["offset"], as_dict["end_offset"], as_dict["hex"]),
                         (None, None, None))
        self.assertEqual(as_dict["path_expr"], "$<key 1>[1]")

    def test_members_in_schema_order_then_unknown_keys_in_data_order(self):
        # data keys: 9, name, 1, 7: the schema lists 1 before name
        data = bytes.fromhex("a4 09 00 646e616d65 05 01 8201 6178 07 00".replace(" ", ""))
        _, doc, _, _ = self.report(SCHEMA, data, "--type", "doc")
        self.assertEqual([d["path_expr"] for d in doc["diagnostics"]],
                         ["$<key 1>[1]", "$.name", "$<key 9>", "$<key 7>"])

    def test_hex_is_bounded(self):
        data = bytes([0x58, 40]) + bytes(40)  # a 40-byte string where uint is expected
        _, doc, _, _ = self.report("r = uint", data)
        diag = doc["diagnostics"][0]
        self.assertEqual(len(diag["hex"].split()), 16)
        self.assertTrue(diag["hex_truncated"])

    def test_schema_errors_are_all_reported(self):
        status, doc, _, _ = self.report("doc = { 1: foo, 2: bar }\n", GOOD)
        self.assertEqual(status, 1)
        self.assertEqual(len(doc["diagnostics"]), 2)
        self.assertEqual({d["code"] for d in doc["diagnostics"]}, {"schema"})
        self.assertEqual([d["line"] for d in doc["diagnostics"]], [1, 1])

    def test_syntax_error_is_a_schema_diagnostic(self):
        status, doc, _, _ = self.report("doc = {\n", GOOD)
        self.assertEqual(status, 1)
        self.assertEqual(doc["diagnostics"][0]["code"], "schema")

    def test_missing_files_are_diagnostics(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            main([str(self.root / "no.cddl"), str(self.root / "no.cbor"), "--diagnostics", "json"])
        self.assertEqual(raised.exception.code, 1)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["diagnostics"][0]["code"], "schema")
        self.write("s.cddl", SCHEMA)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main([str(self.root / "s.cddl"), str(self.root / "no.cbor"), "--diagnostics", "json"])
        self.assertEqual(json.loads(out.getvalue())["diagnostics"][0]["code"], "decode")

    def test_edn_only_with_output_file(self):
        target = self.root / "out.edn"
        status, doc, out, _ = self.report(SCHEMA, GOOD, "--type", "doc", "-o", str(target))
        self.assertEqual(status, 0)
        self.assertTrue(doc["valid"])
        self.assertIn("name", target.read_text(encoding="utf-8"))
        self.assertNotIn("name", out.replace('"name"', ""))  # EDN is not on stdout

    def test_no_edn_when_validation_fails(self):
        target = self.root / "out.edn"
        status, _, _, _ = self.report(SCHEMA, NESTED_BAD, "--type", "doc", "-o", str(target))
        self.assertEqual(status, 1)
        self.assertFalse(target.exists())

    def test_inferred_type_failures_are_warnings(self):
        status, doc, _, _ = self.report(SCHEMA, NESTED_BAD)
        self.assertEqual(status, 0)
        self.assertFalse(doc["valid"])
        self.assertEqual({d["severity"] for d in doc["diagnostics"]}, {"warning"})

    def test_verbose_logs_stay_off_stdout(self):
        _, doc, out, err = self.report(SCHEMA, NESTED_BAD, "--type", "doc", "--verbose")
        self.assertEqual(json.loads(out), doc)
        self.assertTrue(err)

    def test_show_types_is_refused(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_cli_raw = main([str(self.write("s.cddl", SCHEMA)), str(self.write("d.cbor", GOOD)),
                                     "--diagnostics", "json", "--show-types"])
        self.assertEqual(raised.exception.code, 2)

    def test_stdout_is_pure_in_a_real_process(self):
        cddl, cbor = self.write("s.cddl", SCHEMA), self.write("d.cbor", NESTED_BAD)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
        done = subprocess.run(
            [sys.executable, "-m", "cddl_verifier", str(cddl), str(cbor), "--type", "doc",
             "--diagnostics", "json", "--verbose"],
            capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 1)
        self.assertFalse(json.loads(done.stdout)["valid"])
        self.assertIn("VALIDATION FAILED", done.stderr)


class TestTextMode(CliCase):
    def test_default_is_unchanged(self):
        status, out, err = self.run_cli(SCHEMA, NESTED_BAD, "--type", "doc")
        self.assertEqual(status, 1)
        self.assertIn("[FAIL] Validation failed:", err)
        self.assertIn("  - Element [1] of field '1' has wrong type", err)
        self.assertEqual(out, "")

    def test_schema_problems_are_listed_on_stderr(self):
        status, out, err = self.run_cli("doc = { 1: foo, 2: bar }\n", GOOD)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("Error loading CDDL file:", err)
        self.assertIn("undefined name 'foo'", err)
        self.assertIn("undefined name 'bar'", err)


if __name__ == "__main__":
    unittest.main()
