"""The modules the package ships: ``rfc9393`` and ``rfc9164`` resolve without being supplied (#239)."""

import contextlib
import io
import tempfile
import unittest
import warnings
from pathlib import Path

from cddl_verifier import SchemaError, Validator, schema_diagnostics, validate
from cddl_verifier._analyzer import main
from cddl_verifier._cddl import modules
from cddl_verifier.cbor import encode

ROOT = Path(__file__).resolve().parent.parent
IP = ";# import rfc9164 as cbor-ip\nr = cbor-ip.ipv4-address\n"
COSWID = ";# import rfc9393 as coswid\nr = coswid.concise-swid-tag\n"
SWID = {0: "id", 12: 0, 1: "name", 2: {31: "entity", 33: 1}}


def quietly(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


class TestLicence(unittest.TestCase):
    """The bundled CDDL is IETF Trust text under the Revised BSD License, and ships with its notice."""

    MODULES = ROOT / "src" / "cddl_verifier" / "_modules"

    def test_the_package_declares_both_licences_and_ships_the_notice(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = "MIT AND BSD-3-Clause"', text)
        self.assertIn('"src/cddl_verifier/_modules/LICENSE-IETF.txt"', text)
        self.assertIn('"LICENSE"', text)

    def test_the_notice_names_each_rfc_with_its_year(self):
        notice = (self.MODULES / "LICENSE-IETF.txt").read_text(encoding="utf-8")
        for rfc, year in (("9393", "2023"), ("9164", "2021")):
            with self.subTest(rfc=rfc):
                self.assertIn(f"rfc{rfc}.cddl", notice)
                self.assertIn(f"RFC {rfc}", notice)
                self.assertIn(f"Copyright (c) {year} IETF Trust", notice)
        self.assertIn("persons identified as the authors of", notice)
        self.assertIn("Neither the name of Internet Society, IETF or IETF Trust", notice)

    def test_each_module_carries_its_own_copyright_line(self):
        for rfc, year in (("9393", "2023"), ("9164", "2021")):
            head = (self.MODULES / f"rfc{rfc}.cddl").read_text(encoding="utf-8")[:700]
            with self.subTest(rfc=rfc):
                self.assertIn(f"Copyright (c) {year} IETF Trust", head)
                self.assertIn("LICENSE-IETF.txt", head)


class TestBundled(unittest.TestCase):
    def test_the_modules_and_the_licence_are_shipped(self):
        directory = ROOT / "src" / "cddl_verifier" / "_modules"
        for name in modules.BUNDLED_MODULES:
            text = modules.bundled_module(name)
            self.assertIsNotNone(text, name)
            self.assertEqual(modules.bundled_module(name + ".cddl"), text)
            self.assertIn("Revised BSD", text)
            self.assertIn("LICENSE-IETF.txt", text)
        self.assertIn("Redistribution and use", (directory / "LICENSE-IETF.txt").read_text(encoding="utf-8"))
        self.assertIsNone(modules.bundled_module("measured-component"))
        self.assertIsNone(modules.bundled_module("../rfc9393"))

    def test_they_are_the_corpus_copies_of_the_rfcs(self):
        corpus = ROOT / "tests" / "corpus" / "schemas" / "ietf"
        for name in modules.BUNDLED_MODULES:
            shipped = modules.bundled_module(name)
            self.assertTrue(shipped.endswith((corpus / f"{name}.cddl").read_text(encoding="utf-8")), name)

    def test_packaging_includes_them(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("_modules/*.cddl", pyproject)
        self.assertIn("_modules", (ROOT / "MANIFEST.in").read_text(encoding="utf-8"))

    def test_an_import_resolves_without_a_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            v = Validator(IP)
        self.assertEqual(v.warnings, ())
        self.assertTrue(v.validate(encode(b"abcd")).valid)
        self.assertFalse(v.validate(encode(b"abc")).valid)
        self.assertFalse(v.validate(encode(True)).valid)

    def test_coswid_names_are_checked(self):
        v = Validator(COSWID)
        self.assertTrue(v.validate(encode(SWID)).valid)
        self.assertFalse(v.validate(encode({0: "id"})).valid)
        self.assertFalse(v.validate(encode({**SWID, 12: "one"})).valid)

    def test_strict_modules_accepts_them(self):
        Validator(IP + COSWID.split("\n", 1)[0] + "\n", strict_modules=True)
        self.assertEqual(schema_diagnostics(COSWID, strict_modules=True), ())

    def test_an_include_without_a_prefix(self):
        v = Validator(";# include rfc9164\nr = ipv4-address\n")
        self.assertTrue(v.validate(encode(b"abcd")).valid)

    def test_the_corim_schema_has_only_measured_component_missing(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Validator(ROOT / "cddl-schemas" / "unified.cddl")
        messages = [str(w.message) for w in caught]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("measured-component", messages[0])


class TestOrder(unittest.TestCase):
    """What the caller supplies wins over what is shipped."""

    def test_explicit_modules_win(self):
        v = Validator(IP, modules={"rfc9164": "ipv4-address = tstr"})
        self.assertTrue(v.validate(encode("text")).valid)
        self.assertFalse(v.validate(encode(b"abcd")).valid)

    def test_the_search_path_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "rfc9164.cddl").write_text("ipv4-address = uint", encoding="utf-8")
            v = Validator(IP, module_path=[directory])
            self.assertTrue(v.validate(encode(5)).valid)
            self.assertFalse(v.validate(encode(b"abcd")).valid)

    def test_the_schemas_own_directory_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "rfc9164.cddl").write_text("ipv4-address = uint", encoding="utf-8")
            schema = Path(directory) / "main.cddl"
            schema.write_text(IP, encoding="utf-8")
            self.assertTrue(Validator(schema).validate(encode(5)).valid)

    def test_bundled_modules_false_gives_the_earlier_behaviour(self):
        with self.assertWarns(UserWarning):
            v = Validator(IP, bundled_modules=False)
        self.assertTrue(v.validate(encode(True)).valid)   # unchecked: matches any value
        with self.assertRaises(SchemaError):
            Validator(IP, bundled_modules=False, strict_modules=True)
        self.assertEqual(len(schema_diagnostics(IP, strict_modules=True, bundled_modules=False)), 1)
        self.assertTrue(validate(IP, encode(True), bundled_modules=False) is not None)

    def test_other_names_are_still_unresolved(self):
        with self.assertRaises(SchemaError):
            Validator(";# import not-shipped as n\nr = n.t\n", strict_modules=True)


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
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "main.cddl").write_text(IP, encoding="utf-8")
        (root / "bad.cbor").write_bytes(encode(True))
        self.schema, self.bad = str(root / "main.cddl"), str(root / "bad.cbor")

    def test_bundled_by_default(self):
        code, out, err = self.run_cli(self.schema, self.bad, "--type", "r", "--strict-modules")
        self.assertNotEqual(code, 0)           # the data is not an IPv4 address
        self.assertNotIn("Error loading CDDL file", err)
        self.assertNotIn("Warning", err)

    def test_the_flag_turns_it_off(self):
        code, out, err = self.run_cli(self.schema, self.bad, "--type", "r", "--no-bundled-modules")
        self.assertEqual(code, 0, err)         # unchecked names match anything
        self.assertIn("module 'rfc9164'", err)
        code, out, err = self.run_cli(self.schema, self.bad, "--no-bundled-modules", "--strict-modules")
        self.assertEqual(code, 1)
        self.assertIn("module 'rfc9164' (imported as 'cbor-ip') was not found", err)


if __name__ == "__main__":
    unittest.main()
