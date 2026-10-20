"""``strict_modules``: an import that cannot be resolved fails closed (#229)."""

import contextlib
import io
import json
import tempfile
import unittest
import warnings
from pathlib import Path

from cddl_verifier import SchemaError, Validator, schema_diagnostics, validate
from cddl_verifier._analyzer import main
from cddl_verifier._cddl.resolve import import_directives, parse_imports
from cddl_verifier.cbor import encode

ROOT = Path(__file__).resolve().parent.parent

PREFIXED = ";# import rfc9164 as cbor-ip\nhost = { ip: cbor-ip.ipv4-address }\n"
UNPREFIXED = ";# include missing-module\nhost = { ip: completely-typoed-type-name }\n"


def quietly(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


class TestDefaultIsUnchanged(unittest.TestCase):
    """Without the option an unresolved import still only warns, as documented in #121."""

    def test_prefixed_names_match_any_value(self):
        with self.assertWarns(UserWarning):
            result = validate(PREFIXED, encode({"ip": True}))
        self.assertTrue(result.valid)

    def test_unprefixed_import_turns_off_undefined_name_checks(self):
        self.assertTrue(quietly(validate, UNPREFIXED, encode({"ip": 12345})).valid)

    def test_strict_is_off_by_default_in_every_entry_point(self):
        quietly(Validator, PREFIXED)
        self.assertEqual(schema_diagnostics(PREFIXED), ())


class TestFailsClosed(unittest.TestCase):

    def test_prefixed_import(self):
        with self.assertRaises(SchemaError) as caught:
            validate(PREFIXED, encode({"ip": True}), strict_modules=True)
        self.assertIn("module 'rfc9164' (imported as 'cbor-ip') was not found", str(caught.exception))

    def test_unprefixed_include(self):
        with self.assertRaises(SchemaError) as caught:
            Validator(UNPREFIXED, strict_modules=True)
        self.assertIn("module 'missing-module' (imported without a prefix) was not found",
                      str(caught.exception))

    def test_no_warning_is_issued_instead(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with self.assertRaises(SchemaError):
                Validator(PREFIXED, strict_modules=True)

    def test_an_unused_import_is_an_error_too(self):
        with self.assertRaises(SchemaError):
            Validator(";# import nothere as zz\nr = uint", strict_modules=True)

    def test_a_module_that_is_found_passes(self):
        v = Validator(PREFIXED, modules={"rfc9164": "ipv4-address = bstr .size 4"},
                      strict_modules=True)
        self.assertTrue(v.validate(encode({"ip": b"abcd"})).valid)
        self.assertFalse(v.validate(encode({"ip": True})).valid)
        self.assertEqual(v.warnings, ())

    def test_a_module_found_on_the_search_path_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "rfc9164.cddl").write_text("ipv4-address = bstr .size 4", encoding="utf-8")
            v = Validator(PREFIXED, module_path=[directory], strict_modules=True)
            self.assertTrue(v.validate(encode({"ip": b"abcd"})).valid)

    def test_a_schema_without_imports_is_not_affected(self):
        self.assertTrue(Validator("r = uint", strict_modules=True).validate(encode(1)).valid)

    def test_the_bundled_corim_schema_names_what_is_missing(self):
        with self.assertRaises(SchemaError) as caught:
            Validator(ROOT / "cddl-schemas" / "unified.cddl", strict_modules=True)
        message = str(caught.exception)
        self.assertIn("unified.cddl", message)
        self.assertIn("'measured-component'", message)
        self.assertEqual({e.message.split("'")[1] for e in caught.exception.errors},
                         {"measured-component", "rfc9393", "rfc9164"})

    def test_a_module_that_cannot_load_its_own_import_fails_in_the_module(self):
        with self.assertRaises(SchemaError) as caught:
            Validator(";# import m as m\nr = m.t", modules={"m": ";# import q as q\nt = q.u"},
                      strict_modules=True)
        self.assertTrue(str(caught.exception).startswith("<module m>:1:1: module 'q'"))

    def test_the_imports_after_a_module_with_a_missing_import_are_reported_too(self):
        text = ";# import m as m\n;# import z as z\nr = m.t\n"
        with self.assertRaises(SchemaError) as caught:
            Validator(text, modules={"m": ";# import q as q\nt = q.u"}, strict_modules=True)
        errors = caught.exception.errors
        self.assertEqual([(e.source.name, e.line, e.column) for e in errors],
                         [("<module m>", 1, 1), (None, 2, 1)])
        self.assertIn("module 'q'", errors[0].message)
        self.assertIn("module 'z'", errors[1].message)
        self.assertIs(errors[0], caught.exception)
        found = schema_diagnostics(text, modules={"m": ";# import q as q\nt = q.u"}, strict_modules=True)
        self.assertEqual([d.schema_location for d in found], ["<module m>:1:1", "<schema>:2:1"])

    def test_a_missing_import_two_modules_share_is_reported_once(self):
        modules = {"a": ";# import shared as s\nx = uint", "b": ";# import shared as s\ny = uint",
                   "shared": ";# import gone as g\nt = uint"}
        with self.assertRaises(SchemaError) as caught:
            Validator(";# import a\n;# import b\nr = uint", modules=modules, strict_modules=True)
        errors = caught.exception.errors
        self.assertEqual([(e.source.name, e.line) for e in errors],
                         [("<module shared>", 1)])

    def test_missing_imports_at_three_levels_are_all_reported(self):
        modules = {"a": ";# import b\n;# import gone-a\nx = uint", "b": ";# import gone-b\ny = uint"}
        with self.assertRaises(SchemaError) as caught:
            Validator(";# import a\n;# import gone-root\nr = uint", modules=modules, strict_modules=True)
        self.assertEqual([e.message.split("'")[1] for e in caught.exception.errors],
                         ["gone-b", "gone-a", "gone-root"])

    def test_a_module_found_in_a_module_passes(self):
        v = Validator(";# import m as m\nr = m.t",
                      modules={"m": ";# import q as q\nt = q.u", "q": "u = uint"},
                      strict_modules=True)
        self.assertTrue(v.validate(encode(1)).valid)


class TestPosition(unittest.TestCase):
    """The error says which directive, by line and column of its ``;#``."""

    def test_line_and_column(self):
        with self.assertRaises(SchemaError) as caught:
            Validator("\n\n  ;# import gone as g\nr = uint\n", strict_modules=True)
        error = caught.exception
        self.assertEqual((error.line, error.column), (3, 3))
        self.assertEqual(error.span.end - error.span.start, len(";# import gone as g"))
        self.assertTrue(str(error).startswith("<schema>:3:3: "))

    def test_the_file_name_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.cddl"
            path.write_text(";# import gone\nr = uint\n", encoding="utf-8")
            with self.assertRaises(SchemaError) as caught:
                Validator(path, strict_modules=True)
        self.assertTrue(str(caught.exception).startswith(f"{path}:1:1: "))

    def test_the_search_path_is_named(self):
        with self.assertRaises(SchemaError) as caught:
            Validator(PREFIXED, module_path=["/no/such/dir"], strict_modules=True)
        self.assertIn("searched /no/such/dir", str(caught.exception))

    def test_every_unresolved_import_is_reported_once_in_order(self):
        text = ";# import a as x\n;# include b\n;# import a as x\n;# import c\n"
        with self.assertRaises(SchemaError) as caught:
            Validator(text, strict_modules=True)
        errors = caught.exception.errors
        self.assertEqual([(e.line, e.column) for e in errors], [(1, 1), (2, 1), (4, 1)])
        self.assertIs(errors[0], caught.exception)

    def test_directive_offsets(self):
        text = "x = 1\n\t;# include foo\n;# import bar as b ; trailing\n"
        found = import_directives(text)
        self.assertEqual([(kind, module, prefix) for kind, module, prefix, _, _ in found],
                         [("include", "foo", None), ("import", "bar", "b")])
        for (_, _, _, start, end), expected in zip(found, (";# include foo", ";# import bar as b")):
            self.assertEqual(text[start:end], expected)
        self.assertEqual(parse_imports(text), [("include", "foo", None), ("import", "bar", "b")])


class TestSchemaDiagnostics(unittest.TestCase):

    def test_one_diagnostic_per_unresolved_import(self):
        found = schema_diagnostics(";# import a as x\nr = x.t\n;# include b\n", strict_modules=True)
        self.assertEqual([d.code for d in found], ["schema", "schema"])
        self.assertEqual([(d.line, d.column) for d in found], [(1, 1), (3, 1)])
        self.assertEqual([d.schema_location for d in found], ["<schema>:1:1", "<schema>:3:1"])
        self.assertIn("module 'a'", found[0].message)
        self.assertIn("module 'b'", found[1].message)
        self.assertEqual(found[0].offset, 0)

    def test_nothing_when_the_modules_are_found(self):
        self.assertEqual(
            schema_diagnostics(PREFIXED, modules={"rfc9164": "ipv4-address = bstr .size 4"},
                               strict_modules=True), ())

    def test_other_schema_errors_are_reported_as_before(self):
        found = schema_diagnostics("r = { a: nope, b: missing }", strict_modules=True)
        self.assertEqual(len(found), 2)


class TestCli(unittest.TestCase):

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main(list(args))
            except SystemExit as exit_:
                code = exit_.code or 0
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "main.cddl").write_text(PREFIXED, encoding="utf-8")
        (root / "ok.cbor").write_bytes(encode({"ip": b"abcd"}))
        (root / "bad.cbor").write_bytes(encode({"ip": True}))
        self.schema, self.good, self.bad = (str(root / "main.cddl"), str(root / "ok.cbor"),
                                            str(root / "bad.cbor"))

    def test_without_the_flag_it_warns_and_accepts(self):
        code, out, err = self.run_cli(self.schema, self.bad)
        self.assertEqual(code, 0, err)
        self.assertIn("Warning: module 'rfc9164'", err)

    def test_with_the_flag_the_schema_fails_to_load(self):
        code, out, err = self.run_cli(self.schema, self.bad, "--strict-modules")
        self.assertEqual(code, 1)
        self.assertIn("Error loading CDDL file:", err)
        self.assertIn("main.cddl:1:1: module 'rfc9164' (imported as 'cbor-ip') was not found", err)
        self.assertNotIn("Warning:", err)

    def test_json_diagnostics(self):
        code, out, err = self.run_cli(self.schema, self.good, "--strict-modules",
                                      "--diagnostics", "json")
        self.assertEqual(code, 1)
        document = json.loads(out)
        self.assertFalse(document["valid"])
        (diagnostic,) = document["diagnostics"]
        self.assertEqual(diagnostic["code"], "schema")
        self.assertEqual((diagnostic["line"], diagnostic["column"]), (1, 1))

    def test_with_the_module_supplied_the_flag_passes(self):
        root = Path(self.directory.name)
        (root / "rfc9164.cddl").write_text("ipv4-address = bstr .size 4", encoding="utf-8")
        code, out, err = self.run_cli(self.schema, self.good, "--type", "host", "--strict-modules")
        self.assertEqual(code, 0, err)
        self.assertIn("Validation successful", out + err)
        code, out, err = self.run_cli(self.schema, self.bad, "--type", "host", "--strict-modules")
        self.assertNotEqual(code, 0)
        self.assertNotIn("Error loading CDDL file", err)

    def test_module_option_satisfies_the_flag(self):
        root = Path(self.directory.name)
        (root / "elsewhere.cddl").write_text("ipv4-address = bstr .size 4", encoding="utf-8")
        code, out, err = self.run_cli(self.schema, self.good, "--strict-modules",
                                      "--module", f"rfc9164={root / 'elsewhere.cddl'}")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
