#!/usr/bin/env python3
"""
``.and`` / ``.within`` around generic structures, and the occurrence of
wildcard map members (#120).

``non-empty<M> = (M) .and ({ + any => any })`` is how CoRIM forbids an empty
map. The validator reads ``non-empty<{ ... }>`` as the map ``M`` and also
checks the other operand of ``.and`` / ``.within``. That check relies on
``+ K => V`` (and ``n*m K => V``) requiring at least ``n`` entries.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import validate
from cddl_verifier.cbor import CBORTag

ROOT = Path(__file__).resolve().parent.parent
NON_EMPTY = 'non-empty<M> = (M) .and ({ + any => any })\n'


def check(case, schema, root, accepted, rejected):
    for data in accepted:
        result = validate(schema, data, root_type=root)
        case.assertTrue(result.valid, (schema, data, result.errors))
    for data in rejected:
        result = validate(schema, data, root_type=root)
        case.assertFalse(result.valid, (schema, data))


class TestNonEmptyGeneric(unittest.TestCase):

    def test_root_rule_rejects_empty_map(self):
        check(self, NON_EMPTY + 'x = non-empty<{ ? a: int }>', 'x',
              [{'a': 1}], [{}, {'a': 'text'}])

    def test_field_type_rejects_empty_map(self):
        check(self, NON_EMPTY + 'r = { f: non-empty<{ ? a: int }> }', 'r',
              [{'f': {'a': 1}}], [{'f': {}}, {'f': {'a': 'text'}}])

    def test_array_element_rejects_empty_map(self):
        check(self, NON_EMPTY + 'r = [ + non-empty<{ ? a: int }> ]', 'r',
              [[{'a': 1}]], [[{}], [{'a': 1}, {}]])

    def test_error_names_the_rule_and_operand(self):
        result = validate(NON_EMPTY + 'x = non-empty<{ ? a: int }>', {}, root_type='x')
        self.assertFalse(result.valid)
        self.assertIn("Type 'x'", result.errors[0])
        self.assertIn('at least 1 entry', result.errors[0])

    def test_required_member_still_reported(self):
        check(self, NON_EMPTY + 'x = non-empty<{ a: int }>', 'x',
              [{'a': 1}], [{}, {'b': 1}])

    def test_within_operand_is_checked(self):
        schema = 'lim<M> = (M) .within ({ * tstr => any })\nx = lim<{ * any => any }>'
        check(self, schema, 'x', [{}, {'a': 1}], [{1: 2}, {'a': 1, 2: 3}])

    def test_array_operand_is_checked(self):
        # the operand is a uniform array: its '+' needs an element
        for control in ('and', 'within'):
            schema = f'mo<M> = (M) .{control} ([ + int ])\nx = mo<[ * int ]>'
            check(self, schema, 'x', [[1], [1, 2]], [[], ['text']])

    def test_corim_empty_triples_map_rejected(self):
        # CoRIM's triples-map is non-empty<{ ... }>
        schema = (ROOT / 'cddl-schemas' / 'unified.cddl')
        from cddl_verifier.cbor import encode
        comid = {1: {0: 'urn:example:comid:1'}, 4: {}}
        data = CBORTag(501, {0: 'urn:example:corim:1', 1: [CBORTag(506, encode(comid))]})
        result = validate(schema, encode(data), root_type='tagged-unsigned-corim-map')
        self.assertFalse(result.valid)
        self.assertIn('triples-map', ' '.join(result.errors))


class TestWildcardOccurrence(unittest.TestCase):

    def test_one_or_more(self):
        check(self, 'r = { + tstr => int }', 'r',
              [{'a': 1}, {'a': 1, 'b': 2}], [{}, {'a': 'x'}])

    def test_bounded_count(self):
        check(self, 'r = { 2*3 tstr => int }', 'r',
              [{'a': 1, 'b': 2}, {'a': 1, 'b': 2, 'c': 3}],
              [{}, {'a': 1}, {'a': 1, 'b': 2, 'c': 3, 'd': 4}])

    def test_fixed_member_does_not_count(self):
        check(self, 'r = { a: int, + tstr => int }', 'r',
              [{'a': 1, 'b': 2}], [{'a': 1}])

    def test_other_occurrences_unchanged(self):
        for occurrence in ('*', '?'):
            check(self, f'r = {{ {occurrence} tstr => int }}', 'r', [{}, {'a': 1}], [{'a': 'x'}])

    def test_no_occurrence_unchanged(self):
        check(self, 'r = { tstr => int }', 'r', [{'a': 1}], [{'a': 'x'}])


if __name__ == '__main__':
    unittest.main()
