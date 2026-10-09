#!/usr/bin/env python3
"""
CBOR tag checking: a tagged rule (``t = #6.7(m)``) requires exactly its tag,
and an untagged rule rejects tagged data, at the root, in map fields, in
arrays and through type choices.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier._analyzer import CDDLParser

M = "\nm = {a: uint}"
T = "\nt = #6.7(m)"
TAGGED = (7, {"a": 1})
WRONG_TAG = (8, {"a": 1})
UNTAGGED = {"a": 1}


class TagTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, data)
                self.assertEqual(result.valid, expected, result.errors)


class TestRootTags(TagTestCase):

    def test_untagged_root_rejects_tagged_data(self):
        result = validate("m = { a: uint }", (999, {"a": 1}))
        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0],
                         "Type 'm' expects an untagged map, but the data has CBOR tag 999")
        self.assertTrue(validate("m = { a: uint }", {"a": 1}).valid)

    def test_untagged_array_root_rejects_tagged_data(self):
        self.check([
            ("r = [* uint]", [1], True),
            ("r = [* uint]", (5, [1]), False),
        ])

    def test_tagged_root(self):
        self.check([
            ("r = #6.7(m)" + M, TAGGED, True),
            ("r = #6.7(m)" + M, WRONG_TAG, False),
            ("r = #6.7(m)" + M, UNTAGGED, False),
            ("r = t" + T + M, TAGGED, True),
            ("r = t" + T + M, WRONG_TAG, False),
        ])

    def test_nested_tags(self):
        self.check([
            ("r = #6.1(#6.2(m))" + M, (1, (2, {"a": 1})), True),
            ("r = #6.1(#6.2(m))" + M, (1, {"a": 1}), False),
            ("r = #6.1(#6.2(m))" + M, (1, (3, {"a": 1})), False),
            ("r = #6.1(m)" + M, (1, (2, {"a": 1})), False),
        ])


class TestFieldTags(TagTestCase):

    def test_untagged_field_rejects_tagged_data(self):
        self.check([
            ("r = {x: m}" + M, {"x": UNTAGGED}, True),
            ("r = {x: m}" + M, {"x": (9, {"a": 1})}, False),
            ("r = {x: uint}", {"x": (9, 5)}, False),
        ])

    def test_tagged_rule_field(self):
        self.check([
            ("r = {x: t}" + T + M, {"x": TAGGED}, True),
            ("r = {x: t}" + T + M, {"x": WRONG_TAG}, False),
            ("r = {x: t}" + T + M, {"x": UNTAGGED}, False),
            ("r = {x: #6.7(m)}" + M, {"x": TAGGED}, True),
            ("r = {x: #6.7(m)}" + M, {"x": WRONG_TAG}, False),
        ])

    def test_tagged_primitive_field(self):
        self.check([
            ("r = {x: #6.7(uint)}", {"x": (7, 1)}, True),
            ("r = {x: #6.7(uint)}", {"x": (8, 1)}, False),
            ("r = {x: #6.7(uint)}", {"x": 1}, False),
            ("r = {x: #6.7(uint)}", {"x": (7, "s")}, False),
            ("r = {x: s}\ns = #6.1(tstr .size 2)", {"x": (1, "ab")}, True),
            ("r = {x: s}\ns = #6.1(tstr .size 2)", {"x": (1, "abc")}, False),
        ])

    def test_tagged_primitive_field_error_message(self):
        result = validate("r = {x: #6.7(uint)}", {"x": (8, 1)})
        self.assertIn("Field 'x' in 'r' requires CBOR tag 7, got tag 8", result.errors)


class TestArrayAndChoiceTags(TagTestCase):

    def test_declared_array_elements(self):
        self.check([
            ("r = [* m]" + M, [UNTAGGED], True),
            ("r = [* m]" + M, [(9, {"a": 1})], False),
            ("r = [* t]" + T + M, [TAGGED], True),
            ("r = [* t]" + T + M, [WRONG_TAG], False),
            ("r = [* t]" + T + M, [UNTAGGED], False),
            ("r = [* #6.7(uint)]", [(7, 1)], True),
            ("r = [* #6.7(uint)]", [(8, 1)], False),
        ])

    def test_declared_array_error_names_element_rule(self):
        result = validate("r = [* t]" + T + M, [WRONG_TAG])
        self.assertEqual(result.errors[0], "Type 't' requires CBOR tag 7, got tag 8")

    def test_inline_array_field_elements(self):
        self.check([
            ("r = {x: [* m]}" + M, {"x": [(9, {"a": 1})]}, False),
            ("r = {x: [* t]}" + T + M, {"x": [TAGGED]}, True),
            ("r = {x: [* t]}" + T + M, {"x": [WRONG_TAG]}, False),
        ])

    def test_socket_choice(self):
        schema = "$c /= t\n$c /= m\nr = {x: $c}" + T + M
        self.check([
            (schema, {"x": TAGGED}, True),
            (schema, {"x": UNTAGGED}, True),
            (schema, {"x": WRONG_TAG}, False),
        ])


class TestExtractCborTag(unittest.TestCase):

    def test_nested_parentheses(self):
        cddl = CDDLParser("")
        self.assertEqual(cddl.extract_cbor_tag("#6.1(#6.2(m))"), (1, "#6.2(m)"))
        self.assertEqual(cddl.extract_cbor_tag("#6.501(unsigned-corim-map)"),
                         (501, "unsigned-corim-map"))
        self.assertEqual(cddl.extract_cbor_tag("#6.505(bytes .cbor x)"),
                         (505, "bytes .cbor x"))
        self.assertIsNone(cddl.extract_cbor_tag("#6.1()"))
        self.assertIsNone(cddl.extract_cbor_tag("#6.1(m"))
        self.assertIsNone(cddl.extract_cbor_tag("m"))


if __name__ == "__main__":
    unittest.main()
