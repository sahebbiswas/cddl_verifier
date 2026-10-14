#!/usr/bin/env python3
"""
Embedded CBOR is checked against the whole ``.cbor`` argument (#127).

Generic instances (``pair<uint, tstr>``, ``opt<uint>``), inline maps and
inline arrays used as the argument of ``.cbor`` are expanded like any other
type, so the embedded item is checked against them instead of being accepted.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier.cbor import encode
from cddl_verifier.cbor import CBORTag

PAIR = "pair<A, B> = [A, B]\n"
OPT = "opt<T> = T / nil\n"


class TestFieldArgument(unittest.TestCase):
    """``o: bstr .cbor X`` in a map."""

    CASES = [
        # (schema, good embedded value, bad embedded value)
        ("r = { o: bstr .cbor pair<uint, tstr> }\n" + PAIR, [1, "a"], [1, 2]),
        ("r = { o: bstr .cbor opt<uint> }\n" + OPT, None, "x"),
        ("r = { o: bstr .cbor { a: uint } }", {"a": 1}, {"a": "x"}),
        ("r = { o: bstr .cbor [uint, tstr] }", [1, "a"], [1, 2]),
        ("r = { o: bstr .cbor [+ pair<uint, tstr>] }\n" + PAIR, [[1, "a"]], [[1, 2]]),
        ("r = { o: bstr .cbor #6.7(pair<uint, tstr>) }\n" + PAIR, CBORTag(7, [1, "a"]), CBORTag(7, [1, 2])),
        ("r = { o: bstr .cbor [+ p] }\np = [uint, tstr]", [[1, "a"]], [[1, 2]]),
        ("r = { o: bstr .cbor [+ p] }\np = [uint, tstr]", [[1, "a"]], []),
        ("r = { o: bstr .cbor [* uint] }", [1, 2], [1, "x"]),
    ]

    def test_cases(self):
        for schema, good, bad in self.CASES:
            with self.subTest(schema=schema, data="good"):
                result = validate(schema, {"o": encode(good)})
                self.assertTrue(result.valid, result.errors)
            with self.subTest(schema=schema, data="bad"):
                self.assertFalse(validate(schema, {"o": encode(bad)}).valid)

    def test_errors_are_reported_once(self):
        result = validate("r = { o: bstr .cbor m }\nm = { a: uint }", {"o": encode({"a": "x"})})
        self.assertEqual(len(result.errors), 1, result.errors)
        result = validate("r = { o: bstr .cbor [uint, tstr] }", {"o": encode([1, 2])})
        self.assertEqual(len(result.errors), 1, result.errors)
        self.assertIn("expected tstr", result.errors[0])

    def test_named_argument_still_checked(self):
        schema = "r = { o: bstr .cbor m }\nm = [uint, tstr]"
        self.assertTrue(validate(schema, {"o": encode([1, "a"])}).valid)
        self.assertFalse(validate(schema, {"o": encode([1, 2])}).valid)

    def test_bytes_still_required(self):
        result = validate("r = { o: bstr .cbor pair<uint, tstr> }\n" + PAIR, {"o": [1, "a"]})
        self.assertFalse(result.valid)


class TestOtherPositions(unittest.TestCase):

    def test_rule(self):
        schema = "r = bstr .cbor pair<uint, tstr>\n" + PAIR
        self.assertTrue(validate(schema, encode(encode([1, "a"]))).valid)
        self.assertFalse(validate(schema, encode(encode([1, 2]))).valid)

    def test_array_element(self):
        schema = "r = [* bstr .cbor pair<uint, tstr>]\n" + PAIR
        self.assertTrue(validate(schema, [encode([1, "a"])]).valid)
        self.assertFalse(validate(schema, [encode([1, "a"]), encode([1, 2])]).valid)

    def test_choice_alternative(self):
        schema = "r = { o: bstr .cbor pair<uint, tstr> / tstr }\n" + PAIR
        self.assertTrue(validate(schema, {"o": "text"}).valid)
        self.assertTrue(validate(schema, {"o": encode([1, "a"])}).valid)
        self.assertFalse(validate(schema, {"o": encode([1, 2])}).valid)

    def test_size_on_the_bytes_still_applies(self):
        schema = "r = { o: (bstr .cbor opt<uint>) .size 1 }\n" + OPT
        self.assertTrue(validate(schema, {"o": encode(None)}).valid)
        self.assertFalse(validate(schema, {"o": encode(1000)}).valid)


if __name__ == "__main__":
    unittest.main()
