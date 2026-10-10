#!/usr/bin/env python3
"""
Literal types (#73): ``1``, ``-1``, ``1.5``, ``"a"``, ``h'01'``, ``true`` and
``false`` match only their own value, as map fields, array elements, inline
array elements, named rules and the root rule.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate


class LiteralTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, data)
                self.assertEqual(result.valid, expected, result.errors)


class TestFieldLiterals(LiteralTestCase):

    def test_integer(self):
        self.check([
            ("r = {x: 1}", {"x": 1}, True),
            ("r = {x: 1}", {"x": 2}, False),     # was accepted
            ("r = {x: 1}", {"x": True}, False),  # bool is not an integer
            ("r = {x: 1}", {"x": 1.0}, False),   # float is not an integer
            ("r = {x: 1}", {"x": "1"}, False),
            ("r = {x: -1}", {"x": -1}, True),
            ("r = {x: -1}", {"x": 1}, False),
        ])

    def test_float(self):
        self.check([
            ("r = {x: 1.5}", {"x": 1.5}, True),
            ("r = {x: 1.5}", {"x": 2.5}, False),
            ("r = {x: 0.0}", {"x": 0.0}, True),
            ("r = {x: 0.0}", {"x": -0.0}, False),  # distinct CBOR values
            ("r = {x: -0.0}", {"x": -0.0}, True),
        ])

    def test_text_and_bytes(self):
        self.check([
            ('r = {x: "a"}', {"x": "a"}, True),
            ('r = {x: "a"}', {"x": "b"}, False),  # was accepted
            ('r = {x: "a"}', {"x": b"a"}, False),
            ("r = {x: h'01'}", {"x": b"\x01"}, True),
            ("r = {x: h'01'}", {"x": b"\x02"}, False),
            ("r = {x: 'ab'}", {"x": b"ab"}, True),
            ("r = {x: 'ab'}", {"x": "ab"}, False),
        ])

    def test_true_and_false(self):
        self.check([
            ("r = {x: true}", {"x": True}, True),
            ("r = {x: true}", {"x": False}, False),  # was accepted as bool
            ("r = {x: true}", {"x": 1}, False),
            ("r = {x: false}", {"x": False}, True),
            ("r = {x: false}", {"x": True}, False),
            ("r = {x: bool}", {"x": False}, True),
        ])

    def test_named_rule(self):
        self.check([
            ("r = {x: c}\nc = 1", {"x": 1}, True),
            ("r = {x: c}\nc = 1", {"x": 2}, False),
            ("r = {x: c}\nc = d\nd = \"v\"", {"x": "w"}, False),
            ("r = {x: c}\nc = true", {"x": False}, False),
        ])

    def test_literal_with_control(self):
        self.check([
            ('r = {x: "ab" .size 2}', {"x": "ab"}, True),
            ('r = {x: "ab" .regexp "a+"}', {"x": "ab"}, False),
            ('r = {x: "ab" .regexp "a+"}', {"x": "b"}, False),
            ("r = {x: 5 .le 10}", {"x": 5}, True),
            ("r = {x: 5 .le 10}", {"x": 6}, False),
        ])

    def test_optional_field(self):
        self.check([
            ("r = {? x: 1}", {}, True),
            ("r = {? x: 1}", {"x": 2}, False),
        ])


class TestArrayLiterals(LiteralTestCase):

    def test_positional_elements(self):
        self.check([
            ("r = [1, tstr]", [1, "a"], True),
            ("r = [1, tstr]", [2, "a"], False),  # was accepted
            ('r = [tstr, "end"]', ["a", "end"], True),
            ('r = [tstr, "end"]', ["a", "stop"], False),
        ])

    def test_repeated_elements(self):
        self.check([
            ("r = [* 1]", [1, 1], True),
            ("r = [* 1]", [1, 2], False),
            ("r = [* true]", [True, False], False),
        ])

    def test_inline_array_field(self):
        self.check([
            ("r = {x: [+ 1]}", {"x": [1]}, True),
            ("r = {x: [+ 1]}", {"x": [2]}, False),
            ('r = {x: [* "a"]}', {"x": ["a", "b"]}, False),
        ])


class TestRootLiterals(LiteralTestCase):

    def test_root_rule(self):
        self.check([
            ("r = 1", 1, True),
            ("r = 1", 2, False),
            ('r = "a"', "a", True),
            ('r = "a"', "b", False),
            ("r = true", True, True),
            ("r = true", False, False),
            ("r = c\nc = 2", 2, True),
            ("r = c\nc = 2", 3, False),
        ])

    def test_error_message_names_the_literal(self):
        result = validate("r = {x: 1}", {"x": 2})
        self.assertFalse(result.valid)
        self.assertTrue(any("expected 1" in error for error in result.errors), result.errors)


class TestRedefinedTrue(LiteralTestCase):

    def test_schema_rule_named_true_is_used(self):
        # A schema may define its own 'true'; it is then a rule, not the literal.
        self.check([
            ("r = {x: true}\ntrue = uint", {"x": 5}, True),
            ("r = {x: true}\ntrue = uint", {"x": "a"}, False),
        ])

    def test_structured_rule_named_true_is_used(self):
        self.check([
            ("r = {x: true}\ntrue = {a: 1}", {"x": {"a": 1}}, True),
            ("r = {x: true}\ntrue = {a: 1}", {"x": True}, False),
            ("r = {x: false}\nfalse = [uint]", {"x": [1]}, True),
            ("r = {x: false}\nfalse = [uint]", {"x": False}, False),
            ("r = {x: true}\ntrue /= uint\ntrue /= tstr", {"x": 5}, True),
            ("r = {x: true}\ntrue /= uint\ntrue /= tstr", {"x": True}, False),
        ])


if __name__ == "__main__":
    unittest.main()
