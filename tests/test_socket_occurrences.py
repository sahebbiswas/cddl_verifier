#!/usr/bin/env python3
"""
Occurrences on socket entries in arrays count elements exactly (#223).

A type socket (``$name``) in an array is one element, whatever its ``/=``
alternatives, and a group socket (``$$name``) is one of its ``//=`` groups.
Both take the occurrence written on the entry (``[2* $s]``, ``[? $$g, int]``)
like any other entry. Until #223 the entry could not be expanded, so only
``*`` and ``+`` were counted, and arrays such as ``[? $s, tstr]`` were
rejected for good data.
"""

import itertools
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator, validate
from cddl_verifier._analyzer import CDDLParser

OCCURRENCES = ['', '* ', '+ ', '? ', '1* ', '2* ', '3* ', '*2 ', '1*2 ', '2*3 ', '2*2 ']

TYPE_SOCKET = '$s /= uint\n$s /= tstr\n'
GROUP_SOCKET = '$$g //= (uint, tstr)\n$$g //= (bool)\n'


def bounds(occurrence):
    """(min, max) of an occurrence written as in CDDL; ``max`` None for no limit."""
    text = occurrence.strip()
    if text == '':
        return 1, 1
    if text == '*':
        return 0, None
    if text == '+':
        return 1, None
    if text == '?':
        return 0, 1
    low, high = text.split('*')
    return int(low) if low else 0, int(high) if high else None


class TestTypeSocketOccurrences(unittest.TestCase):
    """``[<occurrence> $s]`` takes between its minimum and maximum elements."""

    def test_every_occurrence_at_every_length(self):
        for occurrence in OCCURRENCES:
            validator = Validator('x = [%s$s]\n%s' % (occurrence, TYPE_SOCKET))
            low, high = bounds(occurrence)
            for length in range(0, 6):
                data = [1, 'a', 2, 'b', 3][:length]
                expected = length >= low and (high is None or length <= high)
                with self.subTest(occurrence=occurrence, length=length):
                    self.assertEqual(validator.validate(data).valid, expected)

    def test_elements_are_still_checked(self):
        schema = 'x = [2* $s]\n' + TYPE_SOCKET
        self.assertTrue(validate(schema, [1, 'a']).valid)
        self.assertFalse(validate(schema, [1, True]).valid)
        self.assertFalse(validate(schema, [1.5, 'a']).valid)

    def test_minimum_not_met_names_the_missing_element(self):
        result = validate('x = [3* $s]\n$s /= uint', [1, 2])
        self.assertFalse(result.valid)
        self.assertIn('missing element [2]', result.diagnostics[0].message)

    def test_maximum_exceeded_names_the_extra_element(self):
        result = validate('x = [? $s]\n$s /= uint', [1, 2])
        self.assertFalse(result.valid)
        self.assertIn('unexpected element [1]', result.diagnostics[0].message)

    def test_structured_alternatives(self):
        schema = 'x = [2* $s]\n$s /= [int]\n$s /= {a: int}\n'
        self.assertTrue(validate(schema, [[1], {'a': 1}]).valid)
        self.assertFalse(validate(schema, [[1]]).valid)
        self.assertFalse(validate(schema, [[1], {'a': 'x'}]).valid)

    def test_nested_array(self):
        schema = 'x = [ [2* $s] ]\n$s /= uint'
        self.assertTrue(validate(schema, [[1, 2]]).valid)
        self.assertFalse(validate(schema, [[1]]).valid)

    def test_socket_extended_after_it_is_used(self):
        schema = 'x = [2* $s]\n$s /= uint\ny = $s\n$s /= tstr'
        self.assertTrue(validate(schema, [1, 'a']).valid)
        self.assertFalse(validate(schema, ['a']).valid)


class TestWithOtherEntries(unittest.TestCase):
    """The socket entry shares the array with other entries, so elements are assigned."""

    def check(self, schema, accepted, rejected):
        validator = Validator(schema)
        for data in accepted:
            with self.subTest(accepted=data):
                self.assertTrue(validator.validate(data).valid)
        for data in rejected:
            with self.subTest(rejected=data):
                self.assertFalse(validator.validate(data).valid)

    def test_optional_socket_before_another_entry(self):
        self.check('x = [? $s, tstr]\n$s /= uint',
                   [['a'], [1, 'a']], [[1], [], [1, 2, 'a'], ['a', 'b']])

    def test_repeated_socket_before_another_entry(self):
        self.check('x = [* $s, tstr]\n$s /= uint',
                   [['a'], [1, 'a'], [1, 2, 3, 'a']], [[1, 'a', 'b'], [1, 2]])
        self.check('x = [2* $s, tstr]\n$s /= uint',
                   [[1, 2, 'a'], [1, 2, 3, 'a']], [[1, 'a'], ['a']])

    def test_bounded_socket_between_entries(self):
        self.check('x = [tstr, 2*3 $s, bool]\n$s /= uint',
                   [['a', 1, 2, True], ['a', 1, 2, 3, True]],
                   [['a', 1, True], ['a', 1, 2, 3, 4, True], ['a', True]])

    def test_sockets_without_occurrences(self):
        self.check('x = [$s, $s]\n$s /= uint', [[1, 2]], [[1], [], [1, 2, 3]])
        self.check('x = [$s, tstr, $s]\n$s /= uint', [[1, 'a', 2]], [[1, 'a'], ['a']])

    def test_two_sockets(self):
        self.check('x = [? $a, 2* $b]\n$a /= bool\n$b /= uint',
                   [[1, 2], [True, 1, 2], [1, 2, 3]], [[True, 1], [True], []])

    def test_socket_in_a_group_entry(self):
        self.check('x = [2* (tstr, $s)]\n$s /= uint',
                   [['a', 1, 'b', 2]], [['a', 1], ['a', 1, 'b'], []])

    def test_has_a_sequence(self):
        """The entries are matched exactly, not by the length alone (#135)."""
        parser = CDDLParser('x = [? $s, tstr]\n$s /= uint')
        sequence = parser.types['x']['sequence']
        self.assertEqual([[entry['min'], entry['max']] for entry in sequence[0]],
                         [[0, 1], [1, 1]])


class TestGroupSocketOccurrences(unittest.TestCase):
    """``[<occurrence> $$g]`` takes that many of the groups the socket was extended with."""

    def test_repetitions_take_any_alternative(self):
        schema = 'x = [2* $$g]\n' + GROUP_SOCKET
        validator = Validator(schema)
        for data in ([1, 'a', True], [True, True], [1, 'a', 2, 'b'], [True, 1, 'a', False]):
            with self.subTest(accepted=data):
                self.assertTrue(validator.validate(data).valid)
        for data in ([], [1, 'a'], [True], [1, True], [1, 'a', 2], ['a', 1, True]):
            with self.subTest(rejected=data):
                self.assertFalse(validator.validate(data).valid)

    def test_every_occurrence_with_one_element_groups(self):
        for occurrence in OCCURRENCES:
            validator = Validator('x = [%s$$g]\n$$g //= (bool)\n$$g //= (uint)' % occurrence)
            low, high = bounds(occurrence)
            for length in range(0, 6):
                data = [True, 1, False, 2, True][:length]
                expected = length >= low and (high is None or length <= high)
                with self.subTest(occurrence=occurrence, length=length):
                    self.assertEqual(validator.validate(data).valid, expected)

    def test_optional_group_socket(self):
        validator = Validator('x = [? $$g]\n' + GROUP_SOCKET)
        self.assertTrue(validator.validate([]).valid)
        self.assertTrue(validator.validate([1, 'a']).valid)
        self.assertTrue(validator.validate([True]).valid)
        self.assertFalse(validator.validate([True, False]).valid)

    def test_between_other_entries(self):
        validator = Validator('x = [int, $$g, int]\n' + GROUP_SOCKET)
        self.assertTrue(validator.validate([0, 1, 'a', 9]).valid)
        self.assertTrue(validator.validate([0, True, 9]).valid)
        self.assertFalse(validator.validate([0, 9]).valid)
        self.assertFalse(validator.validate([0, 1, 9]).valid)

    def test_plug_with_optional_entries(self):
        validator = Validator('x = [2* $$g]\n$$g //= (? uint, tstr)')
        self.assertTrue(validator.validate(['a', 'b']).valid)
        self.assertTrue(validator.validate([1, 'a', 'b']).valid)
        self.assertFalse(validator.validate(['a']).valid)
        self.assertFalse(validator.validate([1, 'a']).valid)

    def test_socket_extended_in_two_places(self):
        schema = 'x = [2* $$g]\n$$g //= (uint)\ny = [$$g]\n$$g //= (tstr)'
        self.assertTrue(validate(schema, [1, 'a']).valid)
        self.assertFalse(validate(schema, [1]).valid)


class TestSocketAsGenericArgument(unittest.TestCase):
    """The RFC 9165 / CoRIM idiom ``one-or-more<T> = T / [ 2* T ]`` with a socket."""

    ONE_OR_MORE = 'one-or-more<T> = T / [ 2* T ]\n'

    def test_one_or_more(self):
        validator = Validator('x = one-or-more<$s>\n' + self.ONE_OR_MORE + '$s /= uint')
        self.assertTrue(validator.validate(5).valid)
        self.assertTrue(validator.validate([5, 6]).valid)
        self.assertTrue(validator.validate([5, 6, 7]).valid)
        self.assertFalse(validator.validate([5]).valid)
        self.assertFalse(validator.validate([]).valid)
        self.assertFalse(validator.validate(['a', 'b']).valid)
        self.assertFalse(validator.validate([5, 'a']).valid)

    def test_one_or_more_as_a_map_value(self):
        schema = ('x = { &(id: 0) ^ => one-or-more<$s> }\n' + self.ONE_OR_MORE
                  + '$s /= uint\n$s /= tstr\n$s /= #6.37(bstr .size 16)')
        validator = Validator(schema)
        self.assertTrue(validator.validate({0: 1000}).valid)
        self.assertTrue(validator.validate({0: [1000, 'abc']}).valid)
        self.assertFalse(validator.validate({0: [1000]}).valid)
        self.assertFalse(validator.validate({0: []}).valid)

    def test_pair(self):
        validator = Validator('x = pair<$s, $s>\npair<A, B> = [A, B]\n$s /= uint')
        self.assertTrue(validator.validate([1, 2]).valid)
        self.assertFalse(validator.validate([1]).valid)
        self.assertFalse(validator.validate([1, 2, 3]).valid)


class TestEmptySockets(unittest.TestCase):
    """A socket nothing extends has no alternatives."""

    def test_empty_type_socket(self):
        self.assertTrue(validate('x = [int, * $e]', [1]).valid)
        self.assertFalse(validate('x = [int, * $e]', [1, 2]).valid)
        self.assertFalse(validate('x = [int, 2* $e]', [1]).valid)
        self.assertFalse(validate('x = [int, $e]', [1]).valid)

    def test_optional_empty_group_socket_takes_no_entries(self):
        self.assertTrue(validate('x = [int, ? $$e]', [1]).valid)
        self.assertTrue(validate('x = [int, * $$e]', [1]).valid)
        self.assertFalse(validate('x = [int, ? $$e]', [1, 5]).valid)

    def test_required_empty_group_socket_is_left_as_in_a_map(self):
        self.assertTrue(validate('x = [int, $$e]', [1]).valid)
        self.assertTrue(validate('x = { a: int, $$e }', {'a': 1}).valid)


class TestSameAsARule(unittest.TestCase):
    """A shape written with a socket gives the verdict of the same shape with a rule.

    Every array of up to four elements from a small alphabet is checked
    against each shape, so no assignment of elements to entries can differ.
    """

    ALPHABET = [1, 'a', True]
    LAYOUTS = ['{e}', 'int, {e}', '{e}, int', '? int, {e}, bool', '{e}, {e}']

    def arrays(self):
        for length in range(0, 5):
            yield from (list(items) for items in itertools.product(self.ALPHABET, repeat=length))

    def compare(self, as_socket, as_rule, socket_name, rule_name):
        arrays = list(self.arrays())
        for layout in self.LAYOUTS:
            for occurrence in OCCURRENCES:
                socket_shape = '[%s]' % layout.format(e=occurrence + socket_name)
                rule_shape = '[%s]' % layout.format(e=occurrence + rule_name)
                socket = Validator('x = %s\n%s' % (socket_shape, as_socket))
                rule = Validator('x = %s\n%s' % (rule_shape, as_rule))
                differ = [data for data in arrays
                          if socket.validate(data).valid != rule.validate(data).valid]
                with self.subTest(shape=socket_shape):
                    self.assertEqual(differ, [])

    def test_type_socket(self):
        self.compare('$s /= uint\n$s /= tstr', 't = uint / tstr', '$s', 't')

    def test_group_socket(self):
        self.compare('$$g //= (uint, tstr)\n$$g //= (bool)', 'g = (uint, tstr // bool)',
                     '$$g', 'g')


if __name__ == '__main__':
    unittest.main()
