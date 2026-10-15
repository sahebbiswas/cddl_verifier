#!/usr/bin/env python3
"""Computed literals: ``.plus``, ``.cat`` and ``.det`` (RFC 9165 section 2) (#205)."""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, Validator, cbor
from cddl_verifier._cddl import computed


def ok(schema, value, root=None):
    return Validator(schema).validate(cbor.encode(value), root).valid


class Plus(unittest.TestCase):
    def test_integers(self):
        self.assertTrue(ok("r = 3 .plus 5", 8))
        self.assertFalse(ok("r = 3 .plus 5", 3))
        self.assertFalse(ok("r = 3 .plus 5", 5))
        self.assertTrue(ok("r = 3 .plus -5", -2))

    def test_floats(self):
        self.assertTrue(ok("r = 1.5 .plus 1.0", 2.5))
        self.assertFalse(ok("r = 1.5 .plus 1.0", 2))

    def test_integer_target_takes_the_floor(self):
        self.assertTrue(ok("r = 1 .plus 1.5", 2))

    def test_ranges_shift(self):
        schema = "r = (1..5) .plus 10"
        for value, expected in ((11, True), (15, True), (10, False), (16, False)):
            self.assertEqual(ok(schema, value), expected, value)
        self.assertFalse(ok("r = (1...5) .plus 10", 15))
        self.assertTrue(ok("r = (1...5) .plus 10", 14))

    def test_names_choices_and_nesting(self):
        schema = "base = 100\nr = base .plus (1 / 2)\nq = (1 .plus 2) .plus 3"
        self.assertTrue(ok(schema, 101, "r"))
        self.assertTrue(ok(schema, 102, "r"))
        self.assertFalse(ok(schema, 100, "r"))
        self.assertTrue(ok(schema, 6, "q"))

    def test_with_a_generic_parameter(self):
        schema = "r = shifted<10>\nshifted<N> = 1 .plus N"
        self.assertTrue(ok(schema, 11, "r"))
        self.assertFalse(ok(schema, 1, "r"))

    def test_map_keys(self):
        schema = "base = 100\nr = {(base .plus 1) => tstr}"
        self.assertTrue(ok(schema, {101: "x"}, "r"))
        self.assertFalse(ok(schema, {1: "x"}, "r"))

    def test_rfc_9165_interval_example(self):
        schema = ("interval<BASE> = (BASE => int, (BASE .plus 1) => int, ? (BASE .plus 2) => int)\n"
                  "X = 0\nY = 3\nrect = {interval<X>, interval<Y>}")
        self.assertTrue(ok(schema, {0: 1, 1: 2, 3: 4, 4: 5, 5: 6}, "rect"))
        self.assertTrue(ok(schema, {0: 1, 1: 2, 3: 4, 4: 5}, "rect"))
        self.assertFalse(ok(schema, {0: 1, 1: 2, 3: 4}, "rect"))        # no upper bound for Y
        self.assertFalse(ok(schema, {0: 1, 3: 4, 4: 5}, "rect"))        # no upper bound for X
        self.assertFalse(ok(schema, {0: "a", 1: 2, 3: 4, 4: 5}, "rect"))


class Cat(unittest.TestCase):
    def test_text(self):
        self.assertTrue(ok('r = "a" .cat "b"', "ab"))
        self.assertFalse(ok('r = "a" .cat "b"', "a"))

    def test_bytes(self):
        self.assertTrue(ok("r = 'a' .cat 'b'", b"ab"))
        self.assertFalse(ok("r = 'a' .cat 'b'", "ab"))

    def test_text_with_bytes_gives_the_type_of_the_target(self):
        self.assertTrue(ok("r = \"a\" .cat 'b'", "ab"))
        self.assertTrue(ok("r = 'a' .cat \"b\"", b"ab"))

    def test_choices_make_every_combination(self):
        schema = 'r = ("a" / "b") .cat ("c" / "d")'
        for value in ("ac", "ad", "bc", "bd"):
            self.assertTrue(ok(schema, value), value)
        self.assertFalse(ok(schema, "ab"))

    def test_chained_and_through_names(self):
        schema = 'p = "x"\nr = (p .cat "y") .cat p'
        self.assertTrue(ok(schema, "xyx", "r"))

    def test_as_a_map_key(self):
        schema = 'k = ("a" .cat "b")\nr = {k => int}'
        self.assertTrue(ok(schema, {"ab": 1}, "r"))
        self.assertFalse(ok(schema, {"a": 1}, "r"))

    def test_in_array_elements(self):
        schema = 'r = ["a" .cat "b", 1 .plus 1]'
        self.assertTrue(ok(schema, ["ab", 2]))
        self.assertFalse(ok(schema, ["a", 2]))


class Det(unittest.TestCase):
    def test_dedent_function(self):
        self.assertEqual(computed.dedent("  a\n  b"), "a\nb")
        self.assertEqual(computed.dedent("  a\n    b\n"), "a\n  b\n")
        self.assertEqual(computed.dedent("  a\n \n  b"), "a\n\nb")
        self.assertEqual(computed.dedent(b"  a\n  b"), b"a\nb")
        self.assertEqual(computed.dedent("a"), "a")

    def test_both_sides_are_dedented(self):
        self.assertTrue(ok('r = "  a\\n  b" .det "  c"', "a\nbc"))
        self.assertFalse(ok('r = "  a\\n  b" .det "  c"', "  a\n  b  c"))

    def test_bytes(self):
        self.assertTrue(ok("r = '  a' .det '  b'", b"ab"))


class SchemaErrors(unittest.TestCase):
    def test_operands_must_be_computable(self):
        for schema in ("r = uint .plus 1", "r = 1 .plus uint", "r = tstr .cat \"a\"", "r = \"a\" .cat tstr",
                       "r = any .det \"a\"", "r = 1 .plus"):
            with self.assertRaises(SchemaError, msg=schema):
                Validator(schema)

    def test_open_generic_parameter(self):
        with self.assertRaises(SchemaError):
            Validator("r = shifted<uint>\nshifted<N> = 1 .plus N")

    def test_wrong_kinds(self):
        for schema in ('r = "a" .plus 1', "r = 1 .cat 2", 'r = 1 .det "a"'):
            with self.assertRaises(SchemaError, msg=schema) as caught:
                Validator(schema)
            self.assertIn("cannot be computed", str(caught.exception))

    def test_invalid_utf8_result(self):
        with self.assertRaises(SchemaError):
            Validator("r = \"a\" .cat h'ff'")

    def test_too_many_results(self):
        choices = " / ".join(f'"{n}"' for n in range(70))
        with self.assertRaises(SchemaError):
            Validator(f'a = ({choices})\nr = a .cat a .cat a')

    def test_the_product_is_counted_before_it_is_built(self):
        import time
        choices = " / ".join(f'"{n}"' for n in range(64))
        start = time.monotonic()
        with self.assertRaises(SchemaError):
            Validator(f'x = ({choices})\ny = x .cat x\nr = y .cat y')   # 4096 x 4096
        self.assertLess(time.monotonic() - start, 5)

    def test_long_strings_are_refused(self):
        schema = 'x = "%s"\n' % ("a" * 100000) + "".join(
            f"{name} = {prev} .cat {prev}\n" for prev, name in zip("xyzwqu", "yzwqu" + "v")) + "r = v"
        with self.assertRaises(SchemaError) as caught:
            Validator(schema)
        self.assertIn("longer than", str(caught.exception))

    def test_runaway_recursion(self):
        with self.assertRaises(SchemaError):
            Validator('a = a .cat "x"\nr = a')


if __name__ == "__main__":
    unittest.main()
