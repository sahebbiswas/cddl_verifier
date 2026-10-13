#!/usr/bin/env python3
"""Group composition in maps and arrays, and wildcard occurrence bounds (#72, #151, #158)."""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate


def ok(schema, data):
    return validate(schema, data).valid


class MapGroups(unittest.TestCase):
    def test_group_reference(self):
        s = "r = {id, b: int}\nid = (a: int)"
        self.assertTrue(ok(s, {"a": 1, "b": 2}))
        self.assertFalse(ok(s, {"b": 2}))

    def test_inline_and_optional_group(self):
        s = "r = {a: int, ? (b: int, c: int)}"
        self.assertTrue(ok(s, {"a": 1}))
        self.assertTrue(ok(s, {"a": 1, "b": 2, "c": 3}))
        self.assertFalse(ok(s, {"a": 1, "b": 2}))

    def test_group_choice(self):
        s = "r = {(a: int // b: tstr)}"
        self.assertTrue(ok(s, {"a": 1}))
        self.assertTrue(ok(s, {"b": "x"}))
        self.assertFalse(ok(s, {"a": 1, "b": "x"}))
        self.assertFalse(ok(s, {}))

    def test_independent_choices_multiply(self):
        s = "r = {(a: int // b: int), (c: int // d: int)}"
        self.assertTrue(ok(s, {"a": 1, "d": 2}))
        self.assertFalse(ok(s, {"a": 1, "b": 2}))

    def test_socket(self):
        s = "r = {$$ext}\n$$ext //= (a: int)\n$$ext //= (b: tstr)"
        self.assertTrue(ok(s, {"a": 1}))
        self.assertTrue(ok(s, {"b": "x"}))
        self.assertFalse(ok(s, {"a": "x"}))

    def test_recursive_group(self):
        s = "r = {g}\ng = (a: int, ? (n: {g}))"
        self.assertTrue(ok(s, {"a": 1, "n": {"a": 2}}))
        self.assertFalse(ok(s, {"a": 1, "n": {"a": "x"}}))

    def test_generic_group(self):
        s = "r = {pair<int>}\npair<T> = (x: T, y: T)"
        self.assertTrue(ok(s, {"x": 1, "y": 2}))
        self.assertFalse(ok(s, {"x": 1, "y": "z"}))

    def test_unwrap_splice(self):
        s = "r = {~hdr, c: int}\nhdr = {a: int}"
        self.assertTrue(ok(s, {"a": 1, "c": 2}))
        self.assertFalse(ok(s, {"c": 2}))

    def test_unwrap_array_into_map_rejected(self):
        with self.assertRaises(Exception) as cm:
            validate("r = {~hdr}\nhdr = [int]", {})
        self.assertIn("cannot be spliced into a map", str(cm.exception))

    def test_too_many_alternatives_fails_closed(self):
        s = "r = {" + ", ".join(f"(a{i}: int // b{i}: int)" for i in range(12)) + "}"
        res = validate(s, {f"a{i}": 1 for i in range(12)})
        self.assertFalse(res.valid)
        self.assertIn("alternatives", " ".join(res.errors))


class ArraySplice(unittest.TestCase):
    def test_unwrap_in_array(self):
        s = "r = [~hdr, tstr]\nhdr = [int, int]"
        self.assertTrue(ok(s, [1, 2, "x"]))
        self.assertFalse(ok(s, [1, "x"]))

    def test_repeated_unwrap(self):
        s = "r = [* ~hdr]\nhdr = [int, int]"
        self.assertTrue(ok(s, [1, 2, 3, 4]))
        self.assertFalse(ok(s, [1, 2, 3]))

    def test_tag_unwrap_as_type(self):
        s = "r = {a: ~t}\nt = #6.7(int)"
        self.assertTrue(ok(s, {"a": 5}))
        self.assertFalse(ok(s, {"a": "x"}))


class WildcardBounds(unittest.TestCase):
    def test_default_is_exactly_one(self):
        s = "r = {tstr => int}"
        self.assertTrue(ok(s, {"a": 1}))
        self.assertFalse(ok(s, {}))
        self.assertFalse(ok(s, {"a": 1, "b": 2}))

    def test_star_and_plus_and_bounds(self):
        self.assertTrue(ok("r = {* tstr => int}", {}))
        self.assertFalse(ok("r = {+ tstr => int}", {}))
        self.assertTrue(ok("r = {1*2 tstr => int}", {"a": 1, "b": 2}))
        self.assertFalse(ok("r = {1*2 tstr => int}", {"a": 1, "b": 2, "c": 3}))

    def test_plus_group_scales(self):
        s = "r = {+ (tstr => int)}"
        self.assertTrue(ok(s, {"a": 1, "b": 2}))
        self.assertFalse(ok(s, {}))

    def test_prefers_member_with_room(self):
        s = "r = {? tstr => int, ? tstr => tstr}"
        self.assertTrue(ok(s, {"a": 1, "b": "x"}))


if __name__ == "__main__":
    unittest.main()
