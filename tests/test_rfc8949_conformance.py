"""RFC 8949 decoding conformance vectors and rejection cases.

The encoding vectors live in test_deterministic_encoding.py. This companion
suite checks decoding independently, including accepted non-preferred encodings
and malformed input. Hex strings are intentionally kept next to their expected
semantic values so regressions are easy to review against RFC 8949 Appendix A.
"""
import math
import sys
import unittest
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier._cbor import (
    CBORDecodeError,
    CBORTruncatedError,
    CBORTrailingDataError,
    cbor_decode,
)


# (RFC concept, encoded bytes, expected Python value). Tags currently decode to
# (tag, value) tuples in this project; see the public API's documented limitation.
VALID_VECTORS = [
    ("unsigned integer 0", "00", 0),
    ("unsigned integer 23", "17", 23),
    ("unsigned integer 24", "1818", 24),
    ("unsigned integer 256", "190100", 256),
    ("negative integer -1", "20", -1),
    ("negative integer -1000", "3903e7", -1000),
    ("text string empty", "60", ""),
    ("text string UTF-8", "63e6b0b4", "水"),
    ("byte string empty", "40", b""),
    ("byte string", "4401020304", b"\x01\x02\x03\x04"),
    ("empty array", "80", []),
    ("nested array", "8301820203820405", [1, [2, 3], [4, 5]]),
    ("empty map", "a0", {}),
    ("map", "a201020304", {1: 2, 3: 4}),
    ("false", "f4", False),
    ("true", "f5", True),
    ("null", "f6", None),
    ("tag 1 epoch time", "c11a514b67b0", (1, 1363896240)),
    ("tag 32 URI", "d82076687474703a2f2f7777772e6578616d706c652e636f6d",
     (32, "http://www.example.com")),
    ("positive bignum", "c249010000000000000000", 2**64),
    ("negative bignum", "c349010000000000000000", -(2**64) - 1),
    # RFC 8949 permits non-preferred integer widths outside deterministic mode.
    ("non-preferred unsigned integer width", "1801", 1),
    ("non-preferred negative integer width", "3800", -1),
]


class TestRFC8949DecodeVectors(unittest.TestCase):
    def test_valid_vectors(self):
        for name, encoded_hex, expected in VALID_VECTORS:
            with self.subTest(vector=name):
                actual = cbor_decode(bytes.fromhex(encoded_hex))
                self.assertEqual(actual, expected)

    def test_float_vectors(self):
        cases = [
            ("f90000", 0.0),
            ("f98000", -0.0),
            ("f93e00", 1.5),
            ("fa47c35000", 100000.0),
            ("fb7e37e43c8800759c", 1.0e300),
            ("f97c00", math.inf),
            ("f9fc00", -math.inf),
            ("f97e00", math.nan),
        ]
        for encoded_hex, expected in cases:
            with self.subTest(encoded=encoded_hex):
                actual = cbor_decode(bytes.fromhex(encoded_hex))
                if math.isnan(expected):
                    self.assertTrue(math.isnan(actual))
                else:
                    self.assertEqual(actual, expected)
                    self.assertEqual(math.copysign(1, actual), math.copysign(1, expected))


class TestRFC8949RejectVectors(unittest.TestCase):
    # These are malformed or incomplete single-item inputs, not unsupported
    # optional features such as indefinite-length items.
    # (name, hex input, expected error class, byte offset)
    INVALID_VECTORS = [
        ("empty input", "", CBORTruncatedError, 0),
        ("truncated uint argument", "18", CBORTruncatedError, 1),
        ("truncated uint32 argument", "1a0000", CBORTruncatedError, 1),
        ("truncated byte string", "430102", CBORTruncatedError, 1),
        ("truncated text string", "636162", CBORTruncatedError, 1),
        ("truncated array child", "8201", CBORTruncatedError, 2),
        ("truncated map value", "a101", CBORTruncatedError, 2),
        ("truncated tag child", "c1", CBORTruncatedError, 1),
        ("invalid UTF-8 text", "61ff", CBORDecodeError, 1),
        ("reserved additional information", "1c", CBORDecodeError, 0),
        ("break outside indefinite item", "ff", CBORDecodeError, 0),
        ("trailing second data item", "0001", CBORTrailingDataError, 1),
    ]

    def test_invalid_vectors_rejected(self):
        for name, encoded_hex, error_type, expected_offset in self.INVALID_VECTORS:
            with self.subTest(vector=name):
                with self.assertRaises(error_type) as caught:
                    cbor_decode(bytes.fromhex(encoded_hex))
                self.assertEqual(caught.exception.offset, expected_offset)


if __name__ == "__main__":
    unittest.main()
