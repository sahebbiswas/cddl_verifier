"""EDN literals: non-finite floats and text string escaping (#227, RFC 8949 section 8)."""

import json
import unittest

from cddl_verifier import Validator
from cddl_verifier.cbor import encode


def edn(schema: str, value, root: str = "r") -> str:
    return Validator(schema).to_edn(encode(value), root_type=root)


class TestNonFiniteFloats(unittest.TestCase):
    """RFC 8949 section 8 writes NaN, Infinity and -Infinity."""

    def test_map_members(self):
        text = edn("r = { a: float, b: float, c: float }",
                   {"a": float("nan"), "b": float("inf"), "c": float("-inf")})
        self.assertIn('"a": NaN', text)
        self.assertIn('"b": Infinity', text)
        self.assertIn('"c": -Infinity', text)
        for python_spelling in ("nan", "inf"):
            self.assertNotIn(python_spelling, text)

    def test_array_elements_and_every_width(self):
        # half, single and double precision encodings of the same three values: the width
        # beyond the shortest is an encoding indicator (RFC 8949 section 8.1, #237)
        for raw, suffix in (("83f97e00f97c00f9fc00", ""), ("83fa7fc00000fa7f800000faff800000", "_2"),
                            ("83fb7ff8000000000000fb7ff0000000000000fbfff0000000000000", "_3")):
            text = Validator("r = [* float]").to_edn(bytes.fromhex(raw), root_type="r")
            self.assertEqual([line.strip().rstrip(',') for line in text.splitlines()[1:-1]],
                             ["NaN" + suffix, "Infinity" + suffix, "-Infinity" + suffix], raw)

    def test_as_map_key(self):
        text = edn("r = { * float => int }", {float("inf"): 1})
        self.assertIn("Infinity: 1", text)

    def test_finite_floats_unchanged(self):
        text = edn("r = [* float]", [-0.0, 1.5, 1e300, 5e-324, 100000.0])
        values = [line.strip().rstrip(',') for line in text.splitlines()[1:-1]]
        self.assertEqual(values, ["-0.0", "1.5", "1e+300", "5e-324", "100000.0"])


class TestTextStringEscapes(unittest.TestCase):
    """The notation borrows JSON strings, so U+0000 to U+001F are escaped."""

    def literal(self, value: str) -> str:
        return edn("r = tstr", value)

    def test_issue_example(self):
        self.assertEqual(self.literal("a\x00\x08\x1fb"), '"a\\u0000\\b\\u001fb"')

    def test_short_forms(self):
        self.assertEqual(self.literal('\b\f\n\r\t"\\'), '"\\b\\f\\n\\r\\t\\"\\\\"')

    def test_every_c0_control_is_escaped_and_reads_back(self):
        value = ''.join(chr(i) for i in range(0x20))
        text = self.literal(value)
        self.assertFalse(any(ord(ch) < 0x20 for ch in text))
        self.assertEqual(json.loads(text), value)

    def test_terminal_and_line_controls(self):
        text = self.literal("\x1b[31m\x7f\x85\x9b  ")
        self.assertEqual(text, '"\\u001b[31m\\u007f\\u0085\\u009b\\u2028\\u2029"')
        self.assertEqual(json.loads(text), "\x1b[31m\x7f\x85\x9b  ")

    def test_printable_text_is_literal(self):
        for value in ("plain", "café   中文", "\U0001f600", "/ slash /"):
            self.assertEqual(self.literal(value), '"' + value + '"')

    def test_map_keys(self):
        text = edn("r = { * tstr => int }", {"a\x00\x1b": 1})
        self.assertIn('"a\\u0000\\u001b": 1', text)
        self.assertFalse(any(ord(ch) < 0x20 and ch != "\n" for ch in text))

    def test_text_inside_embedded_cbor(self):
        schema = "r = #6.24(bstr .cbor s)\ns = tstr"
        from cddl_verifier.cbor import CBORTag
        text = edn(schema, CBORTag(24, encode("x\x00")))
        self.assertIn('"x\\u0000"', text)


if __name__ == "__main__":
    unittest.main()
