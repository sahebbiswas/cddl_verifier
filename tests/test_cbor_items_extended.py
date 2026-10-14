#!/usr/bin/env python3
"""
Items the decoder used to refuse, and how an item was written (#168, #171):
indefinite-length strings, arrays and maps, ``undefined`` and the other simple
values decode; ``keep_encoding`` records the additional information of each
item's head; CDDL checks them (``undefined``, ``#7.N``, ``float32``,
``#M.N``).
"""

import copy
import json
import pickle
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import CBORDecodeError, Validator, validate
from cddl_verifier._cbor import CBOR, CBORKey, CBORTruncatedError, CBORUnsupportedError
from cddl_verifier._cbor_values import UNDEFINED, CBORSimple, head_info
from cddl_verifier._json_codec import cbor_to_json, json_to_cbor
from cddl_verifier.cbor import decode, diag_dump, encode
from cddl_verifier.cbor import CBORTag

HEX = bytes.fromhex


class TestIndefiniteLength(unittest.TestCase):
    # RFC 8949 Appendix A
    VECTORS = [
        ("5f42010243030405ff", b"\x01\x02\x03\x04\x05"),
        ("5fff", b""),
        ("7f657374726561646d696e67ff", "streaming"),
        ("7f61616162ff", "ab"),
        ("9fff", []),
        ("9f018202039f0405ffff", [1, [2, 3], [4, 5]]),
        ("9f01820203820405ff", [1, [2, 3], [4, 5]]),
        ("83018202039f0405ff", [1, [2, 3], [4, 5]]),
        ("83019f0203ff820405", [1, [2, 3], [4, 5]]),
        ("9f0102030405060708090a0b0c0d0e0f101112131415161718181819ff", list(range(1, 26))),
        ("bf61610161629f0203ffff", {"a": 1, "b": [2, 3]}),
        ("826161bf61626163ff", ["a", {"b": "c"}]),
        ("bf6346756ef563416d7421ff", {"Fun": True, "Amt": -2}),
        ("bfff", {}),
        ("9f9fffff", [[]]),
    ]

    def test_appendix_a_vectors(self):
        for encoded, expected in self.VECTORS:
            with self.subTest(encoded=encoded):
                self.assertEqual(decode(HEX(encoded)), expected)

    def test_text_chunks_join_on_character_boundaries(self):
        self.assertEqual(decode(HEX("7f62c3a9626162ff")), "éab")

    def test_re_encoding_is_definite_length(self):
        for encoded, expected in self.VECTORS:
            with self.subTest(encoded=encoded):
                again = encode(decode(HEX(encoded)))
                self.assertNotIn(again[0], (0x5f, 0x7f, 0x9f, 0xbf))
                self.assertEqual(decode(again), expected)

    def test_validator_sees_the_value(self):
        for schema, encoded, expected in [
            ("r = [* uint]", "9f010203ff", True),
            ("r = [* uint]", "9f016161ff", False),
            ("r = {a: [* uint], b: tstr}", "bf61619f0102ff61626161ff", True),
            ("r = {a: [* uint], b: tstr}", "bf61619f0102ff616201ff", False),
            ("r = {a: tstr}", "bf61617f61616162ffff", True),
            ("r = bstr .size 3", "5f4101420203ff", True),
            ("r = bstr .size 2", "5f4101420203ff", False),
            ("r = tstr .size 3", "7f6161626263ff", True),
        ]:
            with self.subTest(schema=schema, encoded=encoded):
                self.assertEqual(validate(schema, HEX(encoded)).valid, expected)

    def test_malformed(self):
        for name, encoded, error, offset in [
            ("break outside an item", "ff", CBORDecodeError, 0),
            ("break inside a definite array", "82 01 ff", CBORDecodeError, 2),
            ("no break (array)", "9f01", CBORTruncatedError, 2),
            ("no break (map)", "bf6161 01", CBORTruncatedError, 4),
            ("no break (bytes)", "5f4101", CBORTruncatedError, 3),
            ("no break (text)", "7f6161", CBORTruncatedError, 3),
            ("map ends after a key", "bf6161ff", CBORDecodeError, 3),
            ("text chunk in a byte string", "5f6161ff", CBORDecodeError, 1),
            ("byte chunk in a text string", "7f4161ff", CBORDecodeError, 1),
            ("nested indefinite chunk", "5f5fffff", CBORDecodeError, 1),
            ("invalid UTF-8 in a chunk", "7f61ffff", CBORDecodeError, 2),
            ("character split between chunks", "7f61c361a9ff", CBORDecodeError, 2),
            ("indefinite integer", "1f", CBORDecodeError, 0),
            ("indefinite negative integer", "3f", CBORDecodeError, 0),
            ("indefinite tag", "df01", CBORDecodeError, 0),
            ("reserved information 28", "1c", CBORDecodeError, 0),
            ("reserved simple information 28", "fc", CBORDecodeError, 0),
            ("duplicate key", "bf6161 01 6161 02ff", CBORDecodeError, 4),
        ]:
            with self.subTest(case=name):
                with self.assertRaises(error) as caught:
                    decode(HEX(encoded.replace(" ", "")))
                self.assertEqual(caught.exception.offset, offset)

    def test_locations_follow_indefinite_items(self):
        data, locations = CBOR.loads_with_locations(HEX("9f01bf616102ff03ff"))
        self.assertEqual(data, [1, {"a": 2}, 3])
        self.assertEqual(locations.span((0,)), (1, 2))
        self.assertEqual(locations.span((2,)), (7, 8))
        self.assertEqual(locations.span((1, "a")), (5, 6))

    def test_unsupported_error_is_kept_for_compatibility(self):
        self.assertTrue(issubclass(CBORUnsupportedError, CBORDecodeError))
        self.assertTrue(issubclass(CBORUnsupportedError, NotImplementedError))


class TestSimpleValues(unittest.TestCase):

    def test_undefined(self):
        self.assertIs(decode(HEX("f7")), UNDEFINED)
        self.assertEqual(repr(UNDEFINED), "undefined")
        self.assertIs(pickle.loads(pickle.dumps(UNDEFINED)), UNDEFINED)
        self.assertIs(copy.deepcopy(UNDEFINED), UNDEFINED)

    def test_other_simple_values(self):
        for encoded, number in [("e0", 0), ("f0", 16), ("f3", 19), ("f820", 32), ("f8ff", 255)]:
            with self.subTest(encoded=encoded):
                value = decode(HEX(encoded))
                self.assertEqual(value, CBORSimple(number))
                self.assertEqual(repr(value), f"simple({number})")
                self.assertEqual(encode(value), HEX(encoded))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                self.assertEqual(hash(value), hash(CBORSimple(number)))

    def test_simple_value_is_not_an_integer(self):
        self.assertNotEqual(CBORSimple(16), 16)
        self.assertFalse(validate("r = uint", encode(CBORSimple(16))).valid)

    def test_numbers_that_are_not_simple_values(self):
        for number in (20, 21, 22, 23, 24, 31, -1, 256, True, "x"):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    CBORSimple(number)

    def test_two_byte_form_below_32_is_malformed(self):
        for encoded in ("f800", "f818", "f81f"):
            with self.subTest(encoded=encoded):
                with self.assertRaises(CBORDecodeError):
                    decode(HEX(encoded))

    def test_round_trip(self):
        for value in (UNDEFINED, CBORSimple(16), [UNDEFINED, CBORSimple(40), None]):
            with self.subTest(value=value):
                self.assertEqual(decode(encode(value)), value)
                self.assertEqual(decode(encode(value, canonical=True)), value)

    def test_as_map_keys(self):
        data = decode(HEX("a2f700f001"))
        self.assertEqual(data, {CBORKey(UNDEFINED): 0, CBORKey(CBORSimple(16)): 1})
        self.assertEqual(encode(data), HEX("a2f700f001"))
        with self.assertRaises(CBORDecodeError):
            decode(HEX("a2f700f701"))      # the same key twice
        self.assertEqual(len(decode(HEX("a2f700f001"))), 2)

    def test_canonical_key_order(self):
        data = {CBORKey(CBORSimple(16)): 1, 5: 2, CBORKey(UNDEFINED): 3}
        self.assertEqual(encode(data, canonical=True), HEX("a305 02 f0 01 f7 03".replace(" ", "")))


class TestKeptEncoding(unittest.TestCase):

    def test_plain_by_default(self):
        for encoded in ("1805", "fb3ff8000000000000", "9f01ff", "580101"):
            with self.subTest(encoded=encoded):
                value = decode(HEX(encoded))
                self.assertIn(type(value), (int, float, list, bytes))

    def test_preferred_values_stay_plain(self):
        for encoded in ("05", "1818", "190100", "8101", "4101", "6161", "a0"):
            with self.subTest(encoded=encoded):
                value = CBOR.loads(HEX(encoded), keep_encoding=True)
                self.assertIn(type(value), (int, list, bytes, str, dict))
                self.assertFalse(hasattr(value, "info"))

    def test_other_widths_are_recorded(self):
        for encoded, expected, info in [
            ("1805", 5, 24), ("1900ff", 255, 25), ("1a000000ff", 255, 26),
            ("1b00000000000000ff", 255, 27), ("3805", -6, 24),
            ("580101", b"\x01", 24), ("780161", "a", 24), ("980101", [1], 24),
            ("b800", {}, 24), ("9f01ff", [1], 31), ("bf0000ff", {0: 0}, 31),
            ("5f4101ff", b"\x01", 31), ("7f6161ff", "a", 31),
        ]:
            with self.subTest(encoded=encoded):
                value = CBOR.loads(HEX(encoded), keep_encoding=True)
                self.assertEqual(value, expected)
                self.assertEqual(type(value).__name__, type(expected).__name__)
                self.assertEqual(head_info(value), info)

    def test_floats_always_carry_their_width(self):
        for encoded, info in (("f93c00", 25), ("fa3fc00000", 26), ("fb3ff8000000000000", 27)):
            with self.subTest(encoded=encoded):
                value = CBOR.loads(HEX(encoded), keep_encoding=True)
                self.assertEqual(value, 1.5 if info != 25 else 1.0)
                self.assertEqual(head_info(value), info)

    def test_head_info_of_plain_values(self):
        for value, info in [(5, 5), (23, 23), (24, 24), (255, 24), (256, 25), (65535, 25),
                            (65536, 26), (2 ** 32, 27), (-1, 0), (-25, 24),
                            ([1, 2], 2), ({}, 0), (b"a" * 24, 24), ("é", 2)]:
            with self.subTest(value=value):
                self.assertEqual(head_info(value), info)
        for value in (1.5, True, None, 2 ** 70, UNDEFINED, CBORTag(1, "a")):
            with self.subTest(value=value):
                self.assertIsNone(head_info(value))

    def test_nested_values_keep_theirs(self):
        value = CBOR.loads(HEX("9f1805fa3fc00000a1180501ff"), keep_encoding=True)
        self.assertEqual(head_info(value), 31)
        self.assertEqual(head_info(value[0]), 24)
        self.assertEqual(head_info(value[1]), 26)
        self.assertEqual(head_info(list(value[2])[0]), 24)

    def test_recorded_values_behave_as_plain_ones(self):
        value = CBOR.loads(HEX("1805"), keep_encoding=True)
        self.assertEqual(value, 5)
        self.assertEqual({5: "a"}[value], "a")
        self.assertEqual(value + 1, 6)
        self.assertEqual(json.dumps(value), "5")
        self.assertEqual(encode(value), HEX("05"))
        self.assertEqual(type(pickle.loads(pickle.dumps(value))), int)
        self.assertEqual(type(copy.deepcopy(value)), int)
        data = CBOR.loads(HEX("9f1805bf6161fb3ff8000000000000ffff"), keep_encoding=True)
        self.assertEqual(encode(data), HEX("82 05 a1 6161 fb3ff8000000000000".replace(" ", "")))
        self.assertEqual(data, [5, {"a": 1.5}])

    def test_as_map_keys(self):
        data = CBOR.loads(HEX("a1180501"), keep_encoding=True)
        self.assertEqual(data, {5: 1})
        self.assertEqual(data[5], 1)


class TestJSONAndDiagnostics(unittest.TestCase):

    def test_typed_json_round_trip(self):
        data = HEX("83f7f0f820")
        text = cbor_to_json(data, typed=True)
        self.assertEqual(json.loads(text), [{"$cbor": "undefined"},
                                            {"$cbor": "simple", "$value": 16},
                                            {"$cbor": "simple", "$value": 32}])
        self.assertEqual(json_to_cbor(text), data)

    def test_untyped_json_is_lossy(self):
        self.assertEqual(json.loads(cbor_to_json(HEX("83f7f0f820"))), [None, 16, 32])

    def test_json_keys(self):
        self.assertEqual(json.loads(cbor_to_json(HEX("a2f700f001"))),
                         {"undefined": 0, "simple(16)": 1})
        typed = cbor_to_json(HEX("a2f700f001"), typed=True)
        self.assertEqual(json_to_cbor(typed), HEX("a2f700f001"))

    def test_indefinite_items_in_json(self):
        self.assertEqual(json.loads(cbor_to_json(HEX("bf61619f0102ffff"))), {"a": [1, 2]})

    def test_diagnostic_dump_of_indefinite_items(self):
        dump = diag_dump(HEX("9f01bf616102ff5f4101ffff"))
        for expected in ("array(*)", "map(*)", "bytes(*)", "chunk: bytes(1)", "break"):
            self.assertIn(expected, dump)
        self.assertEqual(dump.count("break"), 3)

    def test_diagnostic_dump_of_simple_values(self):
        dump = diag_dump(HEX("84f7f0f820e3"))
        for expected in ("undefined", "simple(16)", "simple(32)", "simple(3)"):
            self.assertIn(expected, dump)

    def test_diagnostic_dump_reports_malformed_heads(self):
        self.assertIn("break with no indefinite-length item", diag_dump(HEX("ff")))
        self.assertIn("not well-formed", diag_dump(HEX("1c")))
        self.assertIn("not well-formed", diag_dump(HEX("1f")))
        self.assertIn("no break", diag_dump(HEX("9f01")))

    def test_edn(self):
        text = Validator("r = [* any]").to_edn(HEX("83f7f0f820"))
        for expected in ("undefined", "simple(16)", "simple(32)"):
            self.assertIn(expected, text)


class TestCDDLChecks(unittest.TestCase):

    def check(self, schema, cases):
        validator = Validator(schema)
        for data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validator.validate(HEX(data))
                self.assertEqual(result.valid, expected, result.errors)

    def test_undefined(self):
        self.check("r = undefined", [("f7", True), ("f6", False), ("f4", False), ("01", False)])
        self.check("r = {a: undefined}", [("a16161f7", True), ("a16161f6", False)])
        self.check("r = [* undefined]", [("82f7f7", True), ("82f7f6", False), ("80", True)])
        self.check("r = undefined / null", [("f7", True), ("f6", True), ("f4", False)])
        self.check("r = {undefined => int}", [("a1f701", True), ("a0", False), ("a1f601", False)])

    def test_undefined_is_not_null(self):
        self.check("r = null", [("f7", False), ("f6", True)])
        self.check("r = nil", [("f7", False)])
        self.check("r = {a: null}", [("a16161f7", False), ("a16161f6", True)])

    def test_undefined_is_any(self):
        self.check("r = any", [("f7", True), ("f0", True)])
        self.check("r = {a: any}", [("a16161f7", True)])
        self.check("r = [* any]", [("82f7f0", True)])
        self.check("r = #", [("f7", True)])

    def test_undefined_matches_no_other_type(self):
        for type_name in ("bool", "uint", "int", "tstr", "bstr", "float", "float16", "true",
                          "false", "[* any]", "{* any => any}", "1", '"a"'):
            with self.subTest(type=type_name):
                self.assertFalse(Validator(f"r = {type_name}").validate(HEX("f7")).valid)

    def test_simple_values(self):
        self.check("r = #7.16", [("f0", True), ("f1", False), ("f7", False), ("10", False)])
        self.check("r = #7.255", [("f8ff", True), ("f820", False)])
        self.check("r = #7", [("f0", True), ("f820", True), ("f7", True), ("01", False)])
        self.check("r = {a: #7.32}", [("a16161f820", True), ("a16161f821", False)])
        self.check("r = [* #7.<0..19>]", [("82e0f3", True), ("82e0f4", False)])

    def test_floats_follow_the_encoding(self):
        half, single, double = "f93c00", "fa3fc00000", "fb3ff8000000000000"
        for type_name, accepts in [("float16", {half}), ("float32", {single}),
                                   ("float64", {double}),
                                   ("float16-32", {half, single}),
                                   ("float32-64", {single, double}),
                                   ("float", {half, single, double})]:
            for shape, prefix in (("r = %s", ""), ("r = {a: %s}", "a16161"),
                                  ("r = [* %s]", "81")):
                for encoded in (half, single, double):
                    schema = shape % type_name
                    with self.subTest(schema=schema, encoded=encoded):
                        result = Validator(schema).validate(HEX(prefix + encoded))
                        self.assertEqual(result.valid, encoded in accepts, result.errors)

    def test_float_errors_name_the_width(self):
        result = validate("r = float16", HEX("fb3ff8000000000000"))
        self.assertFalse(result.valid)
        self.assertIn("expected float16, got float64 1.5", result.errors[0])
        self.assertIn("got undefined", validate("r = uint", HEX("f7")).errors[0])
        self.assertIn("got simple(16)", validate("r = tstr", HEX("f0")).errors[0])

    def test_float_controls_still_apply(self):
        self.check("r = float32 .ge 1.0", [("fa3fc00000", True), ("fa3f000000", False),
                                           ("f93c00", False)])
        self.check("r = float16-32 .le 2.0", [("fa3fc00000", True), ("fa40800000", False)])

    def test_float_values_given_as_python_objects(self):
        # no wire encoding: a float fits the widths it can be written in without loss
        for type_name, value, expected in [
            ("float16", 1.5, True), ("float16", 1.1, False), ("float16", 1e300, False),
            ("float32", 1.5, True), ("float32", 1.1, False), ("float32", 1e300, False),
            ("float64", 1.1, True), ("float64", 1e300, True),
            ("float16", float("inf"), True), ("float16", float("nan"), True),
        ]:
            with self.subTest(type=type_name, value=value):
                self.assertEqual(validate(f"r = {type_name}", value).valid, expected)

    def test_head_widths_in_every_position(self):
        for ref, encoded, expected in [
            ("#0.24", "1805", True), ("#0.5", "1805", False), ("#0.5", "05", True),
            ("#3.24", "78 01 61", True), ("#3.1", "78 01 61", False),
            ("#4.31", "9f01ff", True), ("#4.1", "9f01ff", False),
        ]:
            data = HEX(encoded.replace(" ", ""))
            for shape, prefix in (("r = %s", b""), ("r = {a: %s}", HEX("a16161")),
                                  ("r = [* %s]", HEX("81"))):
                schema = shape % ref
                with self.subTest(schema=schema, encoded=encoded):
                    self.assertEqual(Validator(schema).validate(prefix + data).valid, expected)

    def test_nested_embedded_cbor_keeps_head_widths(self):
        # 'bstr .cbor float32': the embedded item's own width is checked
        self.assertTrue(validate("r = bstr .cbor float32", HEX("45fa3fc00000")).valid)
        self.assertFalse(validate("r = bstr .cbor float32", HEX("43f93c00")).valid)
        self.assertTrue(validate("r = {a: bstr .cbor float32}",
                                 HEX("a1616145fa3fc00000")).valid)

    def test_unique_widths_in_choices(self):
        self.check("r = float16 / float32", [("f93c00", True), ("fa3fc00000", True),
                                              ("fb3ff8000000000000", False)])


if __name__ == "__main__":
    unittest.main()
