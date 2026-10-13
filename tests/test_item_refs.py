#!/usr/bin/env python3
"""
References to data items by encoding (#167): ``#``, ``#M``, ``#M.N``,
``#6(t)`` and ``#6.<type>(t)`` (RFC 9682) are checked wherever a type can
stand: the root rule, map fields, array elements, choice alternatives and
named rules. They used to be accepted unchecked in fields and rejected at the
root.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator, validate

HEX = bytes.fromhex

# one value of each kind, as CBOR
SAMPLES = {
    "uint": HEX("05"),
    "nint": HEX("24"),
    "bstr": HEX("4101"),
    "tstr": HEX("6161"),
    "array": HEX("8101"),
    "map": HEX("a1616101"),
    "tag": HEX("c101"),
    "false": HEX("f4"),
    "true": HEX("f5"),
    "null": HEX("f6"),
    "float": HEX("f93c00"),
    "float32": HEX("fa3fc00000"),
    "float64": HEX("fb3ff8000000000000"),
    "undefined": HEX("f7"),
    "simple": HEX("f0"),            # simple(16)
    "bignum": HEX("c249010000000000000000"),
}


def wrap(shape, item):
    """CBOR for *item* in the position *shape* names."""
    if shape == "root":
        return item
    if shape == "field":
        return HEX("a16161") + item
    return HEX("81") + item  # array


SCHEMAS = {"root": "r = {t}", "field": "r = {{a: {t}}}", "array": "r = [* {t}]"}


class ItemRefTestCase(unittest.TestCase):

    def accepts(self, ref, kinds):
        """*ref* matches exactly the sample kinds *kinds*, in every position."""
        for shape, template in SCHEMAS.items():
            schema = template.format(t=ref)
            validator = Validator(schema)
            for kind, item in SAMPLES.items():
                with self.subTest(schema=schema, kind=kind):
                    result = validator.validate(wrap(shape, item))
                    self.assertEqual(result.valid, kind in kinds, result.errors)


class TestMajorTypes(ItemRefTestCase):

    def test_unsigned(self):
        self.accepts("#0", {"uint"})

    def test_negative(self):
        self.accepts("#1", {"nint"})

    def test_byte_string(self):
        self.accepts("#2", {"bstr"})

    def test_text_string(self):
        self.accepts("#3", {"tstr"})

    def test_array(self):
        self.accepts("#4", {"array"})

    def test_map(self):
        self.accepts("#5", {"map"})

    def test_tag(self):
        self.accepts("#6", {"tag", "bignum"})   # a bignum is tag 2 or 3

    def test_floats_and_simple_values(self):
        self.accepts("#7", {"false", "true", "null", "float", "float32", "float64",
                            "undefined", "simple"})

    def test_any_item(self):
        self.accepts("#", set(SAMPLES))

    def test_prelude_names_agree(self):
        # 'uint = #0' etc. are the same checks as the references
        self.accepts("uint", {"uint"})
        self.accepts("nint", {"nint"})
        self.accepts("tstr", {"tstr"})

    def test_choice_alternatives(self):
        self.accepts("#1 / #3", {"nint", "tstr"})
        self.accepts("#0 / #7.22", {"uint", "null"})

    def test_named_rule(self):
        for kind, expected in (("nint", True), ("uint", False), ("tstr", False)):
            with self.subTest(kind=kind):
                self.assertEqual(
                    validate("r = {a: n}\nn = #1", wrap("field", SAMPLES[kind])).valid, expected)

    def test_error_names_the_expected_type(self):
        result = validate("r = {a: #1}", {"a": "x"})
        self.assertFalse(result.valid)
        self.assertTrue(any("negative integer" in e for e in result.errors), result.errors)

    def test_booleans_are_not_integers(self):
        self.accepts("#0", {"uint"})
        self.assertFalse(validate("r = {a: #0}", {"a": True}).valid)


class TestAdditionalInformation(ItemRefTestCase):

    def test_small_values(self):
        for schema, data, expected in [
            ("r = #0.5", HEX("05"), True), ("r = #0.5", HEX("06"), False),
            ("r = #0.5", HEX("1805"), False),  # 5 written with a 1-byte argument is #0.24
            ("r = #0.24", HEX("1805"), True), ("r = #0.24", HEX("05"), False),
            ("r = #0.24", HEX("1818"), True), ("r = #0.24", HEX("1819"), True),
            ("r = #0.25", HEX("190100"), True), ("r = #0.26", HEX("1a00000005"), True),
            ("r = #0.27", HEX("1b0000000000000005"), True),
            ("r = #1.24", HEX("3805"), True), ("r = #1.24", HEX("20"), False),
            ("r = #1.0", HEX("20"), True), ("r = #1.0", HEX("21"), False),
            ("r = {a: #0.5}", HEX("a1616105"), True), ("r = {a: #0.5}", HEX("a1616106"), False),
        ]:
            with self.subTest(schema=schema, data=data.hex()):
                self.assertEqual(Validator(schema).validate(data).valid, expected)

    def test_lengths(self):
        for schema, data, expected in [
            ("r = #2.2", HEX("420102"), True), ("r = #2.2", HEX("4101"), False),
            ("r = #3.1", HEX("6161"), True), ("r = #3.1", HEX("626162"), False),
            ("r = #4.2", HEX("820102"), True), ("r = #4.2", HEX("8101"), False),
            ("r = #5.1", HEX("a1616101"), True), ("r = #5.1", HEX("a0"), False),
            ("r = {a: #3.2}", HEX("a16161626162"), True),
            ("r = [* #3.2]", HEX("81626162"), True), ("r = [* #3.2]", HEX("816161"), False),
        ]:
            with self.subTest(schema=schema, data=data.hex()):
                self.assertEqual(Validator(schema).validate(data).valid, expected)

    def test_a_type_of_the_wrong_major_still_fails(self):
        self.assertFalse(Validator("r = #0.5").validate(HEX("6161")).valid)

    def test_simple_values(self):
        self.accepts("#7.20", {"false"})
        self.accepts("#7.21", {"true"})
        self.accepts("#7.22", {"null"})

    def test_float_widths_follow_the_encoding(self):
        self.accepts("#7.25", {"float"})
        self.accepts("#7.26", {"float32"})
        self.accepts("#7.27", {"float64"})

    def test_undefined_and_simple_values(self):
        self.accepts("#7.23", {"undefined"})
        self.accepts("#7.16", {"simple"})
        self.accepts("#7.19", set())
        self.accepts("#7.255", set())
        self.accepts("#7.24", set())   # not a simple value number

    def test_number_given_as_a_type(self):
        self.accepts("#7.<20..21>", {"false", "true"})
        self.accepts("#7.<uint .ge 22>", {"null", "undefined", "float", "float32", "float64"})
        self.accepts("#7.<16 / 23>", {"undefined", "simple"})

    def test_lengths_by_head_width(self):
        for schema, data, expected in [
            ("r = #2.24", HEX("580101"), True), ("r = #2.24", HEX("4101"), False),
            ("r = #3.25", HEX("79000161"), True), ("r = #3.1", HEX("79000161"), False),
            ("r = #4.26", HEX("9a0000000101"), True),
            ("r = #5.27", HEX("bb0000000000000000"), True), ("r = #5.0", HEX("a0"), True),
            ("r = #5.0", HEX("b800"), False),
        ]:
            with self.subTest(schema=schema, data=data.hex()):
                self.assertEqual(Validator(schema).validate(data).valid, expected)

    def test_indefinite_length(self):
        for schema, data, expected in [
            ("r = #4.31", HEX("9f01ff"), True), ("r = #4.31", HEX("8101"), False),
            ("r = #4.1", HEX("9f01ff"), False),
            ("r = #5.31", HEX("bf616101ff"), True), ("r = #5.31", HEX("a1616101"), False),
            ("r = #2.31", HEX("5f4101ff"), True), ("r = #3.31", HEX("7f6161ff"), True),
            ("r = #3.31", HEX("6161"), False),
            ("r = {a: #4.31}", HEX("a161619fff"), True),
            ("r = [* #4.31]", HEX("819f01ff"), True), ("r = [* #4.31]", HEX("8180"), False),
        ]:
            with self.subTest(schema=schema, data=data.hex()):
                self.assertEqual(Validator(schema).validate(data).valid, expected)

    def test_values_given_as_python_objects_are_written_the_preferred_way(self):
        self.assertTrue(validate("r = #0.5", 5).valid)
        self.assertFalse(validate("r = #0.24", 5).valid)
        self.assertTrue(validate("r = #0.24", 200).valid)
        self.assertTrue(validate("r = #4.2", [1, 2]).valid)
        self.assertTrue(validate("r = #3.2", "ab").valid)

    def test_no_warnings(self):
        for ref in ("#0.24", "#2.26", "#4.27", "#5.31", "#0.5", "#7.25", "#6", "#"):
            with self.subTest(ref=ref):
                self.assertEqual(Validator(f"r = {ref}").warnings, ())


class TestBareTagNumber(unittest.TestCase):
    """``#6.N`` (no parentheses) is a tag numbered N around any item (RFC 9682)."""

    def check(self, schema, cases):
        validator = Validator(schema)
        for data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validator.validate(HEX(data))
                self.assertEqual(result.valid, expected, result.errors)
                self.assertEqual(validator.warnings, ())  # N is the number, not a width

    def test_number_must_match(self):
        self.check("r = #6.1", [("c101", True), ("c16178", True), ("c201", False), ("01", False)])

    def test_number_of_24_or_more(self):
        self.check("r = #6.24", [("d81841ff", True), ("d8201841", False), ("c101", False)])

    def test_bignum_is_tag_2_or_3(self):
        self.check("r = #6.2", [("c249010000000000000000", True), ("c301", False)])
        self.check("r = #6.3", [("c349010000000000000000", True), ("c201", False)])

    def test_in_fields_elements_and_choices(self):
        self.check("r = {a: #6.1}", [("a16161c101", True), ("a16161c201", False)])
        self.check("r = [* #6.1]", [("81c101", True), ("81c201", False)])
        self.check("r = #6.1 / #6.2", [("c101", True), ("c201", True), ("c301", False)])

    def test_number_given_as_a_type(self):
        self.check("r = #6.<uint .ge 24>", [("d82001", True), ("c101", False), ("01", False)])
        self.check("r = {a: #6.<1 / 2>}", [("a16161c101", True), ("a16161c301", False)])


class TestTagReferences(unittest.TestCase):

    def check(self, schema, cases):
        validator = Validator(schema)
        for data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validator.validate(HEX(data))
                self.assertEqual(result.valid, expected, result.errors)

    def test_any_tag(self):
        for schema, prefix in (("r = #6(tstr)", ""), ("r = {a: #6(tstr)}", "a16161"),
                               ("r = [* #6(tstr)]", "81")):
            self.check(schema, [
                (prefix + "d8206178", True),     # 32("a")
                (prefix + "c16178", True),       # 1("x"): any tag, tstr inside
                (prefix + "c101", False),        # 1(1): the content is not a tstr
                (prefix + "6178", False),        # not tagged
                (prefix + "01", False),
            ])

    def test_tag_number_given_as_a_type(self):
        schema = "r = #6.<uint>(tstr)"
        self.check(schema, [("d8206178", True), ("c16178", True), ("c101", False),
                            ("6178", False), ("01", False)])
        self.check("r = {a: #6.<uint>(tstr)}",
                   [("a16161d8206178", True), ("a1616101", False), ("a16161c101", False),
                    ("a16161d8206161", True)])
        self.check("r = [* #6.<uint>(tstr)]",
                   [("81d8206178", True), ("8101", False), ("80", True)])

    def test_tag_number_must_satisfy_the_type(self):
        self.check("r = #6.<uint .le 10>(any)", [("c101", True), ("d8206178", False)])
        self.check("r = #6.<1 / 2>(any)", [("c101", True), ("c201", True), ("c301", False)])
        self.check("r = {a: #6.<1..5>(tstr)}",
                   [("a16161c26178", True), ("a16161d8206178", False)])

    def test_tag_number_through_a_named_type(self):
        self.check("r = #6.<epoch>(int)\nepoch = 1 / 2", [("c101", True), ("c301", False)])

    def test_content_may_be_an_item_reference(self):
        self.check("r = #6.<uint>(#1)", [("c120", True), ("c101", False)])

    def test_bignum_is_tag_2_or_3(self):
        self.check("r = #6.<2 / 3>(bstr)", [("c249010000000000000000", True), ("c101", False)])
        self.check("r = #6.<1>(any)", [("c249010000000000000000", False)])

    def test_choice(self):
        self.check("r = #6.<uint>(tstr) / uint", [("d8206178", True), ("05", True),
                                                    ("6178", False)])

    def test_unique_choices_sees_a_tag_as_structure(self):
        # '#6.<uint>(tstr)' and '#6.1(tstr)' both fit a 1("x")
        result = validate("r = #6.<uint>(tstr) / #6.1(tstr)", HEX("c16178"),
                          unique_choices=True)
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
