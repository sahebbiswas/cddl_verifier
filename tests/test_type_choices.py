#!/usr/bin/env python3
"""
Inline type choices (``c = a / b``) in map fields, arrays, inline arrays and
at the root, and root rules that are primitives with controls.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier._analyzer import CBORAnalyzer

RULES = "\nm = {a: uint}\nn = {b: tstr}\nt = #6.7(m)"


class ChoiceTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, data)
                self.assertEqual(result.valid, expected, result.errors)


class TestFieldChoices(ChoiceTestCase):

    def test_primitive_alternatives(self):
        self.check([
            ("r = {x: c}\nc = uint / tstr", {"x": 1}, True),
            ("r = {x: c}\nc = uint / tstr", {"x": "a"}, True),     # was rejected
            ("r = {x: c}\nc = uint / tstr", {"x": 1.5}, False),
            ("r = {x: uint / tstr}", {"x": "a"}, True),
            ("r = {x: uint / tstr}", {"x": b"a"}, False),
        ])

    def test_named_rule_alternatives(self):
        self.check([
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"a": 1}}, True),
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"b": "q"}}, True),
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"zz": 1}}, False),  # was accepted
            ("r = {x: c}\nc = m / n" + RULES, {"x": 5}, False),          # was accepted
        ])

    def test_tagged_alternatives(self):
        self.check([
            ("r = {x: c}\nc = t / m" + RULES, {"x": (7, {"a": 1})}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": {"a": 1}}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": (8, {"a": 1})}, False),  # was accepted
        ])

    def test_controls_in_alternatives(self):
        self.check([
            ("r = {x: tstr .size 2 / bstr}", {"x": "ab"}, True),
            ("r = {x: tstr .size 2 / bstr}", {"x": "abc"}, False),
            ("r = {x: tstr .size 2 / bstr}", {"x": b"abc"}, True),
            ("r = {x: c}\nc = uint .le 10 / tstr", {"x": 10}, True),
            ("r = {x: c}\nc = uint .le 10 / tstr", {"x": 11}, False),
            ('r = {x: c}\nc = tstr .regexp "a/b" / uint', {"x": "a/b"}, True),
            ('r = {x: c}\nc = tstr .regexp "a/b" / uint', {"x": "zz"}, False),
        ])

    def test_embedded_cbor_alternative(self):
        from cddl_verifier.cbor import encode
        schema = "r = [p]\np = bstr .cbor m / tstr\nm = {a: uint}"
        self.check([
            (schema, [encode({"a": 1})], True),
            (schema, [encode({"a": "x"})], False),   # was accepted: any bytes passed
            (schema, [b"\xff"], False),              # does not decode
            (schema, ["s"], True),
        ])

    def test_inline_cbor_and_tag_alternatives_in_field(self):
        from cddl_verifier.cbor import encode
        cbor_field = "r = {x: bstr .cbor m / tstr}\nm = {a: uint}"
        self.check([
            (cbor_field, {"x": "s"}, True),
            (cbor_field, {"x": encode({"a": 1})}, True),
            (cbor_field, {"x": encode({"a": "z"})}, False),
            ("r = {x: #6.7(uint) / tstr}", {"x": "s"}, True),
            ("r = {x: #6.7(uint) / tstr}", {"x": (7, 1)}, True),
            ("r = {x: #6.7(uint) / tstr}", {"x": (8, 1)}, False),
            ("r = [#6.7(uint) / tstr]", ["s"], True),
            ("n = #6.7(uint) / tstr", "s", True),
            ("n = #6.7(uint) / tstr", (8, 1), False),
        ])

    def test_literal_alternatives(self):
        schema = 'r = {x: c}\nc = 1 / 2 / "three" / 1.5'
        self.check([
            (schema, {"x": 2}, True),
            (schema, {"x": "three"}, True),
            (schema, {"x": 1.5}, True),
            (schema, {"x": 3}, False),
            (schema, {"x": True}, False),   # true is not 1
            ("r = {x: c}\nc = true / uint", {"x": True}, True),
            ("r = {x: c}\nc = true / uint", {"x": False}, False),
        ])

    def test_alias_chains_and_optional_fields(self):
        self.check([
            ("r = {x: c}\nc = d / tstr\nd = e\ne = uint", {"x": 5}, True),
            ("r = {x: c}\nc = d / tstr\nd = e\ne = uint", {"x": 1.5}, False),
            ("r = {? x: c}\nc = uint / tstr", {}, True),
            ("r = {? x: c}\nc = uint / tstr", {"x": None}, False),
        ])

    def test_error_lists_each_alternative(self):
        result = validate("r = {x: c}\nc = uint / tstr", {"x": 1.5})
        self.assertEqual(result.errors[0],
                         "Field 'x' in 'r' matches none of uint / tstr "
                         "(uint: expected uint, got 1.5; tstr: expected tstr, got 1.5)")


class TestArrayChoices(ChoiceTestCase):

    def test_declared_array(self):
        self.check([
            ("r = [* c]\nc = m / n" + RULES, [{"a": 1}, {"b": "x"}], True),  # second was rejected
            ("r = [* c]\nc = m / n" + RULES, [{"zz": 1}], False),
            ("r = [* uint / tstr]", [1, "a"], True),
            ("r = [* uint / tstr]", [1.5], False),
        ])

    def test_inline_array_field(self):
        self.check([
            ("r = {x: [* c]}\nc = m / n" + RULES, {"x": [{"b": "x"}]}, True),
            ("r = {x: [* c]}\nc = m / n" + RULES, {"x": [5]}, False),
        ])

    def test_group_choice_is_not_split(self):
        self.assertEqual(CBORAnalyzer._split_choice("a // b"), ["a // b"])
        self.assertEqual(CBORAnalyzer._split_choice("[a / b] / c"), ["[a / b]", "c"])
        self.assertEqual(CBORAnalyzer._split_choice('tstr .regexp "x/y"'), ['tstr .regexp "x/y"'])
        self.assertEqual(CBORAnalyzer._split_choice("#6.1(a / b) / c"), ["#6.1(a / b)", "c"])


class TestRootRules(ChoiceTestCase):

    def test_root_choice(self):
        self.check([
            ("n = uint / tstr", "x", True),
            ("n = uint / tstr", 1, True),
            ("n = uint / tstr", 1.5, False),
            ("r = c\nc = t / m" + RULES, (7, {"a": 1}), True),
            ("r = c\nc = t / m" + RULES, {"a": 1}, True),
            ("r = c\nc = t / m" + RULES, (8, {"a": 1}), False),
        ])

    def test_root_controlled_primitive(self):
        self.check([
            ("n = tstr .size 2", "ab", True),
            ("n = tstr .size 2", "abc", False),
            ("n = uint .le 5", 5, True),
            ("n = uint .le 5", 6, False),
            ('n = tstr .regexp "[a-z]+"', "abc", True),
            ('n = tstr .regexp "[a-z]+"', "ABC", False),
            ("n = uint", -1, False),
            ("a = n\nn = tstr", "x", True),
        ])

    def test_root_error_message(self):
        result = validate("n = uint / tstr", 1.5)
        self.assertEqual(result.errors[0],
                         "Value does not match type 'n': matches none of uint / tstr "
                         "(uint: expected uint, got 1.5; tstr: expected tstr, got 1.5)")


class TestCorimChoices(unittest.TestCase):

    def test_svn_type_choice(self):
        schema = Path(__file__).resolve().parent.parent / "cddl-schemas" / "unified.cddl"
        cases = [
            (5, True),                    # svn = uint
            ((552, 5), True),             # tagged-svn
            ((553, 5), True),             # tagged-min-svn
            ((554, 5), False),            # some other tag
            ("five", False),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                result = validate(schema, data, root_type="svn-type-choice")
                self.assertEqual(result.valid, expected, result.errors)


if __name__ == "__main__":
    unittest.main()
