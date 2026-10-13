#!/usr/bin/env python3
"""
References to data items by encoding (#167): ``#``, ``#M``, ``#M.N``,
``#6(t)`` and ``#6.<type>(t)`` (RFC 9682) are checked wherever a type can
stand: the root rule, map fields, array elements, choice alternatives and
named rules. They used to be accepted unchecked in fields and rejected at the
root.
"""

import sys
import warnings
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
    "float64": HEX("fb3ff8000000000000"),
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
        self.accepts("#7", {"false", "true", "null", "float", "float64"})

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
            ("r = #0.5", HEX("1805"), True),   # another width; see width_is_not_kept
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

    def test_float_widths_are_floats(self):
        # the decoder does not keep float widths (#169): all are 'float'
        self.accepts("#7.25", {"float", "float64"})
        self.accepts("#7.27", {"float", "float64"})

    def test_undefined_and_unassigned_simple_values_match_nothing(self):
        # they cannot be decoded at all yet (#168)
        self.accepts("#7.23", set())
        self.accepts("#7.19", set())

    def test_number_given_as_a_type(self):
        self.accepts("#7.<20..21>", {"false", "true"})
        self.accepts("#7.<uint .ge 22>", {"null", "float", "float64"})

    def test_width_is_not_kept(self):
        for ref in ("#0.24", "#2.26", "#4.27", "#5.31"):
            with self.subTest(ref=ref), warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                warned = Validator(f"r = {{a: {ref}}}").warnings
                self.assertEqual(len(warned), 1, warned)
                self.assertIn(f"'{ref}' is checked as '{ref[:2]}'", warned[0])

    def test_width_is_checked_as_the_major_type(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertTrue(Validator("r = #0.24").validate(HEX("05")).valid)
            self.assertFalse(Validator("r = #0.24").validate(HEX("6161")).valid)

    def test_no_warning_for_exact_values(self):
        for ref in ("#0.5", "#7.25", "#7.20", "#6", "#"):
            with self.subTest(ref=ref):
                self.assertEqual(Validator(f"r = {ref}").warnings, ())


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
