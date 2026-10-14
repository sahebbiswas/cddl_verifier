#!/usr/bin/env python3
"""
Findings from type expressions carry their own details (#153).

Controls, choices and the embedded CBOR and tag contents checked through a
type expression used to be reported as the caller's message only: no
``expected``/``actual`` for a control, the member's path instead of the
nested item's, and nothing of the alternatives of a choice.
"""

import json
import sys
import unittest
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:  # run from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import cbor, validate
from cddl_verifier.cbor import CBORTag
from cddl_verifier._diagnostics import MAX_ALTERNATIVES, render_json


def found(schema, data, root="r"):
    raw = cbor.encode(data)
    return raw, validate(schema, raw, root_type=root).diagnostics


def bytes_of(raw, diag):
    return raw[diag.offset:diag.end_offset]


class TestControlViolations(unittest.TestCase):

    def check(self, schema, data, expected, actual, path):
        raw, diags = found(schema, data)
        self.assertEqual(len(diags), 1, [d.message for d in diags])
        diag = diags[0]
        self.assertEqual(diag.category, "value")
        self.assertEqual((diag.expected, diag.actual), (expected, actual))
        self.assertEqual(diag.path, path)
        self.assertIsNotNone(diag.offset)
        return raw, diag

    def test_value_controls_say_what_they_wanted(self):
        for op, bound, value, wanted in (("le", 100, 200, "at most 100"),
                                         ("lt", 100, 100, "less than 100"),
                                         ("ge", 5, 1, "at least 5"),
                                         ("gt", 5, 5, "more than 5")):
            with self.subTest(op=op):
                self.check(f"r = {{ 0: uint .{op} {bound} }}", {0: value},
                           wanted, str(value), (0,))

    def test_range(self):
        _, diag = self.check("r = { 0: 1..5 }", {0: 9}, "a value in 1..5", "9", (0,))
        self.assertIn("9 is outside 1..5", diag.message)

    def test_regexp_as_member_and_as_element(self):
        self.check('r = { 0: tstr .regexp "a+" }', {0: "b"},
                   "text matching .regexp /a+/", '"b"', (0,))
        self.check('r = [* tstr .regexp "a+"]', ["a", "b"],
                   "text matching .regexp /a+/", '"b"', (1,))

    def test_size_and_bits(self):
        raw, diags = found("r = { 0: tstr .size 3 }", {0: "abcd"})
        self.assertEqual((diags[0].category, diags[0].expected, diags[0].actual),
                         ("value", "exactly 3 UTF-8 bytes", "4"))
        _, diag = self.check("r = { 0: uint .bits 3 }", {0: 4},
                             "only the bits .bits allows", "4", (0,))
        self.assertIn(".bits", diag.message)

    def test_root_value_and_array_element(self):
        self.check("r = uint .le 3", 9, "at most 3", "9", ())
        raw, diag = self.check("r = [* uint .le 3]", [1, 9], "at most 3", "9", (1,))
        self.assertEqual(bytes_of(raw, diag), b"\x09")

    def test_computed_key_value_is_pointed_at(self):
        raw, diag = self.check("r = { * tstr => uint .le 3 }", {"a": 9}, "at most 3", "9", ("a",))
        self.assertEqual(bytes_of(raw, diag), b"\x09")  # the value, not the map

    def test_messages_are_unchanged(self):
        _, diags = found("r = { 0: uint .le 100 }", {0: 200})
        self.assertEqual(diags[0].message, "Field '0' in 'r' 200 violates .le 100")


class TestChoices(unittest.TestCase):

    SCHEMA = "r = { 0: uint / tstr / {a: uint} }"

    def test_choice_expects_the_choice_and_has_the_value(self):
        raw, diags = found("r = { 0: uint / tstr }", {0: b"x"})
        diag = diags[0]
        self.assertEqual((diag.expected, diag.actual), ("uint / tstr", "bstr"))
        self.assertEqual(diag.path, (0,))
        self.assertTrue(diag.message.startswith("Field '0' in 'r' matches none of uint / tstr ("))

    def test_every_alternative_has_its_own_finding(self):
        raw, diags = found(self.SCHEMA, {0: {"a": "x"}})
        diag = diags[0]
        self.assertEqual(diag.expected, "uint / tstr / map")  # not the internal 'r@0@2'
        self.assertEqual([(a.expected, a.actual) for a in diag.alternatives],
                         [("uint", "map"), ("tstr", "map"), ("uint", "tstr")])
        self.assertEqual([a.message.split(":")[0] for a in diag.alternatives],
                         ["uint", "tstr", "map"])

    def test_alternatives_point_at_the_nested_item(self):
        raw, diags = found(self.SCHEMA, {0: {"a": "x"}})
        first, _, third = diags[0].alternatives
        self.assertEqual(first.path, (0,))
        self.assertEqual(third.path, (0, "a"))
        self.assertEqual(bytes_of(raw, third), cbor.encode("x"))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode({"a": "x"}))

    def test_no_alternatives_for_other_findings(self):
        _, diags = found("r = { 0: uint .le 1 }", {0: 5})
        self.assertEqual(diags[0].alternatives, ())

    def test_alternatives_in_json_and_text(self):
        _, diags = found(self.SCHEMA, {0: {"a": "x"}})
        document = json.loads(render_json(diags))
        alternatives = document["diagnostics"][0]["alternatives"]
        self.assertEqual(len(alternatives), 3)
        self.assertEqual(alternatives[2]["path"], [0, "a"])
        self.assertEqual(alternatives[0]["alternatives"], [])
        text = diags[0].format()
        self.assertIn("alternative: error[type]: uint: expected uint", text)
        self.assertIn("at data /0/a", text)

    def test_large_choice_lists_a_bounded_number(self):
        count = MAX_ALTERNATIVES + 10
        schema = "r = { 0: " + " / ".join(f'"v{i}"' for i in range(count)) + " }"
        _, diags = found(schema, {0: "other"})
        self.assertEqual(len(diags[0].alternatives), MAX_ALTERNATIVES)
        self.assertIn(f'"v{count - 1}"', diags[0].message)  # the message still names all of them

    def test_paren_choice_with_a_control(self):
        _, diags = found("r = { 0: (uint / tstr) .size 1 }", {0: b"x"})
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].expected, "(uint / tstr) .size 1")

    def test_tag_choice_keeps_its_category(self):
        _, diags = found("r = { 0: #6.5(uint) / tstr }", {0: b"x"})
        self.assertEqual(diags[0].category, "tag")
        self.assertEqual(diags[0].alternatives[0].category, "tag")
        self.assertEqual(diags[0].alternatives[0].expected, "tag 5")


class TestNestedFindings(unittest.TestCase):

    def test_embedded_cbor_in_array_elements(self):
        schema = "r = [* bstr .cbor inner]\ninner = {0: uint}"
        raw, diags = found(schema, [cbor.encode({0: 1}), cbor.encode({0: "no"})])
        self.assertEqual([d.path for d in diags], [(1, 0)])
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("no"))
        self.assertEqual((diags[0].expected, diags[0].actual), ("uint", "tstr"))
        self.assertEqual(diags[0].rule, "inner")

    def test_embedded_cbor_at_the_root(self):
        raw, diags = found("r = bstr .cbor inner\ninner = {0: uint}", cbor.encode({0: "no"}))
        self.assertEqual(diags[0].path, (0,))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("no"))

    def test_embedded_array_of_maps(self):
        schema = "r = bstr .cbor [* inner]\ninner = {0: uint}"
        raw, diags = found(schema, cbor.encode([{0: 1}, {0: "no"}]))
        self.assertEqual(diags[0].path, (1, 0))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("no"))

    def test_embedded_primitive_is_the_whole_inner_item(self):
        raw, diags = found("r = { 0: bstr .cbor uint }", {0: cbor.encode("x")})
        self.assertEqual(diags[0].category, "nested")
        self.assertEqual(diags[0].path, (0,))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("x"))  # inside the byte string
        self.assertEqual((diags[0].expected, diags[0].actual), ("uint", "tstr"))

    def test_embedded_primitive_in_an_element(self):
        raw, diags = found("r = [* bstr .cbor uint]", [cbor.encode(1), cbor.encode("x")])
        self.assertEqual(diags[0].path, (1,))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("x"))

    def test_cborseq_item(self):
        raw, diags = found("r = bstr .cborseq [* inner]\ninner = {0: uint}",
                           cbor.encode({0: 1}) + cbor.encode({0: "no"}))
        self.assertEqual(diags[0].path, (1, 0))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("no"))

    def test_tag_content(self):
        raw, diags = found("r = { 0: #6.5(inner) }\ninner = {a: uint}",
                           {0: CBORTag(5, {"a": "x"})})
        self.assertEqual(diags[0].path, (0, "a"))
        self.assertEqual(bytes_of(raw, diags[0]), cbor.encode("x"))

    def test_nested_choice_inside_embedded(self):
        schema = "r = { 0: bstr .cbor inner }\ninner = {0: uint / tstr}"
        raw, diags = found(schema, {0: cbor.encode({0: b"x"})})
        diag = diags[0]
        self.assertEqual(diag.path, (0, 0))
        self.assertEqual(bytes_of(raw, diag), cbor.encode(b"x"))
        self.assertEqual({bytes_of(raw, a) for a in diag.alternatives}, {cbor.encode(b"x")})

    def test_alternatives_survive_a_nested_check(self):
        schema = "r = [* bstr .cbor inner]\ninner = {a: uint / tstr}"
        raw, diags = found(schema, [cbor.encode({"a": b"x"})])
        diag = diags[0]
        self.assertEqual(diag.path, (0, "a"))
        self.assertEqual([(a.path, a.expected) for a in diag.alternatives],
                         [((0, "a"), "uint"), ((0, "a"), "tstr")])
        self.assertEqual({bytes_of(raw, a) for a in diag.alternatives}, {cbor.encode(b"x")})

    def test_alternatives_continue_from_the_path_of_their_finding(self):
        from cddl_verifier._analyzer import CBORAnalyzer, CDDLParser, _CheckError, _NO_STEP
        analyzer = CBORAnalyzer(CDDLParser("r = uint"))
        cause = _CheckError("expected uint, got str", path=("deeper",), schema_path=("rule",))
        finding = _CheckError("matches none of uint", path=("a", "b"), schema_path=("x",),
                              alternatives=(("uint", cause),))
        diagnostic = analyzer._diagnostic("m", (0,), None, None, None, None, _NO_STEP, finding)
        self.assertEqual(diagnostic.path, (0, "a", "b"))
        alternative = diagnostic.alternatives[0]
        self.assertEqual(alternative.path, (0, "a", "b", "deeper"))
        self.assertEqual(alternative.schema_path, ("x", "rule"))

    def test_alternatives_of_an_embedded_choice_are_inside_the_byte_string(self):
        raw, diags = found("r = { 0: bstr .cbor (uint / tstr) }", {0: cbor.encode(b"x")})
        diag = diags[0]
        self.assertEqual(bytes_of(raw, diag), cbor.encode(b"x"))  # not the outer byte string
        self.assertEqual(len(diag.alternatives), 2)
        for alternative in diag.alternatives:
            self.assertEqual((alternative.offset, alternative.end_offset),
                             (diag.offset, diag.end_offset))

    def test_findings_still_compare_as_strings(self):
        analyzer = validate("r = uint", cbor.encode(1))
        self.assertTrue(analyzer.valid)
        _, diags = found("r = uint .le 3", 9)
        self.assertEqual(diags[0].message, "Value does not match type 'r': 9 violates .le 3")


if __name__ == "__main__":
    unittest.main()
