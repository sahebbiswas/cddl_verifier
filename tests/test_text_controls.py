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
                     'r = text .printf ["%", int]')
        self.refuses('r = text .printf [int]', 'r = text .printf "x"', 'r = text .printf [* int]')
        self.refuses('r = bytes .printf ["%d", int]', 'r = text .printf ["%d %d", int]')

    def test_helpers(self):
        spec = conversions.parse_format("a%03db")
        self.assertEqual(len(spec), 3)

    def test_matching_lists_every_split_shortest_first(self):
        parts = conversions.parse_format("%s%s")
        self.assertEqual(list(conversions.printf_matches(parts, "abc")),
                         [["", "abc"], ["a", "bc"], ["ab", "c"], ["abc", ""]])
        self.assertEqual(list(conversions.printf_matches(conversions.parse_format("%s-%s"), "a-b-c")),
                         [["a", "b-c"], ["a-b", "c"]])
        self.assertEqual(list(conversions.printf_matches(conversions.parse_format("%d-%d"), "1x2")), [])
        self.assertEqual(conversions.match_printf(parts, "abc"), ["", "abc"])

    def test_the_step_budget_covers_all_the_splits(self):
        parts = conversions.parse_format("%s%s%s%s")
        with self.assertRaises(conversions.PrintfLimit):
            for _ in conversions.printf_matches(parts, "a" * 300):
                pass
        # a small budget still gives the first split, and stops on the way to the next ones
        iterator = conversions.printf_matches(conversions.parse_format("%s%s"), "a" * 50, budget=60)
        self.assertEqual(next(iterator), ["", "a" * 50])
        with self.assertRaises(conversions.PrintfLimit):
            list(iterator)

    def test_matching_takes_the_first_split_and_is_bounded(self):
        parts = conversions.parse_format("%s%s%s")
        self.assertEqual(conversions.match_printf(parts, "abc"), ["", "", "abc"])
        self.assertIsNone(conversions.match_printf(conversions.parse_format("%d-%d"), "1x2"))
        with self.assertRaises(conversions.PrintfLimit):
            conversions.match_printf(conversions.parse_format("%s%s%s%s%sX"), "a" * 3000)

    def test_a_format_with_very_many_parts_is_a_limit_not_a_crash(self):
        with self.assertRaises(conversions.PrintfLimit):
            conversions.match_printf(conversions.parse_format("%s," * 5000), "," * 5000)
        items = ", ".join(["tstr"] * 3000)
        validator = Validator(f'r = text .printf ["{"%s," * 3000}", {items}]')
        result = validator.validate(cbor.encode("," * 3000))
        self.assertFalse(result.valid)
        self.assertIn("too many steps", str(result.errors[0]))

    def test_adjacent_conversions_try_every_split(self):
        self.check('r = text .printf ["%s%s", tstr .size 2, tstr .size 2]',
                   [("abcd", True), ("abc", False), ("abcde", False), ("", False)])
        self.check('r = text .printf ["%s%s", tstr .size 1, tstr .size 3]', [("abcd", True), ("ab", False)])
        self.check('r = text .printf ["%s%s%s", tstr .size 1, tstr .size 2, tstr .size 3]',
                   [("abcdef", True), ("abcde", False)])

    def test_a_literal_inside_a_string_conversion_is_tried_both_ways(self):
        lower = 'tstr .regexp "[a-z]"'
        self.check(f'r = text .printf ["%s-%s", {lower}, {lower}]', [("a-b", True), ("a-b-c", False)])
        self.check('r = text .printf ["%s-%s", tstr .size 3, tstr .size 1]', [("a-b-c", True), ("a-b-cx", False)])
        self.check('r = text .printf ["%s:%d", tstr, 1..5]', [("a:b:3", True), ("a:b:9", False), ("a:3:b", False)])

    def test_the_other_splits_of_numbers_are_tried_too(self):
        self.check('r = text .printf ["%d%d", 1..9, 10..99]', [("123", True), ("12", False)])
        self.check('r = text .printf ["%d%d", 10..99, 1..9]', [("123", True), ("12", False)])
        self.check('r = text .printf ["%d%s", 1..9, tstr .size 2]', [("1ab", True), ("123", True), ("12", False)])

    def test_splits_that_cannot_be_read_back_are_skipped(self):
        # "007" as %d%d: only "0" + "07" (no, 07 is not what %d writes) ... none of the splits is exact
        self.check('r = text .printf ["%d%d", uint, uint]', [("12", True), ("007", False), ("0", False)])

    def test_all_splits_failing_the_types_is_a_bounded_rejection(self):
        validator = Validator('r = text .printf ["%s%s%s%s", tstr .size 9, tstr, tstr, tstr]')
        result = validator.validate(cbor.encode("a" * 400))
        self.assertFalse(result.valid)
        self.assertIn("too many steps", str(result.errors[0]))
        self.assertFalse(validator.validate(cbor.encode("abc")).valid)

    def test_hostile_formats_do_not_stall(self):
        # adjacent conversions and a literal that is missing used to be a regular expression's worst case
        validator = Validator('r = text .printf ["%s%s%s%s%sX", tstr, tstr, tstr, tstr, tstr]')
        result = validator.validate(cbor.encode("a" * 3000))
        self.assertFalse(result.valid)
        self.assertIn("too many steps", str(result.errors[0]))
        self.assertTrue(validator.validate(cbor.encode("abcdX")).valid)
        floats = Validator('r = text .printf ["%f%f%f%fX", float, float, float, float]')
        self.assertFalse(floats.validate(cbor.encode("1" * 3000)).valid)


#: ``(format, value, what C's printf writes)``, from glibc.
C_HEX_FLOATS = [
    ("%a", 1.5, "0x1.8p+0"), ("%A", 1.5, "0X1.8P+0"), ("%a", 0.0, "0x0p+0"), ("%a", -0.0, "-0x0p+0"),
    ("%a", 1.0, "0x1p+0"), ("%a", 0.1, "0x1.999999999999ap-4"), ("%a", 255.5, "0x1.ffp+7"),
    ("%a", -3.0, "-0x1.8p+1"), ("%a", 5e-324, "0x0.0000000000001p-1022"),
    ("%a", 2.2250738585072014e-308, "0x1p-1022"), ("%a", 1.7976931348623157e308, "0x1.fffffffffffffp+1023"),
    ("%.0a", 1.5, "0x2p+0"), ("%.0a", 2.5, "0x1p+1"), ("%.0a", 1.9999999999999998, "0x2p+0"),
    ("%.1a", 0.1, "0x1.ap-4"), ("%.2a", 0.1, "0x1.9ap-4"), ("%.3a", 1.0, "0x1.000p+0"),
    ("%.15a", 1.5, "0x1.800000000000000p+0"), ("%#a", 1.0, "0x1.p+0"), ("%#.0a", 1.0, "0x1.p+0"),
    ("%+a", 1.5, "+0x1.8p+0"), ("% a", 1.5, " 0x1.8p+0"), ("%12a", 1.5, "    0x1.8p+0"),
    ("%-12a", 1.5, "0x1.8p+0    "), ("%012a", -1.5, "-0x0001.8p+0"), ("%012A", 1.5, "0X00001.8P+0"),
    ("%10a", float("inf"), "       inf"), ("%010a", float("inf"), "       inf"),
    ("%a", float("-inf"), "-inf"), ("%A", float("inf"), "INF"), ("%.3a", 255.99, "0x2.000p+7"),
    ("%.1a", 1.96875, "0x2.0p+0"),
]


class HexFloats(Cases, unittest.TestCase):
    """``%a`` and ``%A`` (#233): RFC 9741 leaves out only the length modifiers, ``%p`` and ``%n``."""

    def test_formatting_agrees_with_c(self):
        for fmt, value, expected in C_HEX_FLOATS:
            with self.subTest(fmt=fmt, value=value):
                (spec,) = [part for part in conversions.parse_format(fmt) if not isinstance(part, str)]
                self.assertEqual(conversions.format_float(spec, value), expected)

    def test_a_nan_is_written_without_a_sign(self):
        (spec,) = [part for part in conversions.parse_format("%+a") if not isinstance(part, str)]
        self.assertEqual(conversions.format_float(spec, float("nan")), "+nan")
        (spec,) = [part for part in conversions.parse_format("%A") if not isinstance(part, str)]
        self.assertEqual(conversions.format_float(spec, float("-nan")), "NAN")

    def test_every_finite_value_reads_back_from_its_text(self):
        import random
        import struct
        generator = random.Random(1)
        for fmt in ("%a", "%A", "%.4a", "%#a", "%20a", "%-20a", "%+020a"):
            (spec,) = [part for part in conversions.parse_format(fmt) if not isinstance(part, str)]
            for _ in range(200):
                value = struct.unpack(">d", generator.getrandbits(64).to_bytes(8, "big"))[0]
                if value != value:
                    continue
                text = conversions.format_float(spec, value)
                read = conversions.read_conversion(spec, text)
                self.assertIsNotNone(read, (fmt, value, text))
                if spec.precision is None:
                    self.assertEqual(read, value, (fmt, value, text))

    def test_a_schema_with_a_literal_value(self):
        self.check('r = text .printf ["%a", 1.5]',
                   [("0x1.8p+0", True), ("0x1.8000000000000p+0", False), ("0x3p-1", False), ("0X1.8P+0", False),
                    ("1.5", False)])
        self.check('r = text .printf ["%A", 1.5]', [("0X1.8P+0", True), ("0x1.8p+0", False)])
        self.check('r = text .printf ["%.2a", 1.5]', [("0x1.80p+0", True), ("0x1.8p+0", False), ("0x1.800000000000p+0", False)])
        self.check('r = text .printf ["%#a", 1.0]', [("0x1.p+0", True), ("0x1p+0", False)])
        self.check('r = text .printf ["%012a", -1.5]', [("-0x0001.8p+0", True), ("-0x1.8p+0", False)])

    def test_a_schema_with_a_float_type(self):
        self.check('r = text .printf ["%a", float]',
                   [("0x1.8p+0", True), ("-0x0p+0", True), ("inf", True), ("-inf", True), ("nan", True),
                    ("0x1.999999999999ap-4", True), ("0x0.0000000000001p-1022", True),
                    ("0x1.8P+0", False), ("0x1.80p+0", False), ("0x1.8", False), ("1.5", False), ("", False)])
        self.check('r = text .printf ["%A", float]', [("0X1.8P+0", True), ("INF", True), ("0x1.8p+0", False)])

    def test_a_hex_float_in_a_longer_format(self):
        self.check('r = text .printf ["v=%a;n=%d", float, uint]',
                   [("v=0x1.8p+0;n=3", True), ("v=0x1.8p+0;n=x", False), ("v=0x1.8p+0", False)])
        self.check('r = text .printf ["%a%a", float, float]', [("0x1p+00x1p+0", True), ("0x1p+0 0x1p+0", False)])

    def test_the_value_is_held_to_its_type(self):
        self.check('r = text .printf ["%a", 0.0..1.0]', [("0x1p+0", True), ("0x1.8p+0", False), ("-0x0p+0", True)])

    def test_the_format_is_accepted_and_the_rest_is_still_refused(self):
        Validator('r = text .printf ["%a %A %.3a", float, float, float]')
        self.refuses('r = text .printf ["%la", float]', fragment="length modifiers")
        self.refuses('r = text .printf ["%p", float]', 'r = text .printf ["%n", float]',
                     fragment="not allowed")


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


class ControlCharacterEscapingTests(unittest.TestCase):
    def test_diagnostics_do_not_carry_raw_control_characters(self):
        from cddl_verifier._analyzer import CBORAnalyzer, CDDLParser
        analyzer = CBORAnalyzer(CDDLParser("r = tstr"))
        shown = analyzer._format_value_for_log("a\x1b[2Jb\n")
        self.assertEqual(shown, '"a\\u001b[2Jb\\u000a"')
        self.assertNotIn("\x1b", shown)
