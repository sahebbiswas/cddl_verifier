#!/usr/bin/env python3
"""Repeated group choices in maps (#201) and members written ``=>`` that do not cut (#202).

RFC 8610 3.5.1: each repetition of a group takes any of its alternatives, so a map
``{+ g}`` may mix entries of several. 3.5.4: ``K => T`` does not cut, so an entry whose
value is not a ``T`` is left for a later ``* K2 => V`` member; ``K: T`` and ``K ^=> T`` do cut.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate

SOCKET = "r = {+ $$p}\n$$p //= (1 => bstr)\n$$p //= (2 => uint)\n"
INLINE = "r = {+ ((1 => bstr) // (2 => uint))}\n"
NAMED = "r = {* g}\ng = ((1 => bstr) // (2 => uint))\n"


def ok(schema, data):
    return validate(schema, data).valid


def errors(schema, data):
    return [str(e) for e in validate(schema, data).errors]


class RepeatedChoices(unittest.TestCase):
    def test_entries_of_several_alternatives(self):
        for schema in (SOCKET, INLINE):
            with self.subTest(schema=schema):
                self.assertTrue(ok(schema, {1: b"a", 2: 3}))
                self.assertTrue(ok(schema, {1: b"a"}))
                self.assertTrue(ok(schema, {2: 3}))

    def test_star_allows_none(self):
        self.assertTrue(ok(NAMED, {}))
        self.assertTrue(ok(NAMED, {1: b"a", 2: 3}))

    def test_plus_needs_one(self):
        for schema in (SOCKET, INLINE):
            with self.subTest(schema=schema):
                self.assertFalse(ok(schema, {}))

    def test_unknown_key_and_wrong_value(self):
        self.assertFalse(ok(SOCKET, {1: b"a", 3: 1}))
        self.assertFalse(ok(SOCKET, {1: 5}))
        self.assertFalse(ok(SOCKET, {2: b"a"}))

    def test_alternative_with_several_members_needs_them_all(self):
        s = "r = {+ $$p}\n$$p //= (1 => bstr, 3 => int)\n$$p //= (2 => uint)\n"
        self.assertTrue(ok(s, {1: b"a", 3: 1, 2: 2}))
        self.assertTrue(ok(s, {2: 2}))
        self.assertFalse(ok(s, {1: b"a"}))
        self.assertIn("Missing required field '3' (key 3) in type 'r'", errors(s, {1: b"a"}))

    def test_optional_member_of_an_alternative(self):
        s = "r = {+ $$p}\n$$p //= (1 => bstr, ? 3 => int)\n$$p //= (2 => uint)\n"
        self.assertTrue(ok(s, {1: b"a"}))
        self.assertTrue(ok(s, {1: b"a", 3: 1}))
        self.assertFalse(ok(s, {3: 1}))

    def test_bounds_count_the_alternatives_present(self):
        s = "r = {2*2 g}\ng = ((1 => bstr) // (2 => uint) // (3 => int))\n"
        self.assertFalse(ok(s, {1: b"a"}))
        self.assertTrue(ok(s, {1: b"a", 3: 3}))
        self.assertFalse(ok(s, {1: b"a", 2: 1, 3: 3}))
        self.assertIn("needs exactly 2 of the alternatives of a repeated group choice, found 3",
                      errors(s, {1: b"a", 2: 1, 3: 3})[0])

    def test_a_fixed_key_cannot_repeat(self):
        s = "r = {2*3 g}\ng = ((1 => bstr) // (2 => uint))\n"
        self.assertTrue(ok(s, {1: b"a", 2: 1}))
        self.assertFalse(ok(s, {1: b"a"}))  # one key, one repetition

    def test_same_key_with_different_types(self):
        s = "r = {+ g}\ng = ((1 => bstr) // (1 => uint) // (2 => tstr))\n"
        self.assertTrue(ok(s, {1: b"a"}))
        self.assertTrue(ok(s, {1: 5, 2: "x"}))
        self.assertFalse(ok(s, {1: "x"}))

    def test_more_alternatives_than_the_matcher_enumerates(self):
        alternatives = "\n".join(f"$$p //= ({i} => uint)" for i in range(1, 15))
        s = "r = {+ $$p}\n" + alternatives + "\n"
        self.assertTrue(ok(s, {i: i for i in range(1, 15)}))
        self.assertFalse(ok(s, {99: 1}))
        self.assertFalse(ok(s, {}))

    def test_alternative_with_a_wildcard_repeats(self):
        s = "r = {+ ((1 => int) // (* tstr => int))}\n"
        self.assertTrue(ok(s, {1: 1, "a": 2, "b": 3}))
        self.assertTrue(ok(s, {"a": 2}))
        self.assertFalse(ok(s, {"a": "x"}))

    def test_other_members_are_unchanged(self):
        s = SOCKET.replace("r = {+ $$p}", "r = {0: tstr, + $$p}")
        self.assertTrue(ok(s, {0: "t", 1: b"a", 2: 3}))
        self.assertFalse(ok(s, {1: b"a"}))

    def test_group_choice_in_array_still_repeats(self):
        s = "r = [+ ((1 // 2))]\n"
        self.assertTrue(ok(s, [1, 2, 1]))

    def test_choice_not_repeated_still_picks_one(self):
        s = "r = {((1 => bstr) // (2 => uint))}\n"
        self.assertTrue(ok(s, {1: b"a"}))
        self.assertFalse(ok(s, {1: b"a", 2: 3}))
        s = "r = {? ((1 => bstr) // (2 => uint))}\n"
        self.assertFalse(ok(s, {1: b"a", 2: 3}))

    def test_suit_shape(self):
        s = ("r = {+ $$param}\n"
             "$$param //= (1 => bstr)\n$$param //= (2 => uint)\n$$param //= (3 => [* int])\n")
        self.assertTrue(ok(s, {2: 7, 3: [1, 2], 1: b"x"}))


class NonCuttingKeys(unittest.TestCase):
    def test_wildcard_takes_the_entry(self):
        self.assertTrue(ok('r = { ? "a" => int, * tstr => any }', {"a": "x"}))
        self.assertTrue(ok("r = { ? 8 => bool, * int => int }", {8: 1}))

    def test_member_takes_a_matching_entry(self):
        self.assertTrue(ok('r = { ? "a" => int, * tstr => any }', {"a": 1}))
        self.assertTrue(ok('r = { ? "a" => int, * tstr => any }', {"a": 1, "b": 2}))

    def test_wildcard_must_accept_the_value_too(self):
        self.assertFalse(ok('r = { ? "a" => int, * tstr => int }', {"a": "x"}))
        self.assertIn("Type mismatch for field 'a' in 'r'", errors('r = { ? "a" => int, * tstr => int }', {"a": "x"}))

    def test_wildcard_must_accept_the_key(self):
        self.assertFalse(ok('r = { ? "a" => int, * int => any }', {"a": "x"}))

    def test_colon_cuts(self):
        self.assertFalse(ok('r = { ? "a": int, * tstr => any }', {"a": "x"}))
        self.assertFalse(ok('r = { ? a: int, * tstr => any }', {"a": "x"}))

    def test_caret_cuts(self):
        self.assertFalse(ok('r = { ? "a" ^ => int, * tstr => any }', {"a": "x"}))

    def test_required_member_still_needs_its_entry(self):
        # the entry the wildcard took does not satisfy the member
        self.assertFalse(ok('r = { "a" => int, * tstr => any }', {"a": "x"}))
        self.assertTrue(ok('r = { "a" => int, * tstr => any }', {"a": 1}))

    def test_no_wildcard_no_change(self):
        self.assertFalse(ok('r = { ? "a" => int }', {"a": "x"}))

    def test_rule_name_key_agrees(self):
        s = "r = { ? corpus => bool, * label => int }\ncorpus = 8\nlabel = int / tstr\n"
        self.assertTrue(ok(s, {8: 1}))
        self.assertTrue(ok(s.replace("corpus => bool", "8 => bool"), {8: 1}))

    def test_wildcard_count_applies_to_the_entry(self):
        s = 'r = { ? "a" => int, ? tstr => any }'
        self.assertTrue(ok(s, {"a": "x"}))
        self.assertFalse(ok(s, {"a": "x", "b": 1}))  # at most one for the wildcard


if __name__ == "__main__":
    unittest.main()
