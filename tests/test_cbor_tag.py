"""``CBORTag`` is how a tag is held, and a (tag, value) tuple is not one (#78)."""

import copy
import json
import pickle
import sys
import unittest
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import Validator, cbor, json_codec
from cddl_verifier.cbor import CBORKey, CBORTag, UNDEFINED, decode, encode


class TestValue(unittest.TestCase):
    def test_fields_and_repr(self):
        tag = CBORTag(32, "https://example.com")
        self.assertEqual((tag.tag, tag.value), (32, "https://example.com"))
        self.assertEqual(repr(tag), "CBORTag(32, 'https://example.com')")

    def test_unpacks(self):
        number, content = CBORTag(1, 100)
        self.assertEqual((number, content), (1, 100))
        self.assertEqual(list(CBORTag(1, [2])), [1, [2]])

    def test_equal_only_to_a_tag(self):
        self.assertEqual(CBORTag(1, 2), CBORTag(1, 2))
        self.assertNotEqual(CBORTag(1, 2), CBORTag(2, 2))
        self.assertNotEqual(CBORTag(1, 2), CBORTag(1, 3))
        self.assertNotEqual(CBORTag(1, 2), (1, 2))
        self.assertNotEqual((1, 2), CBORTag(1, 2))
        self.assertNotEqual(CBORTag(1, 2), [1, 2])

    def test_nested_tags_compare_by_value(self):
        self.assertEqual(CBORTag(1, CBORTag(2, [3])), CBORTag(1, CBORTag(2, [3])))
        self.assertNotEqual(CBORTag(1, CBORTag(2, [3])), CBORTag(1, (2, [3])))

    def test_hash(self):
        self.assertEqual(hash(CBORTag(1, "a")), hash(CBORTag(1, "a")))
        self.assertEqual(len({CBORTag(1, "a"), CBORTag(1, "a"), CBORTag(2, "a")}), 2)
        with self.assertRaises(TypeError):
            hash(CBORTag(1, [1]))     # a list is not hashable, as in a tuple

    def test_immutable(self):
        tag = CBORTag(1, 2)
        for name in ("tag", "value", "other"):
            with self.subTest(name):
                with self.assertRaises(AttributeError):
                    setattr(tag, name, 5)
        with self.assertRaises(AttributeError):
            del tag.tag

    def test_copy_deepcopy_pickle(self):
        tag = CBORTag(1, {"a": [1, 2]})
        for clone in (copy.copy(tag), copy.deepcopy(tag), pickle.loads(pickle.dumps(tag))):
            self.assertEqual(clone, tag)
        self.assertIsNot(copy.deepcopy(tag).value, tag.value)

    def test_the_tag_number_is_checked(self):
        for number in (True, 1.0, "1", None):
            with self.subTest(number=number):
                with self.assertRaises(TypeError):
                    CBORTag(number, 0)
        for number in (-1, 2 ** 64):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    CBORTag(number, 0)
        self.assertEqual(CBORTag(0, 0).tag, 0)
        self.assertEqual(CBORTag(2 ** 64 - 1, 0).tag, 2 ** 64 - 1)


ROUND_TRIPS = [
    ("epoch time", "c11a514b67b0", CBORTag(1, 1363896240)),
    ("uri", "d82076687474703a2f2f7777772e6578616d706c652e636f6d",
     CBORTag(32, "http://www.example.com")),
    ("self-described", "d9d9f701", CBORTag(55799, 1)),
    ("tag around a tag", "c1c202", CBORTag(1, CBORTag(2, 2))),
    ("tag around an array", "c482211904d2", CBORTag(4, [-2, 1234])),
    ("tag around a map", "d901f5a10102", CBORTag(501, {1: 2})),
    ("tags in an array", "82c101c202", [CBORTag(1, 1), CBORTag(2, 2)]),
    ("tag as a map value", "a161a1c1616c", None),
    ("in-range bignum stays a tag", "c24105", CBORTag(2, b"\x05")),
    ("largest tag number", "dbffffffffffffffff00", CBORTag(2 ** 64 - 1, 0)),
]


class TestRoundTrip(unittest.TestCase):
    def test_decode_gives_a_tag(self):
        for description, hex_text, expected in ROUND_TRIPS:
            if expected is None:
                continue
            with self.subTest(description):
                self.assertEqual(decode(bytes.fromhex(hex_text)), expected)

    def test_encode_writes_it_back(self):
        for description, hex_text, expected in ROUND_TRIPS:
            if expected is None:
                continue
            with self.subTest(description):
                self.assertEqual(encode(expected).hex(), hex_text)

    def test_a_decoded_tag_is_a_tag_not_a_tuple(self):
        item = decode(bytes.fromhex("c101"))
        self.assertIsInstance(item, CBORTag)
        self.assertNotIsInstance(item, tuple)

    def test_bignums_outside_the_64_bit_range_are_still_integers(self):
        big = 2 ** 70
        self.assertEqual(decode(encode(big)), big)
        self.assertEqual(decode(encode(-big)), -big)

    def test_keep_encoding_still_gives_a_tag(self):
        self.assertEqual(decode(bytes.fromhex("c11a514b67b0"), keep_encoding=True),
                         CBORTag(1, 1363896240))

    def test_canonical_encoding(self):
        data = {CBORTag(2, 1): 0, "a": CBORTag(1, {2: 1, 1: 2})}
        raw = encode(data, canonical=True)
        self.assertEqual(decode(raw), {CBORKey(CBORTag(2, 1)): 0, "a": CBORTag(1, {1: 2, 2: 1})})

    def test_a_tag_as_a_map_key(self):
        raw = bytes.fromhex("a2c10200c10300")
        data = decode(raw)
        self.assertEqual(set(data), {CBORKey(CBORTag(1, 2)), CBORKey(CBORTag(1, 3))})
        self.assertEqual(data[CBORKey(CBORTag(1, 2))], 0)
        self.assertEqual(encode(data), raw)
        self.assertNotEqual(CBORKey(CBORTag(1, [])), CBORKey([1, []]))
        self.assertNotEqual(CBORKey(CBORTag(1, 2)), CBORKey(CBORTag(1, 3)))

    def test_a_tag_among_other_values(self):
        data = {"t": CBORTag(1, 5), "u": UNDEFINED, "l": [CBORTag(2, b"\x01"), None]}
        self.assertEqual(decode(encode(data)), data)


class TestTheOldTupleFormIsRefused(unittest.TestCase):
    AMBIGUOUS = [(1, 2), (1, "a"), (32, "http://a"), (0, []), (2 ** 64 + 5, None)]

    def test_encode_asks_which_was_meant(self):
        for value in self.AMBIGUOUS:
            with self.subTest(value=value):
                with self.assertRaises(TypeError) as cm:
                    encode(value)
                self.assertIn("CBORTag", str(cm.exception))
                self.assertIn("list", str(cm.exception))

    def test_nested_and_canonical_and_in_keys(self):
        for build in (lambda t: [t], lambda t: {"a": t}, lambda t: {t: 1},
                      lambda t: CBORTag(5, t)):
            for canonical in (False, True):
                with self.subTest(build=build(None), canonical=canonical):
                    with self.assertRaises(TypeError):
                        encode(build((1, "a")), canonical=canonical)

    def test_every_other_tuple_is_still_an_array(self):
        for value, hex_text in ((("a", 1), "826161" + "01"), ((1, 2, 3), "83010203"),
                                ((-1, 2), "822002"), ((True, 1), "82f501"),
                                ((1,), "8101"), ((), "80"), (([1], 2), "8281" + "0102")):
            with self.subTest(value=value):
                self.assertEqual(encode(value).hex(), hex_text)

    def test_the_cbor_class_refuses_too(self):
        with self.assertRaises(TypeError):
            cbor.CBOR((1, "a")).encode()
        self.assertEqual(cbor.CBOR(CBORTag(1, "a")).encode(), encode(CBORTag(1, "a")))

    def test_the_validator_says_a_tuple_is_not_a_tag(self):
        validator = Validator("r = #6.1(uint)")
        self.assertTrue(validator.validate(CBORTag(1, 5)).valid)
        result = validator.validate((1, 5))
        self.assertFalse(result.valid)
        self.assertIn("not tagged", result.errors[0])
        self.assertIn("use CBORTag", result.errors[0])
        field = Validator("r = {a: #6.1(uint)}").validate({"a": (1, 5)})
        self.assertFalse(field.valid)
        self.assertIn("use CBORTag", field.errors[0])


class TestValidatorAndEdn(unittest.TestCase):
    def test_validates_decoded_and_given_tags_the_same_way(self):
        schema = "r = {u: #6.32(tstr), t: #6.1(uint), n: #6.100(#6.0(tstr))}"
        value = {"u": CBORTag(32, "http://a"), "t": CBORTag(1, 5),
                 "n": CBORTag(100, CBORTag(0, "2020-01-01T00:00:00Z"))}
        validator = Validator(schema)
        self.assertTrue(validator.validate(value).valid)
        self.assertTrue(validator.validate(encode(value)).valid)
        wrong = dict(value, t=CBORTag(2, 5))
        self.assertFalse(validator.validate(wrong).valid)
        self.assertFalse(validator.validate(encode(wrong)).valid)

    def test_a_tag_in_a_choice_and_an_array(self):
        validator = Validator("r = [* s]\ns = #6.7(uint) / tstr")
        self.assertTrue(validator.validate([CBORTag(7, 1), "x"]).valid)
        self.assertFalse(validator.validate([CBORTag(8, 1)]).valid)

    def test_edn(self):
        validator = Validator("r = {u: #6.32(tstr)}")
        data = {"u": CBORTag(32, "http://a")}
        edn = validator.to_edn(data)
        self.assertIn('32("http://a")', edn)
        self.assertEqual(validator.to_edn(encode(data)), edn)


class TestJson(unittest.TestCase):
    def test_typed_json_round_trips_a_tag(self):
        raw = encode({"t": CBORTag(1, 5), "n": CBORTag(100, [CBORTag(2, b"\x01")])})
        typed = json_codec.cbor_to_json(raw, typed=True)
        self.assertEqual(json.loads(typed)["t"], {"$cbor": "tag", "$tag": 1, "$value": 5})
        self.assertEqual(json_codec.json_to_cbor(typed), raw)

    def test_untyped_json_gives_the_value(self):
        raw = encode({"t": CBORTag(1, 5)})
        self.assertEqual(json.loads(json_codec.cbor_to_json(raw)), {"t": 5})

    def test_json_to_cbor_makes_a_tag(self):
        raw = json_codec.json_to_cbor('{"$cbor": "tag", "$tag": 32, "$value": "http://a"}')
        self.assertEqual(decode(raw), CBORTag(32, "http://a"))

    def test_a_bad_tag_annotation_is_a_value_error(self):
        for tag in ("-1", '"x"', "1.5", "true", str(2 ** 64)):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError) as cm:
                    json_codec.json_to_cbor('{"$cbor": "tag", "$tag": %s, "$value": 1}' % tag)
                self.assertIn("$tag", str(cm.exception))

    def test_the_json_encoder_writes_a_tuple_as_an_array(self):
        self.assertEqual(json.loads(json.dumps({"t": (1, "a")}, cls=json_codec.CBORJSONEncoder)),
                         {"t": [1, "a"]})
        encoder = json_codec.CBORJSONEncoder(typed=True)
        self.assertEqual(json.loads(encoder.encode({"t": CBORTag(1, "a")}))["t"]["$tag"], 1)


class TestExport(unittest.TestCase):
    def test_exported_from_the_cbor_module(self):
        self.assertIs(cbor.CBORTag, CBORTag)
        self.assertIn("CBORTag", cbor.__all__)


if __name__ == "__main__":
    unittest.main()
