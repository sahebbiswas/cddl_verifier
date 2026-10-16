#!/usr/bin/env python3
"""
RFC 8610 prelude types beyond the basic primitives (#130): ``uri``,
``tdate``, ``time``, ``number``, ``decfrac``, ``encoded-cbor`` and the other
tagged rules, and the integer types ``nint``, ``biguint``, ``bignint``,
``bigint``, ``integer`` and ``unsigned`` at the 64-bit boundaries (#99).
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
from cddl_verifier.cbor import CBORTag

B = 2**64  # the first value that needs a bignum


class PreludeTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, data)
                self.assertEqual(result.valid, expected, result.errors)

    def check_type(self, type_name, cases):
        """Check each value as a map field, an array element and the root."""
        for value, expected in cases:
            self.check([
                (f"r = {{o: {type_name}}}", {"o": value}, expected),
                (f"r = [* {type_name}]", [value], expected),
                (f"r = {type_name}", value, expected),
            ])


class TestTaggedTextTypes(PreludeTestCase):

    def test_uri(self):
        self.check_type("uri", [
            (CBORTag(32, "https://example.com"), True),
            ([1], False),                    # was accepted
            ("https://example.com", False),  # tag 32 is required
            (CBORTag(33, "https://example.com"), False),
            (CBORTag(32, b"https://example.com"), False),
        ])

    def test_other_tagged_text(self):
        # the tag number and the type of the content; what the text means is
        # checked in tests/test_tag_content.py (#169)
        for name, tag, text in (("tdate", 0, "2020-01-01T00:00:00Z"), ("b64url", 33, "YQ"),
                                ("b64legacy", 34, "YQ=="), ("regexp", 35, "a+"),
                                ("mime-message", 36, "Subject: x\r\n\r\nbody")):
            with self.subTest(type=name):
                self.check_type(name, [
                    (CBORTag(tag, text), True),
                    (text, False),
                    (CBORTag(tag, 5), False),
                    (CBORTag(tag + 100, text), False),
                ])

    def test_encoded_cbor(self):
        self.check_type("encoded-cbor", [
            (CBORTag(24, b"\x01"), True),
            (CBORTag(24, "x"), False),
            (b"\x01", False),
        ])

    def test_tags_around_any(self):
        for name, tag in (("eb64url", 21), ("eb64legacy", 22), ("eb16", 23),
                          ("cbor-any", 55799)):
            with self.subTest(type=name):
                self.check_type(name, [
                    (CBORTag(tag, [1, "a"]), True),
                    (CBORTag(tag, 5), True),
                    ([1, "a"], False),
                    (CBORTag(tag + 1, 5), False),
                ])


class TestNumberTypes(PreludeTestCase):

    def test_number(self):
        self.check_type("number", [
            (5, True),
            (-5, True),
            (1.5, True),
            ("5", False),
            (True, False),
        ])

    def test_time(self):
        self.check_type("time", [
            (CBORTag(1, 1700000000), True),
            (CBORTag(1, 1700000000.5), True),
            (1700000000, False),  # tag 1 is required
            (CBORTag(1, "2020"), False),
        ])

    def test_decfrac_and_bigfloat(self):
        for name, tag in (("decfrac", 4), ("bigfloat", 5)):
            with self.subTest(type=name):
                self.check_type(name, [
                    (CBORTag(tag, [-2, 27315]), True),
                    (CBORTag(tag, [-2, B]), True),      # the mantissa is an 'integer'
                    (CBORTag(tag, ["a", 1]), False),    # was accepted
                    (CBORTag(tag, [1, 1.5]), False),
                    (CBORTag(tag, 5), False),
                    ([-2, 27315], False),
                ])


class TestIntegerTypes(PreludeTestCase):

    def test_nint(self):
        self.check_type("nint", [
            (-1, True),
            (-B, True),
            (0, False),
            (5, False),          # was accepted as 'int'
            (-B - 1, False),     # a bignum
            (True, False),
            (-1.0, False),
        ])

    def test_nint_with_control(self):
        self.check([
            ("r = {o: nint .ge -5}", {"o": -5}, True),
            ("r = {o: nint .ge -5}", {"o": -6}, False),
            ("r = {o: nint .ge -5}", {"o": 1}, False),
        ])

    def test_biguint(self):
        self.check_type("biguint", [
            (B, True),
            (CBORTag(2, b"\x01"), True),   # a bignum that fits stays tagged
            (B - 1, False),         # a uint
            (5, False),
            (-B - 1, False),
            (CBORTag(3, b"\x00"), False),
        ])

    def test_bignint(self):
        self.check_type("bignint", [
            (-B - 1, True),
            (CBORTag(3, b"\x00"), True),
            (-B, False),            # an nint
            (B, False),
            (CBORTag(2, b"\x01"), False),
        ])

    def test_bigint(self):
        self.check_type("bigint", [
            (B, True),
            (-B - 1, True),
            (CBORTag(2, b"\x01"), True),
            (5, False),
            (-5, False),
        ])

    def test_integer(self):
        self.check_type("integer", [
            (0, True),
            (B - 1, True),
            (-B, True),
            (B, True),
            (-B - 1, True),
            (1.5, False),
            ("1", False),
        ])

    def test_unsigned(self):
        self.check_type("unsigned", [
            (0, True),
            (B - 1, True),
            (B, True),
            (-1, False),
            (-B - 1, False),
        ])


class TestSchemaRulesWin(PreludeTestCase):

    def test_schema_redefines_prelude_names(self):
        self.check([
            ("r = {o: uri}\nuri = tstr", {"o": "x"}, True),
            ("r = {o: uri}\nuri = tstr", {"o": CBORTag(32, "x")}, False),
            ("r = {o: uri}\nuri = {a: 1}", {"o": {"a": 1}}, True),
            ("r = {o: decfrac}\ndecfrac = tstr", {"o": "x"}, True),
            ("r = {o: nint}\nnint = tstr", {"o": "x"}, True),
            ("r = {o: time}\ntime /= uint\ntime /= tstr", {"o": 5}, True),
        ])

    def test_tables_only_get_entries_for_used_rules(self):
        self.assertEqual(CDDLParser("r = {o: uri}").types.keys(), {"r"})
        self.assertIn("decfrac@tag", CDDLParser("r = {o: decfrac}").types)
        self.assertNotIn("decfrac@tag", CDDLParser("r = {o: decfrac}\ndecfrac = tstr").types)

    def test_schema_rule_with_the_synthetic_name_is_kept(self):
        # '@' is allowed in CDDL names, so a schema can define 'decfrac@tag'
        # while also using the prelude's 'decfrac'.
        schema = "r = {o: decfrac, p: decfrac@tag}\ndecfrac@tag = tstr"
        self.check([
            (schema, {"o": CBORTag(4, [1, 2]), "p": "x"}, True),
            (schema, {"o": CBORTag(4, [1, 2]), "p": [1, 2]}, False),
            (schema, {"o": CBORTag(4, "x"), "p": "x"}, False),
            (schema, {"o": CBORTag(4, ["a", 1]), "p": "x"}, False),
        ])


if __name__ == "__main__":
    unittest.main()
