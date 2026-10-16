"""Preferred serialization of floats, and ``float_width`` to fix the width of one (#96)."""

import math
import random
import struct
import sys
import unittest
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import validate
from cddl_verifier.cbor import decode, encode, float_width, head_info


class DefaultIsShortest(unittest.TestCase):
    def test_widths(self):
        cases = [
            (1.5, "f93e00"), (0.0, "f90000"), (-0.0, "f98000"), (65504.0, "f97bff"),
            (5.960464477539063e-08, "f90001"),            # smallest half subnormal
            (65505.0, "fa477fe100"), (100000.0, "fa47c35000"), (3.4028234663852886e+38, "fa7f7fffff"),
            (1.1, "fb3ff199999999999a"), (1e300, "fb7e37e43c8800759c"), (5e-324, "fb0000000000000001"),
            (math.inf, "f97c00"), (-math.inf, "f9fc00"), (math.nan, "f97e00"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(encode(value).hex(), expected)

    def test_same_bytes_as_canonical_mode(self):
        rng = random.Random(96)
        values = [struct.unpack(">d", rng.randbytes(8))[0] for _ in range(2000)]
        values += [struct.unpack(">f", rng.randbytes(4))[0] for _ in range(2000)]
        values += [struct.unpack(">e", rng.randbytes(2))[0] for _ in range(2000)]
        for value in values:
            self.assertEqual(encode(value), encode(value, canonical=True), repr(value))

    def test_the_value_survives(self):
        rng = random.Random(97)
        for _ in range(2000):
            value = struct.unpack(">d", rng.randbytes(8))[0]
            back = decode(encode(value))
            if value != value:
                self.assertTrue(back != back)
            else:
                self.assertEqual(struct.pack(">d", back), struct.pack(">d", value))

    def test_inside_structures_and_the_maps_keep_their_order(self):
        data = {"b": [1.5, {"c": 100000.0}], "a": 1.1}
        self.assertEqual(encode(data).hex(),
                         "a2" "6162" "82f93e00a1" "6163" "fa47c35000" "6161" "fb3ff199999999999a")
        self.assertEqual(list(decode(encode(data))), ["b", "a"])

    def test_a_float_key(self):
        self.assertEqual(encode({1.5: 0}).hex(), "a1f93e0000")


class FloatWidth(unittest.TestCase):
    def test_each_width(self):
        self.assertEqual(encode(float_width(1.5, 16)).hex(), "f93e00")
        self.assertEqual(encode(float_width(1.5, 32)).hex(), "fa3fc00000")
        self.assertEqual(encode(float_width(1.5, 64)).hex(), "fb3ff8000000000000")

    def test_it_is_a_float_that_compares_as_the_value_does(self):
        wrapped = float_width(1.5, 64)
        self.assertIsInstance(wrapped, float)
        self.assertEqual(wrapped, 1.5)
        self.assertEqual(hash(wrapped), hash(1.5))
        self.assertEqual(repr(wrapped), "1.5")
        self.assertEqual({1.5: "x"}[wrapped], "x")

    def test_head_info_reads_the_width(self):
        self.assertEqual([head_info(float_width(1.5, bits)) for bits in (16, 32, 64)], [25, 26, 27])
        self.assertIsNone(head_info(1.5))

    def test_an_int_is_taken_as_a_float(self):
        self.assertEqual(encode(float_width(2, 64)).hex(), "fb4000000000000000")

    def test_special_values_in_every_width(self):
        for bits, nan, inf, zero in ((16, "f97e00", "f97c00", "f98000"),
                                     (32, "fa7fc00000", "fa7f800000", "fa80000000"),
                                     (64, "fb7ff8000000000000", "fb7ff0000000000000", "fb8000000000000000")):
            with self.subTest(bits=bits):
                self.assertEqual(encode(float_width(math.nan, bits)).hex(), nan)
                self.assertEqual(encode(float_width(math.inf, bits)).hex(), inf)
                self.assertEqual(encode(float_width(-0.0, bits)).hex(), zero)

    def test_a_value_the_width_cannot_hold_is_refused(self):
        for value, bits in ((0.1, 16), (0.1, 32), (65505.0, 16), (1e300, 32), (1e-10, 16)):
            with self.subTest(value=value, bits=bits):
                with self.assertRaisesRegex(ValueError, "does not fit"):
                    float_width(value, bits)
        float_width(0.1, 64)

    def test_a_bad_width_or_type(self):
        for bits in (8, 128, 0, True, "64", None):
            with self.subTest(bits=bits):
                with self.assertRaisesRegex(ValueError, "16, 32 or 64"):
                    float_width(1.5, bits)
        for value in (True, "1.5", None, b"x", [1.5]):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    float_width(value, 64)
        with self.assertRaisesRegex(ValueError, "too large"):
            float_width(10 ** 400, 64)

    def test_nested_and_as_a_key(self):
        data = {"x": [float_width(1.5, 64), 1.5]}
        self.assertEqual(encode(data).hex(), "a1" "6178" "82" "fb3ff8000000000000" "f93e00")
        self.assertEqual(encode({float_width(1.5, 32): 0}).hex(), "a1fa3fc0000000")

    def test_canonical_mode_ignores_it(self):
        self.assertEqual(encode(float_width(1.5, 64), canonical=True).hex(), "f93e00")

    def test_a_decoded_float_keeps_its_width_when_asked(self):
        for hex_data in ("fb3ff8000000000000", "fa3fc00000", "f93e00",
                         "82fb3ff8000000000000fa3fc00000", "a1fb3ff8000000000000f93e00"):
            raw = bytes.fromhex(hex_data)
            with self.subTest(hex_data):
                self.assertEqual(encode(decode(raw, keep_encoding=True)), raw)
        # without the record the width is not known, so the shortest is written
        self.assertEqual(encode(decode(bytes.fromhex("fb3ff8000000000000"))).hex(), "f93e00")
        self.assertEqual(encode(decode(bytes.fromhex("fb3ff8000000000000"), keep_encoding=True),
                                canonical=True).hex(), "f93e00")


class WithTheValidator(unittest.TestCase):
    """The width the validator sees in Python data is the width the bytes will have."""

    def test_a_fixed_width_schema(self):
        schema = "r = { x: float64 }"
        self.assertFalse(validate(schema, encode({"x": 1.5})).valid)   # written as a half float
        self.assertTrue(validate(schema, encode({"x": float_width(1.5, 64)})).valid)

    def test_python_data_matches_what_it_encodes_to(self):
        for bits, kind in ((16, "float16"), (32, "float32"), (64, "float64")):
            for other in ("float16", "float32", "float64"):
                data = {"x": float_width(1.5, bits)}
                with self.subTest(bits=bits, schema=other):
                    self.assertEqual(validate(f"r = {{ x: {other} }}", data).valid, kind == other)
                    self.assertEqual(validate(f"r = {{ x: {other} }}", encode(data)).valid, kind == other)

    def test_a_plain_float_still_fits_a_wider_or_the_shortest_width(self):
        self.assertTrue(validate("r = float", 1.5).valid)
        self.assertTrue(validate("r = float16", 1.5).valid)
        self.assertTrue(validate("r = float64", 1.5).valid)


if __name__ == "__main__":
    unittest.main()
