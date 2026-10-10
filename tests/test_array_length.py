#!/usr/bin/env python3
"""
Arrays have as many elements as their entries' occurrences allow (#133).

``[int, tstr]`` takes exactly two elements; a missing or extra element is
an error naming its index. Inline arrays (other than ``[ + T ]`` and
``[ * T ]``) are structures of their own, so the same check applies to map
fields, array elements, choices and tags.
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


def length(schema, name='r'):
    return CDDLParser(schema).types[name].get('length')


class TestBounds(unittest.TestCase):

    def test_occurrences(self):
        self.assertEqual(length('r = [int, tstr]'), (2, 2))
        self.assertEqual(length('r = []'), (0, 0))
        self.assertEqual(length('r = [int, ? tstr]'), (1, 2))
        self.assertEqual(length('r = [int, * tstr]'), (1, None))
        self.assertEqual(length('r = [+ tstr]'), (1, None))
        self.assertEqual(length('r = [2*3 int, 0*0 tstr]'), (2, 3))
        self.assertEqual(length('r = [a: int, b: tstr]'), (2, 2))

    def test_names_and_groups(self):
        # a type name is one element, a group name its entries
        self.assertEqual(length('r = [int, t]\nt = tstr'), (2, 2))
        self.assertEqual(length('r = [int, g]\ng = (a: int, ? b: tstr)'), (2, 3))
        self.assertEqual(length('r = [int, 2*2 g]\ng = (a: int, b: tstr)'), (5, 5))
        self.assertEqual(length('r = [int, (tstr, bool)]'), (3, 3))
        self.assertEqual(length('r = [int // tstr, bool]'), (1, 2))
        self.assertEqual(length('r = [pair<int>]\npair<T> = (T, T)'), (2, 2))

    def test_chained_group_choices(self):
        # each group is worked out once, not once per choice that uses it
        schema = 'r = [g0]\n' + ''.join(
            f'g{i} = (g{i + 1} // g{i + 1}, int)\n' for i in range(40)) + 'g40 = (int)\n'
        self.assertEqual(length(schema), (1, 41))

    def test_unknown_bounds(self):
        # a group that refers to itself has no bounds worked out
        self.assertIsNone(length('r = [g]\ng = (int, ? g)'))
        self.assertTrue(validate('r = [g]\ng = (int, ? g)', [1, 2, 3]).valid)


class TestValidation(unittest.TestCase):

    def assert_rejects(self, schema, data, message):
        result = validate(schema, data)
        self.assertFalse(result.valid, data)
        self.assertTrue(any(message in e for e in result.errors), result.errors)

    def test_missing_and_extra_elements(self):
        schema = 'r = [int, tstr]'
        self.assertTrue(validate(schema, [1, 'a']).valid)
        self.assert_rejects(schema, [1], "is missing element [1]")
        self.assert_rejects(schema, [], "is missing element [0]")
        self.assert_rejects(schema, [1, 'a', 'extra'], "has unexpected element [2]")

    def test_optional_and_repeated_entries(self):
        self.assertTrue(validate('r = [int, ? tstr]', [1]).valid)
        self.assert_rejects('r = [int, ? tstr]', [1, 'a', 'b'], "unexpected element [2]")
        self.assertTrue(validate('r = [int, * tstr]', [1, 'a', 'b']).valid)
        self.assert_rejects('r = [2*3 int]', [1], "missing element [1]")
        self.assert_rejects('r = [2*3 int]', [1, 2, 3, 4], "unexpected element [3]")
        self.assert_rejects('r = []', [1], "unexpected element [0]")
        self.assert_rejects('r = [+ tstr]', [], "missing element [0]")

    def test_group_reference(self):
        schema = 'r = [int, g]\ng = (a: int, b: tstr)'
        self.assertTrue(validate(schema, [1, 2, 'x']).valid)
        self.assert_rejects(schema, [1, 2], "missing element [2]")

    def test_inline_arrays(self):
        self.assert_rejects('r = {o: [int, tstr]}', {'o': [1]}, "missing element [1]")
        self.assertTrue(validate('r = {o: [int, tstr]}', {'o': [1, 'a']}).valid)
        self.assertFalse(validate('r = {o: [int, tstr]}', {'o': [1, 2]}).valid)
        self.assertFalse(validate('r = [[int, tstr]]', [[1]]).valid)
        self.assertFalse(validate('r = [* [int, tstr]]', [[1, 'a'], [1]]).valid)
        self.assertFalse(validate('r = {o: nil / [int, tstr]}', {'o': [1]}).valid)
        self.assertTrue(validate('r = {o: nil / [int, tstr]}', {'o': None}).valid)
        # '[ * T ]' and '[ + T ]' are still checked as text
        self.assertTrue(validate('r = {o: [* int]}', {'o': []}).valid)
        self.assertFalse(validate('r = {o: [+ int]}', {'o': []}).valid)

    def test_repeated_group(self):
        # '[ + g ]' with a group of two entries takes two elements per repeat
        schema = 'r = {o: [+ g]}\ng = (a: int, b: tstr)'
        self.assert_rejects(schema, {'o': [1]}, "missing element [1]")
        self.assertTrue(validate(schema, {'o': [1, 'a']}).valid)
        # a type name repeated stays a uniform array
        self.assertTrue(validate('r = {o: [+ t]}\nt = tstr', {'o': ['a']}).valid)
        self.assertFalse(validate('r = {o: [+ t]}\nt = tstr', {'o': []}).valid)

    def test_group_choices(self):
        # an index does not name one entry across choices: only the number
        # of elements is checked, not each element against index i (#135)
        for schema in ('r = [int // tstr]', 'r = {o: [int // tstr]}'):
            wrap = (lambda v: v) if schema.startswith('r = [') else (lambda v: {'o': v})
            self.assertTrue(validate(schema, wrap(['x'])).valid, schema)
            self.assertTrue(validate(schema, wrap([1])).valid, schema)
            self.assertFalse(validate(schema, wrap([])).valid, schema)
            self.assertFalse(validate(schema, wrap([1, 2])).valid, schema)
        self.assertEqual(CDDLParser('r = [int // tstr]').types['r']['element_types'], {})

    def test_tagged_arrays(self):
        self.assertTrue(validate('r = decfrac', (4, [1, 2])).valid)
        self.assert_rejects('r = decfrac', (4, [1]), "missing element [1]")
        self.assert_rejects('r = bigfloat', (5, [1, 2, 3]), "unexpected element [2]")


if __name__ == '__main__':
    unittest.main()
