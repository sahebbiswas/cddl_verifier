#!/usr/bin/env python3
"""
``.eq``, ``.ne``, ``.and`` and ``.within`` (RFC 8610 section 3.8, #181).

The controls apply to any target (primitive, choice, rule, tag, map, array),
in a field, an array element and at the root.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, validate


def ok(schema, data, root='r'):
    return validate(schema, data, root_type=root).valid


class TestEqNe(unittest.TestCase):

    def test_eq_literals(self):
        for schema, good, bad in [
            ('r = uint .eq 5', [5], [4, 6, '5']),
            ('r = tstr .eq "x"', ['x'], ['y', 'xx', 1]),
            ('r = bool .eq true', [True], [False]),
            ('r = any .eq null', [None], [0, 'a']),
        ]:
            for v in good:
                self.assertTrue(ok(schema, v), (schema, v))
            for v in bad:
                self.assertFalse(ok(schema, v), (schema, v))

    def test_ne_literals(self):
        self.assertTrue(ok('r = uint .ne 5', 4))
        self.assertFalse(ok('r = uint .ne 5', 5))
        self.assertFalse(ok('r = tstr .ne "x"', 'x'))
        self.assertTrue(ok('r = tstr .ne "x"', 'y'))

    def test_argument_is_a_rule_that_is_one_value(self):
        s = 'five = 5\nr = uint .eq five'
        self.assertTrue(ok(s, 5))
        self.assertFalse(ok(s, 6))

    def test_in_field_and_element(self):
        s = 'r = {a: tstr .eq "x", b: [* any .ne null]}'
        self.assertTrue(ok(s, {'a': 'x', 'b': [1, 'a']}))
        self.assertFalse(ok(s, {'a': 'y', 'b': []}))
        self.assertFalse(ok(s, {'a': 'x', 'b': [1, None]}))

    def test_target_still_checked(self):
        self.assertFalse(ok('r = uint .ne 5', 'a'))
        self.assertFalse(ok('r = uint .eq -1', -1))

    def test_argument_must_be_one_value(self):
        for arg in ('0..5', 'int', '{a: int}', '[* int]', '1 / 2'):
            with self.assertRaises(SchemaError, msg=arg):
                validate(f'r = int .eq ({arg})', 1, root_type='r')

    def test_argument_checked_after_generic_substitution(self):
        s = 'g<T> = uint .eq T\nr = g<(1 / 2)>'
        with self.assertRaises(SchemaError):
            validate(s, 1, root_type='r')
        good = 'g<T> = uint .eq T\nr = g<5>'
        self.assertTrue(ok(good, 5))
        self.assertFalse(ok(good, 6))


class TestAndWithin(unittest.TestCase):

    def test_primitive(self):
        for op in ('and', 'within'):
            s = f'r = tstr .{op} (tstr .size 2)'
            self.assertTrue(ok(s, 'ab'))
            self.assertFalse(ok(s, 'abc'))
            self.assertFalse(ok(s, 1))

    def test_bounded_spelling(self):
        s = 'r = (uint .ge 0) .le 150'
        self.assertTrue(ok(s, 150))
        self.assertFalse(ok(s, 151))

    def test_choice_target(self):
        s = 'r = (int / tstr) .and (1..3 / "a")'
        self.assertTrue(ok(s, 2))
        self.assertTrue(ok(s, 'a'))
        self.assertFalse(ok(s, 4))
        self.assertFalse(ok(s, 'b'))

    def test_alias_and_tag(self):
        s = 'small = uint .le 9\nr = small .and (uint .ge 3)'
        self.assertTrue(ok(s, 5))
        self.assertFalse(ok(s, 2))
        self.assertFalse(ok(s, 10))
        t = 'r = #6.1(uint) .and #6.1(0..10)'
        self.assertTrue(ok(t, b'\xc1\x05'))
        self.assertFalse(ok(t, b'\xc1\x0b'))

    def test_inline_map_operand(self):
        for op in ('and', 'within'):
            s = f'r = {{a: int}} .{op} {{a: 0..5}}'
            self.assertTrue(ok(s, {'a': 3}))
            self.assertFalse(ok(s, {'a': 9}))
            self.assertFalse(ok(s, {'a': 'x'}))

    def test_array_operand(self):
        s = 'r = [* int] .and [* 0..5]'
        self.assertTrue(ok(s, [1, 2]))
        self.assertFalse(ok(s, [1, 9]))

    def test_array_target_keeps_its_own_shape(self):
        s = 'r = {x: [+ uint] .and [* int]}'
        self.assertTrue(ok(s, {'x': [1]}))
        self.assertFalse(ok(s, {'x': []}))
        self.assertFalse(ok(s, {'x': [-1]}))

    def test_in_field_and_element(self):
        s = 'r = {a: tstr .and (tstr .size 2), b: [* uint .and (0..3)]}'
        self.assertTrue(ok(s, {'a': 'ab', 'b': [1, 3]}))
        self.assertFalse(ok(s, {'a': 'abc', 'b': []}))
        self.assertFalse(ok(s, {'a': 'ab', 'b': [4]}))

    def test_nested_with_parentheses(self):
        s = 'r = (uint .and (uint .ge 2)) .and (uint .le 4)'
        self.assertTrue(ok(s, 3))
        self.assertFalse(ok(s, 5))


if __name__ == '__main__':
    unittest.main()
