#!/usr/bin/env python3
"""
Numeric ranges as types (#71).

``0..10`` (inclusive) and ``0...10`` (upper bound excluded) are checked at the
root, in map fields, array elements, choices, sockets, computed map keys and
generic arguments. An integer range matches CBOR integers only and a float
range floats only. The comparison controls ``.ge``/``.gt``/``.le``/``.lt``
work the same way on floats as on integers.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, validate


class Checks(unittest.TestCase):

    def check(self, schema, accepted, rejected):
        for data in accepted:
            result = validate(schema, data)
            self.assertTrue(result.valid, (schema, data, result.errors))
        for data in rejected:
            self.assertFalse(validate(schema, data).valid, (schema, data))


class TestIntegerRanges(Checks):

    def test_bounds(self):
        self.check('r = 0..10', [0, 5, 10], [-1, 11])
        self.check('r = 0...10', [0, 9], [10])
        self.check('r = -5..5', [-5, 0, 5], [-6, 6])
        self.check('r = 0x10..0x20', [16, 32], [15, 33])

    def test_only_integers(self):
        # not a float, a bool or a bignum (outside major types 0 and 1)
        self.check('r = 0..10', [3], [3.0, True, False, "3", None])
        self.check('r = 0..18446744073709551616', [18446744073709551615],
                   [18446744073709551616])
        self.check('r = -18446744073709551617..0', [-18446744073709551616],
                   [-18446744073709551617])

    def test_named_bounds(self):
        self.check('r = low .. high\nlow = 1\nhigh = 3', [1, 3], [0, 4])
        self.check('r = low ... high\nlow = -1\nhigh = 1', [-1, 0], [1])


class TestFloatRanges(Checks):

    def test_bounds(self):
        self.check('r = 1.0..2.5', [1.0, 2.5], [0.5, 3.0])
        self.check('r = 0.0...1.0', [0.0, 0.5], [1.0])
        self.check('r = -1.5..-0.5', [-1.0], [0.0])

    def test_only_floats(self):
        self.check('r = 0.0..1.0', [1.0], [1, True])
        self.assertFalse(validate('r = 0.0..1.0', float('nan')).valid)


class TestWhereRangesAppear(Checks):

    def test_map_fields(self):
        self.check('r = {a: 0..10}', [{'a': 5}], [{'a': 20}, {'a': 'x'}])
        self.check('r = {? a: (1..5)}', [{}, {'a': 1}], [{'a': 6}])

    def test_array_elements(self):
        self.check('r = [* 1..3]', [[], [1, 3]], [[1, 5]])
        self.check('r = [0..10, tstr]', [[1, 'x']], [[20, 'x']])
        self.check('r = [? 0..9, tstr]', [['x'], [1, 'x']], [[12, 'x']])
        self.check('r = {a: [+ 0.0..1.0]}', [{'a': [0.5]}], [{'a': [1.5]}])

    def test_choices_and_sockets(self):
        self.check('r = 0..10 / tstr', [5, 'x'], [20])
        self.check('r = {a: $s}\n$s /= 0..10\n$s /= tstr', [{'a': 5}, {'a': 'x'}], [{'a': 20}])

    def test_computed_keys_and_generics(self):
        self.check('r = {* 0..9 => tstr}', [{}, {5: 'x'}], [{20: 'x'}])
        self.check('r = pair<0..3>\npair<T> = [T, T]', [[1, 2]], [[1, 9]])

    def test_controls_on_a_parenthesized_range(self):
        self.check('r = (0..100) .le 50', [0, 50], [51, 101])
        self.check('r = (0.0..10.0) .gt 1.0', [2.0], [1.0])


class TestMessages(unittest.TestCase):

    def test_errors_name_the_range(self):
        self.assertIn("11 is outside 0..10", validate('r = 0..10', 11).errors[0])
        self.assertIn("expected an integer in 0..10, got 5.0", validate('r = 0..10', 5.0).errors[0])
        self.assertIn("expected a float in 0.0..1.0, got 1", validate('r = 0.0..1.0', 1).errors[0])
        self.assertIn("Field 'a' in 'r' 20 is outside 0..10",
                      validate('r = {a: 0..10}', {'a': 20}).errors[0])


class TestMalformedRanges(unittest.TestCase):

    def test_schema_errors(self):
        # RFC 8610 ranges have both bounds, of the same kind, and are not empty
        for schema in ('r = 1..', 'r = ..10', 'r = 1..2.5', 'r = 5..1', 'r = 1...1',
                       'r = "a".."z"', 'r = 1..x\nx = tstr'):
            with self.assertRaises(SchemaError, msg=schema):
                validate(schema, 1)


class TestUnresolvedBounds(Checks):

    def test_known_bound_still_sets_the_kind(self):
        # review of #141: a socket bound with several values cannot be compared
        # with, but the other bound still says the value is an integer
        self.check('r = $low .. 10\n$low /= 1\n$low /= 2', [5], ['x', 5.0])

    def test_known_bound_is_enforced(self):
        # review of #141: the bound that is known still limits the value
        self.check('r = $low .. 10\n$low /= 1\n$low /= 2', [-3, 10], [11, 99])
        self.check('r = $low ... 10\n$low /= 1\n$low /= 2', [9], [10])
        self.check('r = 1 .. $high\n$high /= 5\n$high /= 6', [1, 99], [0])
        self.check('r = 0.5 .. $high\n$high /= 1.0\n$high /= 2.0', [0.5], [0.4])


class TestIntegerComparisonControls(Checks):

    def test_array_elements(self):
        # review of #141: these were checked in map fields but not array elements
        self.check('r = [* uint .ge 5]', [[5, 9]], [[0]])
        self.check('r = [uint .ge 5, tstr]', [[5, 'x']], [[0, 'x']])
        self.check('r = {a: [* int .le 3]}', [{'a': [3]}], [{'a': [9]}])

    def test_map_fields(self):
        self.check('r = {a: uint .ge 0}', [{'a': 0}], [{'a': -1}, {'a': True}])
        self.assertIn("0 violates .ge 5", validate('r = {a: uint .ge 5}', {'a': 0}).errors[0])


class TestFloatComparisonControls(Checks):

    def test_fields_and_elements(self):
        self.check('r = {a: float .le 1.0}', [{'a': 0.5}, {'a': 1.0}], [{'a': 2.0}, {'a': 1}])
        self.check('r = [* float .gt 0.0]', [[1.0]], [[-1.0], [0.0]])
        self.check('r = {a: [* float32 .ge 1.5]}', [{'a': [2.0]}], [{'a': [1.0]}])
        self.check('r = float64 .lt 2.0', [1.0], [2.0])


if __name__ == '__main__':
    unittest.main()
