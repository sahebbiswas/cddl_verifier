#!/usr/bin/env python3
"""
CDDLParser tables built from the AST (#110, Phase B of #69).

The validator and EDN generator read ``types``, ``type_aliases``,
``type_choices``, ``socket_extensions``, ``groups``, ``registered_params``
and ``first_definition``. Since #110 these come from ``_cddl/legacy.py``.
Also covers the two parser gaps this fixed: inline maps (#100) and
``key => type`` members (#104).
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator, validate
from cddl_verifier._analyzer import CDDLParser

SCHEMAS = Path(__file__).resolve().parent.parent / "cddl-schemas"


def field(name, type_, optional=False, registered=False):
    info = {'name': name, 'type': type_, 'optional': optional}
    if registered:
        info['registered'] = True
    return info


class TestMaps(unittest.TestCase):

    def test_key_forms(self):
        p = CDDLParser('m = {\n  a: tstr,\n  ? 1: uint ; one\n  "s": int,\n'
                       '  ? &(reg: 5) => bstr,\n  7 => bool\n}')
        self.assertEqual(p.types['m'], {'type': 'map', 'fields': {
            'a': field('a', 'tstr'),
            1: field('one', 'uint', optional=True),
            's': field('s', 'int'),
            5: field('reg', 'bstr', optional=True, registered=True),
            7: field('7', 'bool'),
        }})
        self.assertEqual(p.registered_params, {5: 'reg'})

    def test_multi_line_members_and_comments(self):
        p = CDDLParser("m = {\n  &(name: 0)\n    => tstr,\n  1:\n    uint ; count\n}")
        self.assertEqual(p.types['m']['fields'], {
            0: field('name', 'tstr', registered=True),
            1: field('count', 'uint'),
        })

    def test_optional_inline_group_members_are_optional(self):
        p = CDDLParser("m = { a: int, ? ( b: int, c: int ) }")
        fields = p.types['m']['fields']
        self.assertFalse(fields['a']['optional'])
        self.assertTrue(fields['b']['optional'])
        self.assertTrue(fields['c']['optional'])

    def test_controls_and_choices_are_canonical_text(self):
        p = CDDLParser("m = { a: tstr .size (1..64), b: uint/tstr, c: #6.1(x) }")
        types = [f['type'] for f in p.types['m']['fields'].values()]
        self.assertEqual(types, ['tstr .size (1..64)', 'uint / tstr', '#6.1(x)'])

    def test_generic_wrapper_reads_the_map_inside(self):
        p = CDDLParser("m = non-empty<{ ? a: int }>\nnon-empty<M> = (M) .and ({ + any => any })")
        self.assertEqual(p.types['m']['fields'], {'a': field('a', 'int', optional=True)})
        self.assertEqual(p.type_aliases['non-empty'], '(M) .and ({ + any => any })')


class TestArrays(unittest.TestCase):

    def test_repeated_element(self):
        p = CDDLParser("a = [ + tstr ]")
        self.assertEqual(p.types['a'], {'type': 'array', 'fields': {},
                                        'element_types': {0: 'tstr'}, 'occurrence': '+'})

    def test_repeating_entry_after_positional_ones(self):
        p = CDDLParser("r = [ int, * tstr ]")
        self.assertEqual(p.types['r']['repeat'], 1)
        self.assertTrue(validate("r = [ int, * tstr ]", [1]).valid)
        self.assertTrue(validate("r = [ int, * tstr ]", [1, "a", "b"]).valid)
        result = validate("r = [ int, * tstr ]", [1, "a", 2])
        self.assertIn("expected tstr", result.errors[0])
        self.assertNotIn('repeat', CDDLParser("r = [ int, tstr ]").types['r'])
        self.assertNotIn('repeat', CDDLParser("r = [ int, 1*2 tstr ]").types['r'])

    def test_positional_elements(self):
        p = CDDLParser("a = [ min: int / null, max: int, extra ]")
        self.assertEqual(p.types['a']['element_types'],
                         {0: 'int / null', 1: 'int', 2: 'extra'})
        self.assertEqual(sorted(p.types['a']['fields']), ['max', 'min'])
        self.assertEqual(p.types['a']['occurrence'], '')


class TestRulesAndTables(unittest.TestCase):

    def test_aliases(self):
        p = CDDLParser("a = b\nt = #6.501(m)\nc = uint / tstr\ns = bstr .size 16")
        self.assertEqual(p.type_aliases['a'], 'b')
        self.assertEqual(p.type_aliases['t'], '#6.501(m)')
        self.assertEqual(p.type_aliases['c'], 'uint / tstr')
        self.assertEqual(p.type_aliases['s'], 'bstr .size 16')

    def test_choices_sockets_and_groups(self):
        p = CDDLParser("$c /= a\n$c /= b / c\n$$s //= ( x: int )\n"
                       "g = ( a: int, ? b: tstr )\nh = ( name )")
        self.assertEqual(p.type_choices['$c'], ['a', 'b', 'c'])
        self.assertEqual(p.socket_extensions['$$s'], ['(x: int)'])
        self.assertEqual(p.groups['g'], ['a: int', '? b: tstr'])
        self.assertEqual(p.groups['h'], ['name'])

    def test_first_definition_skips_choices_and_groups(self):
        self.assertEqual(CDDLParser("$c /= a\ng = (a: int)\nroot = { a: int }").first_definition,
                         'root')

    def test_empty_schema_keeps_builtins(self):
        p = CDDLParser("")
        self.assertEqual(p.types, {})
        self.assertEqual(p.type_aliases['text'], 'tstr')
        self.assertIsNone(p.first_definition)


class TestInlineMaps(unittest.TestCase):
    """#100: inline map field types are validated."""

    def test_issue_examples(self):
        self.assertFalse(validate("r = {a: {b: uint}}", {"a": {"b": "x"}}).valid)
        self.assertTrue(validate("r = {a: {b: uint}}", {"a": {"b": 1}}).valid)
        result = validate("r = {a: {b: tstr .size 2}}", {"a": {"b": "abc"}})
        self.assertIn("violates .size", result.errors[0])

    def test_multi_line_and_nested(self):
        schema = "r = {\n  a: {\n    b: {\n      c: int\n    }\n  }\n}"
        self.assertTrue(validate(schema, {"a": {"b": {"c": 1}}}).valid)
        self.assertFalse(validate(schema, {"a": {"b": {"c": "x"}}}).valid)
        self.assertFalse(validate(schema, {"a": {"b": {}}}).valid)

    def test_in_arrays_choices_and_tags(self):
        self.assertFalse(validate("r = [ + { d: tstr } ]", [{"d": 1}]).valid)
        self.assertTrue(validate("r = [ + { d: tstr } ]", [{"d": "x"}]).valid)
        self.assertFalse(validate("r = { a: { b: int } / tstr }", {"a": {"b": "x"}}).valid)
        self.assertTrue(validate("r = { a: { b: int } / tstr }", {"a": "x"}).valid)
        self.assertTrue(validate("r = { t: #6.9({ x: int }) }", {"t": (9, {"x": 1})}).valid)
        self.assertFalse(validate("r = { t: #6.9({ x: int }) }", {"t": (9, {"x": "y"})}).valid)
        self.assertFalse(validate("r = { t: #6.9({ x: int }) }", {"t": (8, {"x": 1})}).valid)

    def test_tagged_structure_rule(self):
        schema = "m = #6.563([ value: bytes, mask: bytes ])"
        self.assertTrue(validate(schema, (563, [b"a", b"b"])).valid)
        self.assertFalse(validate(schema, (563, [b"a", 1])).valid)
        self.assertFalse(validate(schema, [b"a", b"b"]).valid)

    def test_synthetic_names(self):
        p = CDDLParser("r = { a: { b: uint }, c: [ * { d: int } ] }")
        self.assertEqual(p.types['r']['fields']['a']['type'], 'r@a')
        self.assertEqual(p.types['r@a']['fields'], {'b': field('b', 'uint')})
        self.assertEqual(p.synthetic_types, {'r@a', 'r@c@0'})

    def test_synthetic_names_never_collide_or_contain_dots(self):
        p = CDDLParser('r = { "a.b": { x: int } }\nr@a_b = int')
        name = p.types['r']['fields']['a.b']['type']
        self.assertNotIn('.', name)
        self.assertNotEqual(name, 'r@a_b')

    def test_edn_does_not_show_synthetic_names(self):
        v = Validator("r = { a: { b: uint }, e: s }\ns = { f: int }")
        edn = v.to_edn({"a": {"b": 1}, "e": {"f": 2}})
        self.assertNotIn('@', edn)
        self.assertIn('/ s /', edn)


class TestKeyTypeMembers(unittest.TestCase):
    """#104: map members written 'key => type'."""

    def test_issue_examples(self):
        schema = "k = { 1 => int, ? 2 => bstr }"
        self.assertTrue(validate(schema, {1: 5}).valid)
        self.assertFalse(validate(schema, {1: "x"}).valid)
        self.assertFalse(validate(schema, {}).valid)

    def test_cose_key(self):
        for data in ({1: 2, 3: -7}, {1: 2, -1: 1}, {1: 2, -2: b"x", "kid": "y"}):
            with self.subTest(data=data):
                result = validate(SCHEMAS / "unified.cddl", data, root_type="COSE_Key")
                self.assertTrue(result.valid, result.errors)
        result = validate(SCHEMAS / "unified.cddl", {1: 2, 1.5: 0}, root_type="COSE_Key")
        self.assertFalse(result.valid)

    def test_no_bogus_aliases(self):
        p = CDDLParser((SCHEMAS / "unified.cddl").read_text(encoding="utf-8"))
        self.assertFalse([name for name in p.type_aliases if not name[0].isalpha()
                          and name[0] not in '$@_'])
        self.assertEqual(sorted(p.types['COSE_Key']['fields']), [1, 2, 3, 4, 5])

    def test_computed_keys(self):
        schema = "m = { a: int, * tstr => uint }"
        self.assertTrue(validate(schema, {"a": 1, "x": 2, "y": 3}).valid)
        result = validate(schema, {"a": 1, "x": "no"})
        self.assertFalse(result.valid)
        self.assertIn("Value for key 'x'", result.errors[0])
        result = validate(schema, {"a": 1, 5: 2})
        self.assertIn("Unknown fields", result.errors[0])

    def test_uncheckable_computed_key_matches_nothing(self):
        # A key type the validator cannot check yet must not let every extra
        # key through (range keys are #71; '&(n: c)' with a named value is #70).
        for schema in ("m = { a: int, 0..255 => tstr }",
                       "m = { a: int, &(n: c) => tstr }\nc = 1"):
            with self.subTest(schema=schema):
                result = validate(schema, {"a": 1, "foo": "x"})
                self.assertFalse(result.valid)
                self.assertIn("Unknown fields", result.errors[0])

    def test_any_and_socket_keys(self):
        self.assertTrue(validate("m = { * any => int }", {"x": 1, 2: 3}).valid)
        socket = "m = { * $k => int }\n$k /= tstr\n$k /= uint"
        self.assertTrue(validate(socket, {"x": 1, 2: 3}).valid)
        self.assertFalse(validate(socket, {1.5: 1}).valid)

    def test_computed_key_table_entry(self):
        p = CDDLParser("m = { * tstr => any, ? int => { a: int } }")
        self.assertEqual(p.types['m']['computed_keys'],
                         [{'key': 'tstr', 'type': 'any'}, {'key': 'int', 'type': 'm@value'}])


class TestBundledSchemas(unittest.TestCase):

    def test_corim_types(self):
        p = CDDLParser((SCHEMAS / "unified.cddl").read_text(encoding="utf-8"))
        # optional inline group inside non-empty<{...}>
        fields = p.types['measurement-values-map']['fields']
        self.assertTrue(fields[4]['optional'] and fields[5]['optional'])
        # positional array elements are no longer dropped
        self.assertEqual(p.types['int-range']['element_types'],
                         {0: 'int / negative-inf', 1: 'int / positive-inf'})
        self.assertIn('protected-corim-header-map-inline', p.types)
        self.assertEqual(p.type_aliases['tagged-masked-raw-value'],
                         '#6.563(tagged-masked-raw-value@tag)')


if __name__ == "__main__":
    unittest.main()
