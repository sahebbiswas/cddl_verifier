#!/usr/bin/env python3
"""
Schema problems as diagnostics (#156).

``schema_diagnostics`` returns what ``Validator`` raises ``SchemaError`` for, as
``Diagnostic`` values that render like any other finding, and with every
problem in what the rules contain instead of the first.
"""

import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:  # run from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import (Diagnostic, SchemaError, Validator, render_json, render_text,
                           schema_diagnostics)
from cddl_verifier._cddl import CDDLSemanticError, parse_cddl
from cddl_verifier._cddl.resolve import resolve, resolve_all


MANY = 'r = { a: nope, b: tstr .size "x", c: alsono }\nq = { z: missing }\nok = uint'


class TestSchemaDiagnostics(unittest.TestCase):

    def test_a_good_schema_has_none(self):
        self.assertEqual(schema_diagnostics("r = { a: uint, ? b: tstr }"), ())
        self.assertEqual(schema_diagnostics(""), ())

    def test_every_problem_in_the_rules_is_reported_in_source_order(self):
        found = schema_diagnostics(MANY)
        self.assertEqual([d.message for d in found], [
            "undefined name 'nope'",
            "the argument of '.size' must be an unsigned integer, not a text string",
            "undefined name 'alsono'",
            "undefined name 'missing'"])
        self.assertEqual([d.rule for d in found], ["r", "r", "r", "q"])

    def test_fields(self):
        diag = schema_diagnostics("r = { a: nope }")[0]
        self.assertIsInstance(diag, Diagnostic)
        self.assertEqual((diag.code, diag.category, diag.severity), ("schema", "schema", "error"))
        self.assertEqual((diag.line, diag.column), (1, 10))
        self.assertEqual(diag.schema_location, "<schema>:1:10")
        self.assertEqual((diag.offset, diag.end_offset), (9, 13))
        self.assertEqual("r = { a: nope }"[diag.offset:diag.end_offset], "nope")
        self.assertEqual(diag.path, ())
        self.assertIsNone(diag.hex)

    def test_position_on_a_later_line(self):
        text = "a = uint\n\nr = {\n  x: nope\n}"
        diag = schema_diagnostics(text)[0]
        self.assertEqual((diag.line, diag.column), (4, 6))
        self.assertEqual(text[diag.offset:diag.end_offset], "nope")

    def test_syntax_error_is_one_diagnostic_with_what_was_expected(self):
        found = schema_diagnostics("r = {")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].message, "expected '}', found end of input")
        self.assertEqual(found[0].expected, "'}'")
        self.assertEqual((found[0].line, found[0].column), (1, 6))

    def test_a_problem_in_the_names_is_reported_alone(self):
        found = schema_diagnostics("r = uint\nr = tstr\nq = { a: nope }")
        self.assertEqual(len(found), 1)
        self.assertIn("'r' is already defined", found[0].message)
        found = schema_diagnostics("a = b\nb = a\nq = { a: nope }")
        self.assertEqual(len(found), 1)

    def test_the_same_problem_is_not_reported_twice(self):
        found = schema_diagnostics("g = ( a: nope )\nr = { g, g }")
        self.assertEqual([d.message for d in found].count("undefined name 'nope'"), 1)

    def test_text_rendering_has_the_location(self):
        text = render_text(schema_diagnostics("r = { a: nope }"))
        self.assertEqual(text, "<schema>:1:10: undefined name 'nope'")

    def test_verbose_rendering(self):
        text = render_text(schema_diagnostics("r = { a: nope }"), verbose=True)
        self.assertEqual(text.splitlines(), [
            "error[schema]: undefined name 'nope'",
            "  at schema <schema>:1:10, offset 9",
            "  in rule r"])

    def test_json(self):
        document = json.loads(render_json(schema_diagnostics(MANY)))
        self.assertEqual(document["format_version"], 1)
        self.assertEqual(len(document["diagnostics"]), 4)
        first = document["diagnostics"][0]
        self.assertEqual((first["code"], first["line"], first["column"], first["rule"]),
                         ("schema", 1, 10, "r"))

    def test_file_name_is_in_the_location(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.cddl"
            path.write_text("r = { a: nope }\n", encoding="utf-8")
            diag = schema_diagnostics(path)[0]
        self.assertEqual(diag.schema_location, f"{path}:1:10")

    def test_unreadable_file_is_a_diagnostic_not_an_exception(self):
        found = schema_diagnostics(Path("/no/such/file.cddl"))
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].code, found[0].category), ("schema", "schema"))
        self.assertIn("cannot read CDDL schema", found[0].message)
        self.assertIsNone(found[0].offset)

    def test_module_problem_is_a_diagnostic(self):
        found = schema_diagnostics("r = coswid.x\n;# import mod as coswid",
                                   modules={"mod": "x = ["})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].category, "schema")

    def test_wrong_argument_type_still_raises(self):
        with self.assertRaises(TypeError):
            schema_diagnostics(42)

    def test_nothing_is_raised_for_the_schemas_a_validator_rejects(self):
        for text in (MANY, "r = {", "r = uint\nr = tstr"):
            with self.subTest(text=text):
                with self.assertRaises(SchemaError):
                    Validator(text)
                self.assertTrue(schema_diagnostics(text))


class TestResolverCollects(unittest.TestCase):

    def test_resolve_raises_the_first_problem_and_carries_all(self):
        with self.assertRaises(CDDLSemanticError) as caught:
            resolve(parse_cddl(MANY))
        error = caught.exception
        self.assertEqual(error.message, "undefined name 'nope'")
        self.assertEqual(len(error.errors), 4)
        self.assertIs(error.errors[0], error)
        self.assertEqual(error.rule, "r")
        self.assertEqual(str(error), "<schema>:1:10: undefined name 'nope'")  # unchanged

    def test_resolve_all(self):
        resolved, errors = resolve_all(parse_cddl(MANY))
        self.assertIsNone(resolved)
        self.assertEqual(len(errors), 4)
        resolved, errors = resolve_all(parse_cddl("r = uint"))
        self.assertIsNotNone(resolved)
        self.assertEqual(errors, ())

    def test_a_problem_that_stops_the_resolver_has_itself_only(self):
        with self.assertRaises(CDDLSemanticError) as caught:
            resolve(parse_cddl("r = uint\nr = tstr"))
        self.assertEqual(caught.exception.errors, (caught.exception,))

    def test_members_of_one_map_each_get_a_problem(self):
        _, errors = resolve_all(parse_cddl("r = { a: x1, b: x2, c: x3 }"))
        self.assertEqual([e.message for e in errors],
                         ["undefined name 'x1'", "undefined name 'x2'", "undefined name 'x3'"])

    def test_pickle_keeps_the_rule(self):
        with self.assertRaises(CDDLSemanticError) as caught:
            resolve(parse_cddl("r = { a: nope }"))
        again = pickle.loads(pickle.dumps(caught.exception))
        self.assertEqual((again.message, again.rule, again.line), ("undefined name 'nope'", "r", 1))


if __name__ == "__main__":
    unittest.main()
