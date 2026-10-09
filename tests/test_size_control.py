#!/usr/bin/env python3
"""
RFC 8610 §3.8.1 ``.size`` control: tstr counts UTF-8 bytes, bstr counts bytes,
uint is bounded by the number of bytes needed to represent it.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier._analyzer import CDDLParser
from cddl_verifier.cbor import encode


class SizeTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, encode(data))
                self.assertEqual(result.valid, expected, result.errors)


class TestTstrUtf8Bytes(SizeTestCase):

    def test_multibyte_text(self):
        self.check([
            ("r = {a: tstr .size 3}", {"a": "abc"}, True),
            ("r = {a: tstr .size 3}", {"a": "üüü"}, False),   # 6 bytes
            ("r = {a: tstr .size 6}", {"a": "üüü"}, True),
            ("r = {a: tstr .size 2}", {"a": "ü"}, True),
            ("r = {a: tstr .size 1}", {"a": "ü"}, False),
            ("r = {a: tstr .size 3}", {"a": "水"}, True),
            ("r = {a: tstr .size 4}", {"a": "😀"}, True),
            ("r = {a: text .size (1..4)}", {"a": "😀"}, True),
            ("r = {a: tstr .size (1..3)}", {"a": "😀"}, False),
            ("r = {a: tstr .size 0}", {"a": ""}, True),
        ])

    def test_error_names_utf8_bytes(self):
        result = validate("r = {a: tstr .size 3}", encode({"a": "üüü"}))
        self.assertIn("expected exactly 3 UTF-8 bytes, got 6", result.errors[0])


class TestBstrBytes(SizeTestCase):

    def test_bstr_thresholds(self):
        self.check([
            ("r = {a: bstr .size 16}", {"a": b"\x00" * 16}, True),
            ("r = {a: bstr .size 16}", {"a": b"\x00" * 15}, False),
            ("r = {a: bytes .size (1..2)}", {"a": b""}, False),
            ("r = {a: bstr .size (1..2)}", {"a": b"ab"}, True),
            ("r = {a: bstr .size (1..2)}", {"a": b"abc"}, False),
        ])


class TestUintRepresentability(SizeTestCase):

    def test_uint_thresholds(self):
        self.check([
            ("r = {a: uint .size 0}", {"a": 0}, True),
            ("r = {a: uint .size 0}", {"a": 1}, False),
            ("r = {a: uint .size 1}", {"a": 255}, True),
            ("r = {a: uint .size 1}", {"a": 256}, False),
            ("r = {a: uint .size 3}", {"a": 16777215}, True),   # RFC 8610 example
            ("r = {a: uint .size 3}", {"a": 16777216}, False),
            ("r = {a: uint .size 8}", {"a": 2 ** 64 - 1}, True),
            ("r = {a: uint .size 0xffffffffff}", {"a": 2 ** 64 - 1}, True),  # no huge pow
        ])

    def test_uint_range_bounded_by_upper(self):
        self.check([
            ("r = {a: uint .size (1..2)}", {"a": 0}, True),
            ("r = {a: uint .size (1..2)}", {"a": 65535}, True),
            ("r = {a: uint .size (1..2)}", {"a": 65536}, False),
            ("r = {a: uint .size (1...2)}", {"a": 255}, True),
            ("r = {a: uint .size (1...2)}", {"a": 256}, False),
        ])

    def test_uint_type_still_checked(self):
        self.check([("r = {a: uint .size 1}", {"a": -1}, False)])


class TestSizeArguments(SizeTestCase):

    def test_exclusive_range(self):
        self.check([
            ("r = {a: tstr .size (1...3)}", {"a": "ab"}, True),
            ("r = {a: tstr .size (1...3)}", {"a": "abc"}, False),
        ])

    def test_hex_and_named_constants(self):
        self.check([
            ("r = {a: bstr .size 0x2}", {"a": b"ab"}, True),
            ("r = {a: tstr .size m}\nm = 2", {"a": "ab"}, True),
            ("r = {a: tstr .size m}\nm = 2", {"a": "abc"}, False),
            ("r = {a: tstr .size m}\nm = n\nn = 2", {"a": "ab"}, True),
            ("r = {a: tstr .size m}\nm = 1..2", {"a": "abc"}, False),
            ("r = {a: tstr .size (lo .. hi)}\nlo = 1\nhi = 2", {"a": "ab"}, True),
            ("r = {a: tstr .size (lo .. hi)}\nlo = 1\nhi = 2", {"a": "abc"}, False),
        ])

    def test_invalid_arguments_reported(self):
        for schema, fragment in [
            ("r = {a: tstr .size foo}", "Invalid .size argument 'foo'"),
            ("r = {a: tstr .size -1}", "Invalid .size argument '-1'"),
            ("r = {a: tstr .size (3..1)}", "empty range"),
            ("r = {a: tstr .size (1..x)}", "Invalid .size argument '(1..x)'"),
            # A name may contain dots (RFC 8610): 'lo..hi' is one name, not a range
            ("r = {a: tstr .size (lo..hi)}\nlo = 1\nhi = 2", "write 'lo .. hi' for a range"),
            ("r = {a: int .size 1}", ".size is not defined for 'int'"),
            ("r = {a: float .size 4}", ".size is not defined for 'float'"),
        ]:
            with self.subTest(schema=schema):
                data = {"a": "ab"} if "tstr" in schema else {"a": 1.5 if "float" in schema else 1}
                result = validate(schema, encode(data))
                self.assertFalse(result.valid)
                self.assertTrue(any(fragment in e for e in result.errors), result.errors)

    def test_extract_size_constraint(self):
        cddl = CDDLParser("lim = 4\nrg = 1..4\n")
        self.assertEqual(cddl.extract_size_constraint("tstr .size (1...4)"),
                         {"exact": None, "min": 1, "max": 3})
        self.assertEqual(cddl.extract_size_constraint("bstr .size 0x10"),
                         {"exact": 16, "min": None, "max": None})
        self.assertEqual(cddl.extract_size_constraint("tstr .size lim"),
                         {"exact": 4, "min": None, "max": None})
        self.assertEqual(cddl.extract_size_constraint("tstr .size rg"),
                         {"exact": None, "min": 1, "max": 4})
        self.assertIsNone(cddl.extract_size_constraint("tstr"))
        self.assertIn("error", cddl.extract_size_constraint("tstr .size nope"))


class TestSizeContexts(SizeTestCase):

    def test_aliases_and_optional_fields(self):
        self.check([
            ("r = {a: label}\nlabel = tstr .size 2", {"a": "ü"}, True),
            ("r = {a: label}\nlabel = tstr .size 2", {"a": "üü"}, False),
            ("r = {? a: tstr .size 2}", {}, True),
            ("r = {? a: tstr .size 2}", {"a": "abc"}, False),
            ("r = {\n  &(id: 0) => uint .size 1,\n}", {0: 256}, False),
        ])

    def test_arrays(self):
        self.check([
            ("r = [* tstr .size 2]", ["ü", "ab"], True),
            ("r = [* tstr .size 2]", ["ab", "üü"], False),
            ("r = [* uint .size 1]", [255, 256], False),
            ("r = [tstr .size 2, uint .size 1]", ["ab", 256], False),
        ])

    def test_nested(self):
        self.check([
            ("r = {a: s}\ns = {b: tstr .size 2}", {"a": {"b": "üü"}}, False),
            ("r = {a: s}\ns = {b: tstr .size 4}", {"a": {"b": "üü"}}, True),
            ("r = {a: [* tstr .size 2]}", {"a": ["ab"]}, True),
            ("r = {a: [* tstr .size 2]}", {"a": ["abc"]}, False),
            ("r = {\n  a: [+ bstr .size 1],\n}", {"a": [b"ab"]}, False),
            ("r = {a: [* l]}\nl = uint .size 1", {"a": [256]}, False),
        ])

    def test_text_and_bytes_aliases(self):
        self.check([
            ("r = [* text .size 2]", ["ab"], True),
            ("r = [* text .size 2]", ["abc"], False),
            ("r = {a: [* bytes .size 1]}", {"a": [b"a"]}, True),
            ("r = {a: [* bytes .size 1]}", {"a": [b"ab"]}, False),
            ("id = bytes .size 2", b"ab", True),
            ("id = bytes .size 2", b"abc", False),
            ("id = text .size 2", "\u00fc", True),
            ("id = text .size 2", "abc", False),
        ])

    def test_inline_array_elements_type_checked(self):
        self.check([
            ("r = {a: [* uint]}", {"a": [1, 2]}, True),
            ("r = {a: [* uint]}", {"a": ["x"]}, False),
        ])

    def test_root_rule(self):
        self.check([
            ("n = tstr .size 2", "ab", True),
            ("n = tstr .size 2", "ü", True),
            ("n = tstr .size 2", "abc", False),
            ("n = tstr .size 2", 12, False),
            ("n = uint .size 2", 65535, True),
            ("n = uint .size 2", 65536, False),
            ("a = n\nn = bstr .size (1..3)", b"", False),
        ])


if __name__ == "__main__":
    unittest.main()
