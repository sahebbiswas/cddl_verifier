#!/usr/bin/env python3
"""Loading imported CDDL modules (#121)."""

import contextlib
import io
import sys
import tempfile
import warnings
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, Validator, validate
from cddl_verifier._analyzer import main
from cddl_verifier.cbor import encode

LIB = """
;# import base as b
tag-id = b.small / tstr
item = { n: tag-id, ? kids: [* item] }
hdr = (version: uint .size 1)
pair<T> = [T, T]
"""
BASE = "small = uint .size 1"
MAIN = """
;# import lib as l
r = { id: l.tag-id, ? sub: [* l.item], pt: l.pair<uint>, l.hdr }
"""
GOOD = {"id": 5, "sub": [{"n": "x", "kids": [{"n": 3}]}], "pt": [1, 2], "version": 1}
MODULES = {"lib": LIB, "base": BASE}


def quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


class Linking(unittest.TestCase):
    def setUp(self):
        self.v = Validator(MAIN, modules=MODULES)

    def test_imported_rules_are_checked(self):
        self.assertTrue(self.v.validate(GOOD).valid)
        self.assertEqual(self.v.warnings, ())

    def test_rules_of_the_module_apply(self):
        for change in ({"id": 300}, {"sub": [{"n": [1]}]}, {"pt": [1, "a"]},
                       {"version": 999}, {"sub": [{"n": "x", "kids": [{"n": []}]}]}):
            with self.subTest(change=change):
                self.assertFalse(self.v.validate({**GOOD, **change}).valid)

    def test_group_from_module_is_required(self):
        data = dict(GOOD)
        del data["version"]
        self.assertFalse(self.v.validate(data).valid)

    def test_module_of_module_is_prefixed_again(self):
        self.assertIsNotNone(self.v._parser.resolved.lookup("l.b.small"))

    def test_module_rule_names_do_not_leak(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(MAIN + "\nx = item", modules=MODULES)
        self.assertIn("undefined name 'item'", str(cm.exception))

    def test_same_name_in_two_modules(self):
        schema = (";# import one as a\n;# import two as b\n"
                  "r = [a.t, b.t]")
        v = Validator(schema, modules={"one": "t = uint", "two": "t = tstr"})
        self.assertTrue(v.validate([1, "x"]).valid)
        self.assertFalse(v.validate(["x", 1]).valid)

    def test_include_and_unprefixed_import_merge_names(self):
        v = Validator(";# include extra\nr = [t]", modules={"extra": "t = uint"})
        self.assertTrue(v.validate([1]).valid)
        self.assertFalse(v.validate(["x"]).valid)
        self.assertEqual(v.warnings, ())

    def test_include_clashing_with_a_schema_rule(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(";# include extra\nr = t\nt = uint", modules={"extra": "t = tstr"})
        self.assertIn("also defines", str(cm.exception))

    def test_missing_rule_in_loaded_module_is_an_error(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(";# import lib as l\nr = l.nothing", modules=MODULES)
        self.assertIn("module 'lib' (imported as 'l') defines no rule 'nothing'",
                      str(cm.exception))

    def test_module_with_errors_names_its_file(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(MAIN, modules={"lib": "x = nowhere", "base": BASE})
        self.assertIn("<module lib>:1:5", str(cm.exception))

    def test_import_cycle(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(";# import a as a\nr = a.t",
                      modules={"a": ";# import b as b\nt = b.t", "b": ";# import a as a\nt = a.t"})
        self.assertIn("cycle", str(cm.exception))

    def test_prefix_for_two_modules_is_rejected(self):
        for modules in ({"one": "a = uint", "two": "b = uint"},
                        {"one": "a = uint"}):  # 'two' not found: still a clash
            with self.subTest(modules=sorted(modules)):
                with self.assertRaises(SchemaError) as cm:
                    quiet(Validator, ";# import one as p\n;# import two as p\nr = p.a",
                          modules=modules)
                self.assertIn("prefix 'p' is used for both module 'one' and module 'two'",
                              str(cm.exception))

    def test_same_module_imported_twice(self):
        v = Validator(";# import base as x\n;# import base as y\nr = [x.small, y.small]",
                      modules=MODULES)
        self.assertTrue(v.validate([1, 2]).valid)

    def test_generic_parameter_shadows_module_rule(self):
        v = Validator(";# import m as m\nr = m.box<tstr>",
                      modules={"m": "T = uint\nbox<T> = [T]"})
        self.assertTrue(v.validate(["x"]).valid)
        self.assertFalse(v.validate([1]).valid)

    def test_sockets_are_shared(self):
        v = Validator(";# import m as m\nr = m.t\n$$ext //= (b: tstr)",
                      modules={"m": "t = {$$ext}\n$$ext //= (a: int)"})
        self.assertTrue(v.validate({"a": 1}).valid)
        self.assertTrue(v.validate({"b": "x"}).valid)

    def test_socket_named_with_the_import_prefix(self):
        # The CoRIM schemas write `coswid.$version-scheme`; the module only
        # extends the socket (`$version-scheme /= ...`), it never defines it.
        module = "$scheme /= 1\n$scheme /= tstr\nthing = [scheme: $scheme]"
        v = Validator(";# import m as m\nr = [m.$scheme, m.thing]", modules={"m": module})
        self.assertTrue(v.validate([1, [1]]).valid)
        self.assertTrue(v.validate(["x", ["y"]]).valid)
        self.assertFalse(v.validate([2, [1]]).valid)
        self.assertFalse(v.validate([1, [2]]).valid)

    def test_socket_named_with_the_prefix_takes_the_schemas_extensions(self):
        v = Validator(";# import m as m\nr = m.$s\n$s /= bool", modules={"m": "$s /= uint"})
        for good in (1, True):
            self.assertTrue(v.validate(good).valid)
        self.assertFalse(v.validate("x").valid)

    def test_socket_the_module_does_not_have_is_an_error(self):
        with self.assertRaises(SchemaError) as cm:
            Validator(";# import m as m\nr = m.$nothing", modules={"m": "$s /= uint"})
        self.assertIn("undefined name 'm.$nothing'", str(cm.exception))

    def test_socket_through_a_module_of_a_module(self):
        modules = {"a": ";# import b as b\nt = b.$s", "b": "$s /= uint"}
        v = Validator(";# import a as a\nr = a.t", modules=modules)
        self.assertTrue(v.validate(3).valid)
        self.assertFalse(v.validate("x").valid)

    def test_rule_location_is_in_the_module_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "lib.cddl").write_text("\n\nt = uint\n", encoding="utf-8")
            schema = Path(d) / "main.cddl"
            schema.write_text(";# import lib as l\nr = l.t\n", encoding="utf-8")
            v = Validator(schema)
            self.assertEqual(v._parser.rule_location("l.t"), f"{Path(d) / 'lib.cddl'}:3:1")
            self.assertTrue(v._parser.rule_location("r").endswith("main.cddl:2:1"))


class Finding(unittest.TestCase):
    def test_module_path_and_schema_directory(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            (Path(d) / "lib.cddl").write_text(LIB, encoding="utf-8")
            (Path(d) / "base.cddl").write_text(BASE, encoding="utf-8")
            self.assertTrue(Validator(MAIN, module_path=[d]).validate(GOOD).valid)
            schema = Path(d) / "main.cddl"
            schema.write_text(MAIN, encoding="utf-8")
            self.assertTrue(Validator(schema).validate(GOOD).valid)
            # a directory that does not hold it
            v = quiet(Validator, MAIN, module_path=[e])
            self.assertTrue(v.warnings)

    def test_modules_may_be_paths(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.cddl").write_text("t = uint", encoding="utf-8")
            v = Validator(";# import x as x\nr = x.t", modules={"x": Path(d) / "x.cddl"})
            self.assertTrue(v.validate(1).valid)
            self.assertFalse(v.validate("a").valid)

    def test_validate_shorthand(self):
        self.assertTrue(validate(MAIN, GOOD, modules=MODULES).valid)

    def test_unreadable_module(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SchemaError):
                Validator(";# import x as x\nr = x.t", modules={"x": Path(d) / "gone.cddl"})


class NotFound(unittest.TestCase):
    def test_names_match_any_value_and_warn(self):
        with self.assertWarns(UserWarning) as cm:
            v = Validator(";# import nothere as zz\nr = [zz.a, zz.b]")
        self.assertIn("module 'nothere' (imported as 'zz') was not found", str(cm.warning))
        self.assertIn("zz.a, zz.b", v.warnings[0])
        self.assertTrue(v.validate([1, "x"]).valid)

    def test_unused_import_is_quiet(self):
        v = Validator(";# import nothere as zz\nr = uint")
        self.assertEqual(v.warnings, ())

    def test_unprefixed_import_warns(self):
        v = quiet(Validator, ";# include nothere\nr = typo")
        self.assertIn("without a prefix", v.warnings[0])
        self.assertTrue(v.validate(1).valid)

    def test_group_from_missing_module_leaves_a_map_open(self):
        v = quiet(Validator, ";# import m as m\nr = {a: int, m.g}")
        self.assertTrue(v.validate({"a": 1, "other": "x"}).valid)
        self.assertFalse(v.validate({"a": "x"}).valid)
        self.assertFalse(v.validate({"other": "x"}).valid)

    def test_what_a_module_could_not_load_is_unchecked_here_too(self):
        v = quiet(Validator, ";# import m as m\nr = m.t", modules={"m": ";# import q as q\nt = q.u"})
        self.assertTrue(v.validate(1).valid)
        self.assertTrue(v.warnings)


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main(list(args))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue() + err.getvalue()

    def test_cli_options(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "mods").mkdir()
            (root / "mods" / "lib.cddl").write_text(LIB, encoding="utf-8")
            (root / "elsewhere.cddl").write_text(BASE, encoding="utf-8")
            (root / "main.cddl").write_text(MAIN, encoding="utf-8")
            (root / "d.cbor").write_bytes(encode(GOOD))
            schema, data = str(root / "main.cddl"), str(root / "d.cbor")

            code, text = self.run_cli(schema, data)
            self.assertIn("Warning: module 'lib'", text)

            code, text = self.run_cli(schema, data, "-M", str(root / "mods"),
                                      "--module", f"base={root / 'elsewhere.cddl'}")
            self.assertEqual(code, 0, text)
            self.assertNotIn("Warning", text)
            self.assertIn("Validation successful", text)

            (root / "bad.cbor").write_bytes(encode({**GOOD, "id": 300}))
            code, text = self.run_cli(schema, str(root / "bad.cbor"), "--type", "r",
                                      "-M", str(root / "mods"),
                                      "--module", f"base={root / 'elsewhere.cddl'}")
            self.assertNotEqual(code, 0)

    def test_module_option_needs_a_file(self):
        code, text = self.run_cli("a.cddl", "b.cbor", "--module", "oops")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
