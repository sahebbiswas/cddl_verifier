#!/usr/bin/env python3
"""
Array elements are assigned to entries in order, honouring occurrences (#135).

An optional or repeated entry before others (``[? int, tstr]``), a group
name or inline group (``[int, pair]``, ``[+ (int, tstr)]``) and group
choices (``[int // tstr, bool]``) are matched by trying every assignment,
as RFC 8610 §3.4 describes. Arrays where element *i* always takes entry *i*
keep the positional check.
"""

import sys
import time
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier._analyzer import CDDLParser


class TestSequenceTable(unittest.TestCase):

    def sequence(self, schema):
        return CDDLParser(schema).types['r'].get('sequence')

    def test_positional_arrays_have_no_sequence(self):
        for schema in ('r = [int, tstr]', 'r = [int, * tstr]', 'r = [int, ? tstr]',
                       'r = [2*3 int]', 'r = [a: int, b: t]\nt = tstr'):
            self.assertIsNone(self.sequence(schema), schema)

    def test_sequences(self):
        self.assertEqual(self.sequence('r = [? int, tstr]'), [[
            {'type': 'int', 'min': 0, 'max': 1}, {'type': 'tstr', 'min': 1, 'max': 1}]])
        self.assertEqual(self.sequence('r = [int // tstr, bool]'), [
            [{'type': 'int', 'min': 1, 'max': 1}],
            [{'type': 'tstr', 'min': 1, 'max': 1}, {'type': 'bool', 'min': 1, 'max': 1}]])
        self.assertEqual(self.sequence('r = [* g]\ng = (int, tstr)'), [[
            {'group': [[{'type': 'int', 'min': 1, 'max': 1},
                        {'type': 'tstr', 'min': 1, 'max': 1}]], 'min': 0, 'max': None}]])
        self.assertEqual(self.sequence('r = [int, 2*3 tstr]'), [[
            {'type': 'int', 'min': 1, 'max': 1}, {'type': 'tstr', 'min': 2, 'max': 3}]])

    def test_self_referencing_group_is_not_expanded(self):
        self.assertIsNone(self.sequence('r = [? int, g]\ng = (int, ? g)'))
        self.assertTrue(validate('r = [g]\ng = (int, ? g)', [1, 2, 3]).valid)


class TestMatching(unittest.TestCase):

    def check(self, schema, accepted, rejected):
        for data in accepted:
            result = validate(schema, data)
            self.assertTrue(result.valid, (schema, data, result.errors))
        for data in rejected:
            self.assertFalse(validate(schema, data).valid, (schema, data))

    def test_optional_entry_before_others(self):
        self.check('r = [? int, tstr]', [['a'], [1, 'a']], [[1], ['a', 'b'], []])
        self.check('r = [int, ? tstr, bool]', [[1, True], [1, 'a', True]], [[1, 'a']])

    def test_repeated_entry_before_others(self):
        self.check('r = [* int, tstr]', [['a'], [1, 2, 'a']], [[1, 2, 3], ['a', 'b']])
        self.check('r = [+ int, tstr]', [[1, 'a']], [['a']])
        self.check('r = [* int, * tstr]', [[], [1, 'a', 'b'], ['a']], [['a', 1]])
        self.check('r = [int, 2*3 tstr]', [[1, 'a', 'b'], [1, 'a', 'b', 'c']],
                   [[1, 'a'], [1, 'a', 2], [1, 'a', 'b', 'c', 'd']])

    def test_group_names_and_inline_groups(self):
        pair = 'r = [int, pair]\npair = (a: int, b: tstr)'
        self.check(pair, [[1, 2, 'x']], [[1, 'x', 2], [1, 2]])
        self.check('r = [* (int, tstr)]', [[], [1, 'a', 2, 'b']], [[1, 'a', 2], ['a', 1]])
        self.check('r = [+ g]\ng = (int, tstr)', [[1, 'a', 2, 'b']], [[], [1, 'a', 'x']])
        self.check('r = [2*2 (int, ? tstr)]', [[1, 1], [1, 'a', 1], [1, 1, 'a']], [[1], [1, 'a']])
        self.check('r = [pair<int>, tstr]\npair<T> = (T, T)', [[1, 2, 'a']], [[1, 'a']])

    def test_group_choices(self):
        self.check('r = [int // tstr, bool]', [[1], ['x', True]], [[1, True], ['x'], []])
        # a length between the choices' lengths is not enough (review of #136)
        self.check('r = [int // int, int, int]', [[1], [1, 2, 3]], [[1, 2]])

    def test_nested_structures(self):
        self.check('r = [? {a: int}, tstr]', [['x'], [{'a': 1}, 'x']], [[{'a': 'z'}, 'x']])
        self.check('r = {o: [? int, tstr]}', [{'o': ['a']}, {'o': [1, 'a']}], [{'o': [1]}])
        self.check('r = [* [? int, tstr]]', [[['a'], [1, 'b']]], [[['a'], [1]]])


class TestMessages(unittest.TestCase):

    def error(self, schema, data):
        result = validate(schema, data)
        self.assertFalse(result.valid)
        return result.errors[0]

    def test_wrong_element(self):
        self.assertEqual(self.error('r = [int, pair]\npair = (a: int, b: tstr)', [1, 'x', 2]),
                         "Array element [1] of 'r' matches no entry (int: expected int, got str)")

    def test_missing_element(self):
        self.assertIn("is missing element [1]", self.error('r = [? int, tstr]', [1]))
        self.assertIn("is missing element [2]", self.error('r = [int // int, int, int]', [1, 2]))

    def test_unexpected_element(self):
        self.assertEqual(self.error('r = [int // tstr, bool]', [1, True]),
                         "Array type 'r' has unexpected element [1]")


class TestSize(unittest.TestCase):

    def test_linear_in_the_number_of_elements(self):
        # ambiguous entries ('* int, * int') are followed as sets of positions
        for schema, data in (('r = [* int, * int, tstr]', list(range(20000)) + ['a']),
                             ('r = [* (int // tstr), bool]', [1, 'a'] * 10000 + [True])):
            start = time.monotonic()
            self.assertTrue(validate(schema, data).valid)
            self.assertLess(time.monotonic() - start, 10, schema)


if __name__ == '__main__':
    unittest.main()
