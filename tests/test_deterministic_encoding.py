#!/usr/bin/env python3
"""
Byte-for-byte golden vectors for deterministic (canonical) CBOR encoding.

Vectors come from RFC 8949 Appendix A (every entry the encoder can express),
§4.2.1 (map key ordering example) and §4.2.2 (NaN/infinity), plus extra
float-width boundary cases. Each vector is ``(value, expected_hex)`` and is
checked with ``cbor_encode(value, canonical=True)``.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import math
import struct
import unittest

from cddl_verifier._cbor import cbor_decode, cbor_encode

# RFC 8949 Appendix A: integers
RFC8949_INTEGERS = [
    (0, "00"),
    (1, "01"),
    (10, "0a"),
    (23, "17"),
    (24, "1818"),
    (25, "1819"),
    (100, "1864"),
    (1000, "1903e8"),
    (1000000, "1a000f4240"),
    (1000000000000, "1b000000e8d4a51000"),
    (18446744073709551615, "1bffffffffffffffff"),
    (18446744073709551616, "c249010000000000000000"),
    (-18446744073709551616, "3bffffffffffffffff"),
    (-18446744073709551617, "c349010000000000000000"),
    (-1, "20"),
    (-10, "29"),
    (-100, "3863"),
    (-1000, "3903e7"),
]

# RFC 8949 Appendix A: floating point
RFC8949_FLOATS = [
    (0.0, "f90000"),
    (-0.0, "f98000"),
    (1.0, "f93c00"),
    (1.1, "fb3ff199999999999a"),
    (1.5, "f93e00"),
    (65504.0, "f97bff"),
    (100000.0, "fa47c35000"),
    (3.4028234663852886e+38, "fa7f7fffff"),
    (1.0e+300, "fb7e37e43c8800759c"),
    (5.960464477539063e-8, "f90001"),
    (0.00006103515625, "f90400"),
    (-4.0, "f9c400"),
    (-4.1, "fbc010666666666666"),
    (math.inf, "f97c00"),
    (math.nan, "f97e00"),
    (-math.inf, "f9fc00"),
]

# RFC 8949 Appendix A: simple values, tags, strings, arrays, maps
RFC8949_OTHER = [
    (False, "f4"),
    (True, "f5"),
    (None, "f6"),
    ((0, "2013-03-21T20:04:00Z"),
     "c074323031332d30332d32315432303a30343a30305a"),
    ((1, 1363896240), "c11a514b67b0"),
    ((1, 1363896240.5), "c1fb41d452d9ec200000"),
    ((23, b"\x01\x02\x03\x04"), "d74401020304"),
    ((24, b"dIETF"), "d818456449455446"),
    ((32, "http://www.example.com"),
     "d82076687474703a2f2f7777772e6578616d706c652e636f6d"),
    (b"", "40"),
    (b"\x01\x02\x03\x04", "4401020304"),
    ("", "60"),
    ("a", "6161"),
    ("IETF", "6449455446"),
    ("\"\\", "62225c"),
    ("ü", "62c3bc"),
    ("水", "63e6b0b4"),
    ("\U00010151", "64f0908591"),
    ([], "80"),
    ([1, 2, 3], "83010203"),
    ([1, [2, 3], [4, 5]], "8301820203820405"),
    (list(range(1, 26)),
     "98190102030405060708090a0b0c0d0e0f101112131415161718181819"),
    ({}, "a0"),
    ({1: 2, 3: 4}, "a201020304"),
    ({"a": 1, "b": [2, 3]}, "a26161016162820203"),
    (["a", {"b": "c"}], "826161a161626163"),
    ({"a": "A", "b": "B", "c": "C", "d": "D", "e": "E"},
     "a56161614161626142616361436164614461656145"),
]

# Width boundaries beyond Appendix A
FLOAT_BOUNDARIES = [
    (2.0 ** -24, "f90001"),                 # smallest float16 subnormal
    (3 * 2.0 ** -24, "f90003"),             # float16 subnormal
    (2.0 ** -14, "f90400"),                 # smallest float16 normal
    (65504.0, "f97bff"),                    # largest float16
    (65520.0, "fa477ff000"),                # would round to inf in float16
    (1.0 + 2.0 ** -10, "f93c01"),           # float16 mantissa limit
    (1.0 + 2.0 ** -11, "fa3f801000"),       # needs float32
    (1.0 + 2.0 ** -23, "fa3f800001"),       # float32 mantissa limit
    (1.0 + 2.0 ** -24, "fb3ff0000010000000"),  # needs float64
    (2.0 ** -149, "fa00000001"),            # smallest float32 subnormal
    (2.0 ** -150, "fb3690000000000000"),    # below float32 range
    (2.0 ** -1074, "fb0000000000000001"),   # smallest float64 subnormal
    (3.4028235677973366e+38, "fb47effffff0000000"),  # just above float32 max
    (-2.0 ** -24, "f98001"),
]


class TestRFC8949GoldenVectors(unittest.TestCase):
    """Canonical encoding matches RFC 8949 byte-for-byte."""

    def _check(self, vectors):
        for value, expected in vectors:
            with self.subTest(value=value):
                self.assertEqual(cbor_encode(value, canonical=True).hex(), expected)

    def test_integers(self):
        self._check(RFC8949_INTEGERS)

    def test_floats(self):
        self._check(RFC8949_FLOATS)

    def test_other_appendix_a(self):
        self._check(RFC8949_OTHER)

    def test_float_width_boundaries(self):
        self._check(FLOAT_BOUNDARIES)

    def test_float_vectors_decode_back(self):
        for value, expected in RFC8949_FLOATS + FLOAT_BOUNDARIES:
            with self.subTest(value=value):
                decoded = cbor_decode(bytes.fromhex(expected))
                if math.isnan(value):
                    self.assertTrue(math.isnan(decoded))
                else:
                    self.assertEqual(decoded, value)
                    self.assertEqual(math.copysign(1, decoded), math.copysign(1, value))


class TestDeterministicMapOrdering(unittest.TestCase):

    def test_rfc8949_section_4_2_1_example(self):
        # 10, 100, -1, "z", "aa", [100], [-1], false
        data = {False: 7, (-1,): 6, (100,): 5, "aa": 4, "z": 3, -1: 2, 100: 1, 10: 0}
        expected = ("a8" "0a00" "186401" "2002" "617a03" "62616104"
                    "81186405" "812006" "f407")
        self.assertEqual(cbor_encode(data, canonical=True).hex(), expected)

    def test_insertion_order_irrelevant(self):
        a = {"b": 1.5, 2: [3.0], -5: {"y": 1, "x": 2}}
        b = {-5: {"x": 2, "y": 1}, 2: [3.0], "b": 1.5}
        self.assertEqual(cbor_encode(a, canonical=True), cbor_encode(b, canonical=True))

    def test_float_keys_sort_by_shortest_encoding(self):
        # 1.5 (f93e00) sorts before 1.1 (fb...) once shortest forms are used
        encoded = cbor_encode({1.1: 0, 1.5: 1}, canonical=True)
        self.assertEqual(encoded.hex(), "a2f93e0001fb3ff199999999999a00")


class TestDeterministicEdgeCases(unittest.TestCase):

    def test_nan_payloads_canonicalised(self):
        for bits in ("7ff8000000000001", "fff8000000000000", "7ff0000000000001"):
            with self.subTest(bits=bits):
                nan = struct.unpack(">d", bytes.fromhex(bits))[0]
                self.assertEqual(cbor_encode(nan, canonical=True).hex(), "f97e00")

    def test_duplicate_encoded_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate map key"):
            cbor_encode({math.nan: 1, float("nan"): 2}, canonical=True)

    def test_nested_duplicate_encoded_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate map key"):
            cbor_encode([{"k": {math.nan: 1, float("nan"): 2}}], canonical=True)

    def test_failed_canonical_encode_resets_mode(self):
        from cddl_verifier._cbor import CBOR
        obj = CBOR({math.nan: 1, float("nan"): 2})
        with self.assertRaises(ValueError):
            obj.encode(canonical=True)
        obj.data = 1.5
        self.assertEqual(obj.encode().hex(), "fb3ff8000000000000")

    def test_non_canonical_floats_stay_float64(self):
        self.assertEqual(cbor_encode(1.5).hex(), "fb3ff8000000000000")
        self.assertEqual(cbor_encode(math.inf).hex(), "fb7ff0000000000000")

    def test_bignums_in_non_canonical_mode(self):
        self.assertEqual(cbor_encode(2 ** 64).hex(), "c249010000000000000000")
        self.assertEqual(cbor_encode(-(2 ** 64) - 1).hex(), "c349010000000000000000")

    def test_bignum_no_leading_zeros(self):
        self.assertEqual(cbor_encode(2 ** 72 - 1, canonical=True).hex(),
                         "c249ffffffffffffffffff")


if __name__ == "__main__":
    unittest.main()
