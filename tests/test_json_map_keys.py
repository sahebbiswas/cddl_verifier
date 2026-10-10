#!/usr/bin/env python3
"""
CBOR map keys in JSON conversion (#91).

Typed JSON keeps non-string map keys as ``{"$cbor": "map", "$value":
[[key, value], ...]}``, so CBOR → JSON → CBOR gives the same data. Untyped
conversion turns keys into strings and raises when two keys would collide,
and ``json_to_cbor`` rejects a JSON object with a repeated key, so no value
is ever dropped silently.
"""

import json
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier.cbor import decode, encode
from cddl_verifier.json_codec import cbor_to_json, json_to_cbor

TEST_DATA = Path(__file__).resolve().parent.parent / "test-data"


def typed_round_trip(data):
    return decode(json_to_cbor(cbor_to_json(encode(data), typed=True)))


class TestTypedKeys(unittest.TestCase):

    def test_key_types_round_trip(self):
        for data in ({0: "a", 1: "b"}, {-1: "n"}, {b"\x01\x02": 1}, {1.5: "f"},
                     {True: "t"}, {False: "f"}, {None: "null"},
                     {0: "int", "0": "str"}, {"a": {2: "nested"}}, [{3: 4}],
                     {"$cbor": "not an annotation"}):
            self.assertEqual(typed_round_trip(data), data, data)

    def test_pairs_form(self):
        self.assertEqual(json.loads(cbor_to_json(encode({0: "a", "0": "b"}), typed=True)),
                         {"$cbor": "map", "$value": [[0, "a"], ["0", "b"]]})
        self.assertEqual(json.loads(cbor_to_json(encode({b"\x01": 1}), typed=True)),
                         {"$cbor": "map", "$value": [[{"$cbor": "bytes", "$value": "AQ=="}, 1]]})

    def test_string_keys_stay_objects(self):
        self.assertEqual(cbor_to_json(encode({"a": 1, "b": [1]}), typed=True),
                         '{"a": 1, "b": [1]}')
        # documents written before #91 read the same
        self.assertEqual(decode(json_to_cbor('{"a": {"$cbor": "bytes", "$value": "AQ=="}}')),
                         {"a": b"\x01"})

    def test_bundled_cbor_samples(self):
        for path in sorted(TEST_DATA.glob("*.cbor")):
            raw = path.read_bytes()
            self.assertEqual(decode(json_to_cbor(cbor_to_json(raw, typed=True))), decode(raw),
                             path.name)

    def test_malformed_pairs(self):
        for bad in ('{"$cbor": "map", "$value": [[1, 2, 3]]}',
                    '{"$cbor": "map", "$value": [1]}',
                    '{"$cbor": "map", "$value": [[1, "a"], [1, "b"]]}',
                    # review of #140: a non-list $value, and NaN keys (NaN != NaN)
                    '{"$cbor": "map", "$value": {}}',
                    '{"$cbor": "map"}',
                    '{"$cbor": "map", "$value": [[{"$cbor": "NaN"}, 1], [{"$cbor": "NaN"}, 2]]}'):
            with self.assertRaises(ValueError, msg=bad):
                json_to_cbor(bad)


class TestUntypedKeys(unittest.TestCase):

    def test_keys_become_strings(self):
        self.assertEqual(cbor_to_json(encode({0: "a", True: "t", None: "n"})),
                         '{"0": "a", "true": "t", "null": "n"}')
        self.assertEqual(cbor_to_json(encode({b"\x01": 1})), '{"AQ==": 1}')

    def test_colliding_keys_raise(self):
        for data in ({0: "int", "0": "str"}, {True: 1, "true": 2}, {None: 1, "null": 2}):
            with self.assertRaises(ValueError, msg=data) as caught:
                cbor_to_json(encode(data))
            self.assertIn("typed=True", str(caught.exception))


class TestDuplicateJSONKeys(unittest.TestCase):

    def test_repeated_object_key_raises(self):
        with self.assertRaises(ValueError):
            json_to_cbor('{"a": 1, "a": 2}')
        with self.assertRaises(ValueError):
            json_to_cbor('[{"x": {"b": 1, "b": 1}}]')


if __name__ == '__main__':
    unittest.main()
