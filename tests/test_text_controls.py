#!/usr/bin/env python3
"""Conversion controls of RFC 9165 and RFC 9741: ``.b64u`` ... ``.base10``, ``.printf``, ``.json``, ``.join`` (#205)."""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, Validator, cbor
from cddl_verifier._cddl import conversions


def ok(schema, value, root=None):
    return Validator(schema).validate(cbor.encode(value), root).valid


class Cases:
    """Mixin: ``check(schema, [(value, expected)...])``."""

    def check(self, schema, cases):
        validator = Validator(schema)
        for value, expected in cases:
            self.assertEqual(validator.validate(cbor.encode(value)).valid, expected, (schema, value))

    def refuses(self, *schemas, fragment=None):
        for schema in schemas:
            with self.assertRaises(SchemaError, msg=schema) as caught:
                Validator(schema)
            if fragment:
                self.assertIn(fragment, str(caught.exception))


class ByteConversions(Cases, unittest.TestCase):
    def test_base64url(self):
        self.check("r = text .b64u bytes", [("aGk", True), ("-_8", True), ("a!", False), ("aGk=", False),
                                            ("+/8", False), ("", True), ("a", False), ("aGl", False)])

    def test_base64_classic(self):
        self.check("r = text .b64c bytes", [("aGk=", True), ("+/8=", True), ("aGk", False), ("-_8=", False)])

    def test_sloppy_forms_allow_stray_bits_after_the_last_character(self):
        # "aGl" is "hi" with a non-zero last bit: only the sloppy forms take it
        self.check("r = text .b64u-sloppy bytes", [("aGk", True), ("aGl", True), ("a!", False), ("aGk=", False)])
        self.check("r = text .b64c-sloppy bytes", [("aGk=", True), ("aGl=", True), ("aGl", False)])

    def test_base32_and_hex_alphabet_base32(self):
        self.check("r = text .b32 bytes", [("NBSWY3DP", True), ("nbswy3dp", False), ("NBSWY3D", False)])
        self.check("r = text .h32 bytes", [("D1IMOR3F", True), ("d1imor3f", False), ("D1IMOR3", False)])

    def test_base45(self):
        self.check("r = text .b45 bytes", [("QED8WEX0", True), ("QED8WEX", False), ("lowercase", False)])

    def test_base16(self):
        self.check("r = text .hex bytes", [("6869", True), ("68AF", True), ("68af", True), ("686", False), ("zz", False)])
        self.check("r = text .hexlc bytes", [("68af", True), ("68AF", False)])
        self.check("r = text .hexuc bytes", [("68AF", True), ("68af", False)])

    def test_the_decoded_bytes_must_match_the_argument(self):
        self.check("r = text .hex (bytes .size 2)", [("6869", True), ("68", False), ("686970", False)])
        self.check("r = text .hex h'6869'", [("6869", True), ("6868", False)])

    def test_non_text_is_rejected(self):
        self.check("r = text .hex bytes", [(5, False), (b"hi", False)])

    def test_decoders(self):
        self.assertEqual(conversions.decode_hex("6869"), b"hi")
        self.assertIsNone(conversions.decode_hex("6"))
        self.assertEqual(conversions.decode_b64u("aGk", False), b"hi")
        self.assertIsNone(conversions.decode_b64u("aGk=", False))
        self.assertIsNone(conversions.decode_b64u("aGl", False))
        self.assertEqual(conversions.decode_b64u("aGl", True), b"hi")


class Base10(Cases, unittest.TestCase):
    def test_uint_and_int(self):
        self.check("r = text .base10 uint", [("42", True), ("0", True), ("042", False), ("-5", False), ("+5", False),
                                              ("4 2", False), ("", False), ("x", False)])
        self.check("r = text .base10 int", [("-5", True), ("5", True), ("-0", False)])

    def test_the_number_may_be_constrained(self):
        self.check("r = text .base10 (uint .lt 10)", [("5", True), ("50", False)])
        self.check("r = text .base10 (1..3)", [("2", True), ("4", False)])

    def test_schema_errors(self):
        self.refuses("r = text .base10 text", "r = text .base10 float", fragment="base10")


class Json(Cases, unittest.TestCase):
    def test_text_holds_json_of_the_given_type(self):
        self.check("r = text .json {a: int}", [('{"a":1}', True), ('{ "a" : 1 }', True), ('{"a":"x"}', False),
                                               ("{bad", False), ("", False)])

    def test_numbers_and_literals(self):
        self.check("r = text .json [* int]", [("[1,2,3]", True), ("[1.5]", False), ("[]", True)])
        self.check("r = text .json (bool / null)", [("true", True), ("null", True), ("1", False)])

    def test_json_texts_that_are_not_valid_are_refused(self):
        self.check("r = text .json any", [("NaN", False), ("Infinity", False), ('{"a":1,"a":2}', False),
                                          ("[1,]", False), ("'a'", False)])

    def test_applies_to_text_only(self):
        self.refuses("r = bytes .json any", fragment="byte string")

    def test_maps_and_arrays_are_checked_by_shape(self):
        self.check("r = text .json {a: int, ? b: [* tstr]}", [('{"a":1}', True), ('{"a":1,"b":["x"]}', True),
                                                                 ('{"a":1,"b":[1]}', False), ('{"b":[]}', False),
                                                                 ('{"a":1,"c":2}', False)])
        self.check("r = text .json [* {a: int}]", [('[{"a":1},{"a":2}]', True), ('[{"a":"x"}]', False)])


class Printf(Cases, unittest.TestCase):
    def test_formats(self):
        self.check('r = text .printf ["0x%04x", uint]', [("0x00ff", True), ("0xff", False), ("0x00FF", False)])
        self.check('r = text .printf ["%s-%d", tstr, int]', [("a-1", True), ("a--1", True), ("a-x", False)])
        self.check('r = text .printf ["%X", uint]', [("FF", True), ("ff", False)])
        self.check('r = text .printf ["%+d", int]', [("+5", True), ("5", False), ("-5", True)])
        self.check('r = text .printf ["%c", uint]', [("A", True), ("AB", False)])
        self.check('r = text .printf ["%d%%", uint]', [("5%", True), ("5", False)])

    def test_floats(self):
        self.check('r = text .printf ["%.2f", float]', [("3.14", True), ("3.1", False), ("3.141", False)])
        self.check('r = text .printf ["%5.1f", float]', [("  3.1", True), ("3.1", False)])

    def test_data_items_may_be_types_or_literals(self):
        self.check('r = text .printf ["%d", 1..20]', [("7", True), ("70", False)])
        self.check('r = text .printf ["%04x", 19]', [("0013", True), ("0014", False)])

    def test_rfc_9741_example(self):
        schema = 'hexlabel<K> = text .printf (["0x%04x", K])\nr = hexlabel<uint .size 1>'
        self.assertTrue(ok(schema, "0x00ff", "r"))
        self.assertFalse(ok(schema, "0x0100", "r"))

    def test_format_may_be_computed(self):
        self.check('r = text .printf [("0x" .cat "%02x"), uint]', [("0x0a", True), ("0xa", False)])

    def test_schema_errors(self):
        self.refuses('r = text .printf ["%d"]', fragment="1 conversion")
        self.refuses('r = text .printf ["%ld", int]', fragment="length modifiers")
        self.refuses('r = text .printf ["%*d", int]', 'r = text .printf ["%n", int]',
                     'r = text .printf ["%a", float]', 'r = text .printf ["%", int]')
        self.refuses('r = text .printf [int]', 'r = text .printf "x"', 'r = text .printf [* int]')
        self.refuses('r = bytes .printf ["%d", int]', 'r = text .printf ["%d %d", int]')

    def test_helpers(self):
        spec = conversions.parse_format("a%03db")
        self.assertEqual(len(spec), 3)

    def test_matching_takes_the_first_split_and_is_bounded(self):
        parts = conversions.parse_format("%s%s%s")
        self.assertEqual(conversions.match_printf(parts, "abc"), ["", "", "abc"])
        self.assertIsNone(conversions.match_printf(conversions.parse_format("%d-%d"), "1x2"))
        with self.assertRaises(conversions.PrintfLimit):
            conversions.match_printf(conversions.parse_format("%s%s%s%s%sX"), "a" * 3000)

    def test_hostile_formats_do_not_stall(self):
        # adjacent conversions and a literal that is missing used to be a regular expression's worst case
        validator = Validator('r = text .printf ["%s%s%s%s%sX", tstr, tstr, tstr, tstr, tstr]')
        result = validator.validate(cbor.encode("a" * 3000))
        self.assertFalse(result.valid)
        self.assertIn("too many steps", str(result.errors[0]))
        self.assertTrue(validator.validate(cbor.encode("abcdX")).valid)
        floats = Validator('r = text .printf ["%f%f%f%fX", float, float, float, float]')
        self.assertFalse(floats.validate(cbor.encode("1" * 3000)).valid)


class Join(Cases, unittest.TestCase):
    def test_literals_and_types(self):
        self.check('r = text .join ["a", tstr, "c"]', [("abc", True), ("ac", True), ("ab", False)])

    def test_elements_of_other_types(self):
        schema = 'r = text .join ["id-", (tstr .base10 uint)]'
        self.check(schema, [("id-42", True), ("id-x", False), ("id-", False), ("42", False)])

    def test_nested_groups_are_flattened(self):
        self.check('r = text .join ["a", ("b", "c")]', [("abc", True), ("ab", False)])

    def test_ipv4(self):
        schema = ('r = text .join [octet, ".", octet, ".", octet, ".", octet]\n'
                  'octet = tstr .base10 (uint .le 255)')
        self.check(schema, [("1.2.3.4", True), ("255.0.0.1", True), ("1.2.3", False), ("1.2.3.256", False),
                            ("1.2.3.4.5", False)])

    def test_ambiguous_splits_are_all_tried(self):
        self.check('r = text .join [(tstr .size (1..2)), (tstr .size (1..2))]', [("abc", True), ("a", False), ("abcde", False)])

    def test_bytes(self):
        self.check("r = bytes .join [h'01', h'02']", [(b"\x01\x02", True), (b"\x01", False), ("0102", False)])

    def test_the_data_must_be_text_or_bytes_to_match(self):
        self.check('r = text .join ["a"]', [(1, False), (b"a", False), ("a", True)])

    def test_work_is_bounded(self):
        validator = Validator('r = text .join [tstr, tstr, tstr, tstr, tstr, tstr]')
        # a long string with very many ways to split it is not accepted, and says why
        result = validator.validate(cbor.encode("a" * 2000))
        self.assertFalse(result.valid)
        self.assertIn("too many ways", str(result.errors[0]))
        self.assertTrue(validator.validate(cbor.encode("a" * 20)).valid)

    def test_schema_errors(self):
        self.refuses('r = text .join [* tstr]', 'r = text .join [int]', 'r = text .join "a"',
                     'r = text .join [1, 2]', fragment="join")


if __name__ == "__main__":
    unittest.main()
