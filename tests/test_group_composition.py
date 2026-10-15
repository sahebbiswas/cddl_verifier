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


class EmptyGroupSockets(unittest.TestCase):
    """A group socket nothing extends takes no entries; it must not hide the other groups (#80)."""

    # RFC 9393 writes `? payload-or-evidence, * $$coswid-extension, global-attributes`
    SCHEMA = ("r = { 0 => text, ? poe, * $$ext, g }\n"
              "poe //= ( 6 => tstr )\n"
              "poe //= ( 3 => int )\n"
              "g = ( ? 15 => text )")

    def test_optional_group_choice_beside_an_empty_socket(self):
        self.assertTrue(ok(self.SCHEMA, {0: "a"}))
        self.assertTrue(ok(self.SCHEMA, {0: "a", 6: "x"}))
        self.assertTrue(ok(self.SCHEMA, {0: "a", 3: 1, 15: "en"}))

    def test_what_the_other_groups_say_still_applies(self):
        self.assertFalse(ok(self.SCHEMA, {0: "a", 6: 1}))
        self.assertFalse(ok(self.SCHEMA, {0: "a", 6: "x", 3: 1}))
        self.assertFalse(ok(self.SCHEMA, {6: "x"}))

    def test_an_empty_socket_matches_no_extra_entry(self):
        self.assertFalse(ok(self.SCHEMA, {0: "a", 99: 1}))

    def test_the_socket_still_takes_its_extensions(self):
        schema = self.SCHEMA + "\n$$ext //= ( 99 => bool )"
        self.assertTrue(ok(schema, {0: "a", 6: "x", 99: True}))
        self.assertFalse(ok(schema, {0: "a", 6: "x", 99: 1}))


if __name__ == "__main__":
    unittest.main()
