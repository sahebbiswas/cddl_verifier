"""Data is validated against a generic instance as the rule with its arguments put in (RFC 8610 section 3.10, #243).

``r = g<int>`` is ``r = int / nil`` when ``g<T> = T / nil``, wherever the instance sits: as the
root, a map member, an array element, a choice alternative or the argument of another generic. A
generic rule is no root: the first rule that is not generic is.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator
from cddl_verifier.cbor import CBORTag, encode


def ok(schema, value, root="r"):
    return Validator(schema).validate(encode(value), root_type=root).valid


class TheRoot(unittest.TestCase):
    """A generic rule written first does not become the root."""

    def test_instance_after_the_generic_rule_is_the_default_root(self):
        v = Validator('g<T> = T / nil\nr = g<int>')
        self.assertEqual(v.default_root_type, 'r')
        self.assertFalse(v.validate(encode("x")).valid)
        self.assertTrue(v.validate(encode(None)).valid)
        self.assertTrue(v.validate(encode(5)).valid)

    def test_each_example_of_the_issue(self):
        cases = [('g<T> = T / nil\nr = g<int>', "x", False),
                 ('g<T> = uint .eq T\nr = g<5>', 7, False),
                 ('g<T> = uint .eq T\nr = g<5>', 5, True),
                 ('g<T> = tstr .size T\nr = g<2>', "abc", False),
                 ('g<T> = tstr .size T\nr = g<2>', "ab", True),
                 ('g<T> = T .size 2\nr = g<tstr>', "abc", False),
                 ('g<T> = T .size 2\nr = g<tstr>', "ab", True)]
        for schema, value, expected in cases:
            with self.subTest(schema=schema, value=value):
                self.assertEqual(Validator(schema).validate(encode(value)).valid, expected)

    def test_a_schema_of_generics_only_keeps_no_root(self):
        self.assertIsNone(Validator('g<T> = [T]\nh<T> = {a: T}').default_root_type)

    def test_the_first_plain_rule_is_the_root_wherever_the_generics_are(self):
        v = Validator('a = int\ng<T> = [T]\nr = g<int>')
        self.assertEqual(v.default_root_type, 'a')


class WhereTheInstanceSits(unittest.TestCase):
    def test_choice_alternative(self):
        schema = 'g<T> = T / nil\nr = g<int> / tstr'
        self.assertTrue(ok(schema, "s"))
        self.assertTrue(ok(schema, None))
        self.assertFalse(ok(schema, 1.5))

    def test_map_member(self):
        schema = 'g<T> = {a: T}\nr = {m: g<int>}'
        self.assertTrue(ok(schema, {"m": {"a": 1}}))
        self.assertFalse(ok(schema, {"m": {"a": "x"}}))

    def test_array_element(self):
        schema = 'g<T> = {a: T}\nr = [g<int>]'
        self.assertTrue(ok(schema, [{"a": 1}]))
        self.assertFalse(ok(schema, [{"a": "x"}]))

    def test_repeated_array_element(self):
        for occurrence in ('*', '+', '1*3'):
            schema = f'g<T> = {{a: T}}\nr = [{occurrence} g<int>]'
            with self.subTest(occurrence=occurrence):
                self.assertTrue(ok(schema, [{"a": 1}, {"a": 2}]))
                self.assertFalse(ok(schema, [{"a": 1}, {"a": "x"}]))

    def test_repeated_array_element_inside_a_map_member(self):
        schema = 'g<T> = {a: T}\nr = {xs: [* g<int>]}'
        self.assertTrue(ok(schema, {"xs": [{"a": 1}]}))
        self.assertFalse(ok(schema, {"xs": [{"a": "x"}]}))
        self.assertFalse(ok('g<T> = [T]\nr = {xs: [* g<int>]}', {"xs": [["x"]]}))

    def test_repeated_choice_instance(self):
        schema = 'g<T> = T / nil\nr = [* g<int>]'
        self.assertTrue(ok(schema, [1, None]))
        self.assertFalse(ok(schema, [1, "x"]))

    def test_repeated_generic_group_is_left_to_the_group(self):
        schema = 'pair<K, V> = (K, V)\nr = [* pair<tstr, int>]'
        self.assertTrue(ok(schema, ["a", 1, "b", 2]))
        self.assertFalse(ok(schema, ["a", "b"]))

    def test_tag_and_embedded_cbor(self):
        self.assertFalse(ok('g<T> = #6.100(T)\nr = g<int>', CBORTag(100, "x")))
        self.assertTrue(ok('g<T> = #6.100(T)\nr = g<int>', CBORTag(100, 1)))
        self.assertFalse(ok('g<T> = bstr .cbor T\nr = g<[* int]>', encode(["x"])))
        self.assertTrue(ok('g<T> = bstr .cbor T\nr = g<[* int]>', encode([1])))


class Arguments(unittest.TestCase):
    def test_parameter_as_a_control_argument(self):
        self.assertFalse(ok('g<T> = uint .le T\nr = g<5>', 6))
        self.assertTrue(ok('g<T> = uint .le T\nr = g<5>', 4))
        self.assertFalse(ok('g<T> = text .regexp T\nr = g<"a+">', "b"))
        self.assertTrue(ok('g<T> = text .regexp T\nr = g<"a+">', "aa"))
        self.assertFalse(ok('g<T> = T .within uint\nr = g<1..5>', 7))

    def test_parameter_as_a_key_and_a_value(self):
        schema = 'g<K, V> = {K => V}\nr = g<tstr, int>'
        self.assertTrue(ok(schema, {"a": 1}))
        self.assertFalse(ok(schema, {"a": "b"}))
        self.assertFalse(ok(schema, {1: 1}))

    def test_parameter_as_the_whole_rule(self):
        schema = 'g<T> = T\nr = g<(int / tstr)>'
        self.assertTrue(ok(schema, "a"))
        self.assertFalse(ok(schema, 1.5))

    def test_a_generic_that_takes_a_generic(self):
        schema = 'h<T> = [T]\ng<T> = h<h<T>>\nr = g<int>'
        self.assertTrue(ok(schema, [[1]]))
        self.assertFalse(ok(schema, [["x"]]))
        schema = 'g<T> = {a: T}\nh<U> = g<[U]>\nr = h<int>'
        self.assertTrue(ok(schema, {"a": [1]}))
        self.assertFalse(ok(schema, {"a": ["x"]}))
        schema = 'g<T> = [* T]\nr = g<g<int>>'
        self.assertTrue(ok(schema, [[1], [2]]))
        self.assertFalse(ok(schema, [[1], ["x"]]))

    def test_feature_control_with_a_parameter(self):
        schema = 'g<T> = int .feature T\nr = g<"ext">'
        result = Validator(schema).validate(encode(5), root_type="r")
        self.assertTrue(result.valid)
        self.assertEqual(result.features_used, ("ext",))
        off = Validator(schema, features=[]).validate(encode(5), root_type="r")
        self.assertFalse(off.valid)
        both = 'JC<J, C> = J .feature "json" / C .feature "cbor"\nr = JC<tstr, int>'
        self.assertTrue(Validator(both, features=["cbor"]).validate(encode(5), root_type="r").valid)
        self.assertFalse(Validator(both, features=["cbor"]).validate(encode("a"), root_type="r").valid)


class Recursion(unittest.TestCase):
    TREE = 'tree<T> = {v: T, ? kids: [* tree<T>]}\nr = tree<int>'

    def test_every_depth_is_checked(self):
        self.assertTrue(ok(self.TREE, {"v": 1, "kids": [{"v": 2, "kids": [{"v": 3}]}]}))
        self.assertFalse(ok(self.TREE, {"v": 1, "kids": [{"v": "x"}]}))
        self.assertFalse(ok(self.TREE, {"v": 1, "kids": [{"v": 2, "kids": [{"v": "y"}]}]}))

    def test_a_single_child_and_an_array_of_one(self):
        self.assertFalse(ok('tree<T> = {v: T, ? kid: tree<T>}\nr = tree<int>', {"v": 1, "kid": {"v": "x"}}))
        self.assertFalse(ok('tree<T> = {v: T, ? kids: [tree<T>]}\nr = tree<int>', {"v": 1, "kids": [{"v": "x"}]}))


if __name__ == "__main__":
    unittest.main()
