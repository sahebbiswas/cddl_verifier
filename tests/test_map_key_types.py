#!/usr/bin/env python3
"""
Decoded map keys keep their CBOR type (#88).

Keys that are not integers, text, byte strings or null decode to
``CBORKey``, so ``{1: a, true: b}`` keeps both entries, array and map keys
re-encode as arrays and maps, and only true duplicates are rejected.
"""

import copy
import json
import math
import pickle
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator, validate
from cddl_verifier.cbor import CBORDecodeError, CBORKey, decode, encode
from cddl_verifier.json_codec import cbor_to_json, json_to_cbor


class TestRoundTrip(unittest.TestCase):
    """decode → encode gives back the same bytes, whatever the key type."""

    CASES = {
        "array key": "a1820102f6",                      # {[1, 2]: null}
        "nested array key": "a181810100",               # {[[1]]: 0}
        "map key": "a1a1616101f6",                      # {{"a": 1}: null}
        "tag key": "a1c10200",                          # {1(2): 0}
        "tagged array key": "a1c1820102f6",             # {1([1, 2]): null}
        "true and 1": "a201f5f5f4",                     # {1: true, true: false}
        "false and 0": "a200f5f4f4",                    # {0: true, false: false}
        "1.0 and 1": "a201f4fb3ff0000000000000f5",      # {1: false, 1.0: true}
        "0.0 and -0.0": "a2fb000000000000000000fb800000000000000001",
        "every key type": ("a9" "0100" "20" "01" "4101" "02" "616103" "f6" "04"
                           "f5" "05" "fb3ff8000000000000" "06" "8101" "07" "a10101" "08"),
        "nested map with bool key": "a101a2f50101f5",   # {1: {true: 1, 1: true}}
        "array and tag with same content": "a2820181020 0c181020 1".replace(" ", ""),
    }

    def test_round_trip(self):
        for name, hex_data in self.CASES.items():
            with self.subTest(name):
                data = bytes.fromhex(hex_data)
                self.assertEqual(encode(decode(data)), data)

    def test_distinct_keys_keep_both_entries(self):
        self.assertEqual(decode(bytes.fromhex("a201f5f5f4")),
                         {1: True, CBORKey(True): False})
        self.assertEqual(decode(bytes.fromhex("a201f4fb3ff0000000000000f5")),
                         {1: False, CBORKey(1.0): True})
        self.assertEqual(decode(bytes.fromhex("a200f5f4f4")),
                         {0: True, CBORKey(False): False})
        self.assertEqual(len(decode(bytes.fromhex(self.CASES["0.0 and -0.0"]))), 2)

    def test_array_key_is_not_a_tag(self):
        # Before #88 the key decoded to (1, 2) and re-encoded as the tag 1(2)
        self.assertEqual(decode(bytes.fromhex("a1820102f6")), {CBORKey([1, 2]): None})

    def test_plain_keys_stay_plain(self):
        data = decode(bytes.fromhex("a4" "0100" "20" "01" "4101" "02" "616103"))
        self.assertEqual(data, {1: 0, -1: 1, b"\x01": 2, "a": 3})
        self.assertFalse(any(isinstance(k, CBORKey) for k in data))
        self.assertEqual(decode(bytes.fromhex("a1f600")), {None: 0})

    def test_canonical_encoding_sorts_wrapped_keys(self):
        data = {CBORKey(True): 0, 1: 1, CBORKey([1]): 2, CBORKey(1.5): 3}
        # 01 < 81 01 < f5 < f9 3e00
        self.assertEqual(encode(data, canonical=True).hex(), "a4" "0101" "810102" "f500" "f93e0003")


class TestDuplicates(unittest.TestCase):
    """Only keys that CBOR sees as equal are duplicates."""

    CASES = {
        "int twice": ("a201000100", 3),
        "true twice": ("a2f500f501", 3),
        "float, half and double width": ("a2f93e0000fb3ff800000000000000", 5),
        "same NaN twice": ("a2f97e0000f97e0001", 5),
        "array twice": ("a28101008101 01".replace(" ", ""), 4),
        "tag twice": ("a2c10200c10201", 4),
        "map, entries reordered": ("a2a2010203f600a2030401020 1".replace(" ", ""), None),
        "equal map, entries reordered": ("a2a2010203f600a203f6010201", 7),
    }

    def test_duplicates(self):
        for name, (hex_data, offset) in self.CASES.items():
            data = bytes.fromhex(hex_data)
            with self.subTest(name):
                if offset is None:
                    self.assertEqual(len(decode(data)), 2)  # not a duplicate
                    continue
                with self.assertRaises(CBORDecodeError) as ctx:
                    decode(data)
                self.assertIn("Duplicate map key", str(ctx.exception))
                self.assertEqual(ctx.exception.offset, offset)

    def test_true_and_one_inside_array_keys_differ(self):
        # {[1]: 0, [true]: 1}
        self.assertEqual(len(decode(bytes.fromhex("a2810100" "81f501"))), 2)


class TestCBORKey(unittest.TestCase):

    def test_equality_follows_cbor(self):
        self.assertEqual(CBORKey([1, 2]), CBORKey([1, 2]))
        self.assertEqual(CBORKey([1, 2, 3]), CBORKey((1, 2, 3)))  # a 3-tuple encodes as an array
        self.assertNotEqual(CBORKey([1, 2]), CBORKey((1, 2)))     # a (tag, value) pair is a tag
        self.assertEqual(CBORKey({1: 2, 3: 4}), CBORKey({3: 4, 1: 2}))
        self.assertEqual(hash(CBORKey({1: 2, 3: 4})), hash(CBORKey({3: 4, 1: 2})))
        self.assertNotEqual(CBORKey(True), CBORKey(1))
        self.assertNotEqual(CBORKey(1.0), CBORKey(1))
        self.assertNotEqual(CBORKey(0.0), CBORKey(-0.0))
        self.assertEqual(CBORKey(math.nan), CBORKey(math.nan))
        self.assertNotEqual(CBORKey([1]), CBORKey((1, [])))  # array vs tag 1([])

    def test_never_equal_to_plain_values(self):
        self.assertNotEqual(CBORKey(True), True)
        self.assertNotEqual(CBORKey(1), 1)
        self.assertEqual(len({1: "a", CBORKey(True): "b", CBORKey(1.0): "c"}), 3)

    def test_value_is_a_copy(self):
        key = CBORKey([1, {2: 3}])
        value = key.value
        value.append(4)
        self.assertEqual(key.value, [1, {2: 3}])
        self.assertEqual(key, CBORKey([1, {2: 3}]))

    def test_immutable(self):
        with self.assertRaises(AttributeError):
            CBORKey(True)._value = False

    def test_copy_and_pickle(self):
        key = CBORKey([1, CBORKey(True)])
        self.assertEqual(copy.deepcopy(key), key)
        self.assertEqual(pickle.loads(pickle.dumps(key)), key)
        self.assertEqual(CBORKey(key), key)

    def test_repr(self):
        self.assertEqual(repr(CBORKey([1, 2])), "CBORKey([1, 2])")

    def test_rejects_unencodable_values(self):
        with self.assertRaises(TypeError):
            CBORKey(object())

    def test_lookup(self):
        data = decode(bytes.fromhex("a201f5f5f4"))
        self.assertIs(data[1], True)
        self.assertIs(data[CBORKey(True)], False)


class TestJSON(unittest.TestCase):

    def test_typed_round_trip_keeps_key_types(self):
        for hex_data in TestRoundTrip.CASES.values():
            data = bytes.fromhex(hex_data)
            with self.subTest(hex_data):
                self.assertEqual(json_to_cbor(cbor_to_json(data, typed=True)), data)

    def test_typed_bool_key(self):
        self.assertEqual(json.loads(cbor_to_json(bytes.fromhex("a201f5f5f4"), typed=True)),
                         {"$cbor": "map", "$value": [[1, True], [True, False]]})

    def test_typed_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate map key"):
            json_to_cbor('{"$cbor": "map", "$value": [[[1], 0], [[1], 1]]}')
        with self.assertRaisesRegex(ValueError, "Duplicate map key"):
            json_to_cbor('{"$cbor": "map", "$value": [[{"$cbor": "NaN"}, 0], [{"$cbor": "NaN"}, 1]]}')
        # true and 1 are different keys
        self.assertEqual(json_to_cbor('{"$cbor": "map", "$value": [[1, 0], [true, 1]]}').hex(),
                         "a20100f501")

    def test_untyped_keys(self):
        self.assertEqual(json.loads(cbor_to_json(bytes.fromhex("a2f500f97e0001"))),
                         {"true": 0, "NaN": 1})
        self.assertEqual(json.loads(cbor_to_json(bytes.fromhex("a1820102f6"))), {"[1, 2]": None})
        with self.assertRaisesRegex(ValueError, "both become the JSON key"):
            cbor_to_json(bytes.fromhex("a2f500647472756501"))  # {true: 0, "true": 1}


class TestValidation(unittest.TestCase):

    def test_computed_keys_see_the_key_value(self):
        self.assertTrue(validate("r = {* bool => int}", bytes.fromhex("a1f501")).valid)
        self.assertTrue(validate("r = {* float => int}", bytes.fromhex("a1fb3ff800000000000001")).valid)
        self.assertTrue(validate("r = {* [int, int] => any}", bytes.fromhex("a1820102f6")).valid)
        self.assertFalse(validate("r = {* [int, int] => any}", bytes.fromhex("a18201f5f6")).valid)
        self.assertFalse(validate("r = {* int => int}", bytes.fromhex("a1f501")).valid)

    def test_bool_key_does_not_satisfy_int_key(self):
        result = validate("r = {1: int}", bytes.fromhex("a1f501"))
        self.assertFalse(result.valid)
        self.assertIn("Missing required field", " ".join(result.errors))

    def test_edn_writes_keys_as_cbor(self):
        edn = Validator("r = {* any => any}").to_edn(bytes.fromhex("a3820102f601f5f5f4"))
        self.assertIn("[ 1, 2 ]: null", edn)
        self.assertIn("1: true", edn)
        self.assertIn("true: false", edn)
        self.assertIn("h'01': 0", Validator("r = {* any => any}").to_edn(bytes.fromhex("a1410100")))


if __name__ == "__main__":
    unittest.main()
