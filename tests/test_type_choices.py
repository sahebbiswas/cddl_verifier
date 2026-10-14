#!/usr/bin/env python3
"""
Inline type choices (``c = a / b``) in map fields, arrays, inline arrays and
at the root, root rules that are primitives with controls, parenthesized
types (``(uint / nil) .size 1``), type sockets and choices from groups
(``&(a: 0, b: 1)``).
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator, validate
from cddl_verifier._analyzer import CBORAnalyzer, CDDLParser
from cddl_verifier.cbor import encode
from cddl_verifier._cddl import query
from cddl_verifier._cddl.parser import parse_type_expr
from cddl_verifier._cddl.printer import format_node
from cddl_verifier.cbor import CBORTag

RULES = "\nm = {a: uint}\nn = {b: tstr}\nt = #6.7(m)"


class ChoiceTestCase(unittest.TestCase):

    def check(self, cases):
        for schema, data, expected in cases:
            with self.subTest(schema=schema, data=data):
                result = validate(schema, data)
                self.assertEqual(result.valid, expected, result.errors)


class TestFieldChoices(ChoiceTestCase):

    def test_primitive_alternatives(self):
        self.check([
            ("r = {x: c}\nc = uint / tstr", {"x": 1}, True),
            ("r = {x: c}\nc = uint / tstr", {"x": "a"}, True),     # was rejected
            ("r = {x: c}\nc = uint / tstr", {"x": 1.5}, False),
            ("r = {x: uint / tstr}", {"x": "a"}, True),
            ("r = {x: uint / tstr}", {"x": b"a"}, False),
        ])

    def test_named_rule_alternatives(self):
        self.check([
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"a": 1}}, True),
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"b": "q"}}, True),
            ("r = {x: c}\nc = m / n" + RULES, {"x": {"zz": 1}}, False),  # was accepted
            ("r = {x: c}\nc = m / n" + RULES, {"x": 5}, False),          # was accepted
        ])

    def test_tagged_alternatives(self):
        self.check([
            ("r = {x: c}\nc = t / m" + RULES, {"x": CBORTag(7, {"a": 1})}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": {"a": 1}}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": CBORTag(8, {"a": 1})}, False),  # was accepted
        ])

    def test_controls_in_alternatives(self):
        self.check([
            ("r = {x: tstr .size 2 / bstr}", {"x": "ab"}, True),
            ("r = {x: tstr .size 2 / bstr}", {"x": "abc"}, False),
            ("r = {x: tstr .size 2 / bstr}", {"x": b"abc"}, True),
            ("r = {x: c}\nc = uint .le 10 / tstr", {"x": 10}, True),
            ("r = {x: c}\nc = uint .le 10 / tstr", {"x": 11}, False),
            ('r = {x: c}\nc = tstr .regexp "a/b" / uint', {"x": "a/b"}, True),
            ('r = {x: c}\nc = tstr .regexp "a/b" / uint', {"x": "zz"}, False),
        ])

    def test_embedded_cbor_alternative(self):
        from cddl_verifier.cbor import encode
        schema = "r = [p]\np = bstr .cbor m / tstr\nm = {a: uint}"
        self.check([
            (schema, [encode({"a": 1})], True),
            (schema, [encode({"a": "x"})], False),   # was accepted: any bytes passed
            (schema, [b"\xff"], False),              # does not decode
            (schema, ["s"], True),
        ])

    def test_inline_cbor_and_tag_alternatives_in_field(self):
        from cddl_verifier.cbor import encode
        cbor_field = "r = {x: bstr .cbor m / tstr}\nm = {a: uint}"
        self.check([
            (cbor_field, {"x": "s"}, True),
            (cbor_field, {"x": encode({"a": 1})}, True),
            (cbor_field, {"x": encode({"a": "z"})}, False),
            ("r = {x: #6.7(uint) / tstr}", {"x": "s"}, True),
            ("r = {x: #6.7(uint) / tstr}", {"x": CBORTag(7, 1)}, True),
            ("r = {x: #6.7(uint) / tstr}", {"x": CBORTag(8, 1)}, False),
            ("r = [#6.7(uint) / tstr]", ["s"], True),
            ("n = #6.7(uint) / tstr", "s", True),
            ("n = #6.7(uint) / tstr", CBORTag(8, 1), False),
        ])

    def test_tagged_text_literal_with_parenthesis(self):
        schema = 'n = #6.7(")") / uint'
        self.check([
            (schema, CBORTag(7, ")"), True),
            (schema, 5, True),
            (schema, CBORTag(7, "x"), False),
            (schema, CBORTag(8, ")"), False),
            (schema, "zz", False),
        ])

    def test_literal_alternatives(self):
        schema = 'r = {x: c}\nc = 1 / 2 / "three" / 1.5'
        self.check([
            (schema, {"x": 2}, True),
            (schema, {"x": "three"}, True),
            (schema, {"x": 1.5}, True),
            (schema, {"x": 3}, False),
            (schema, {"x": True}, False),   # true is not 1
            ("r = {x: c}\nc = true / uint", {"x": True}, True),
            ("r = {x: c}\nc = true / uint", {"x": False}, False),
        ])

    def test_alias_chains_and_optional_fields(self):
        self.check([
            ("r = {x: c}\nc = d / tstr\nd = e\ne = uint", {"x": 5}, True),
            ("r = {x: c}\nc = d / tstr\nd = e\ne = uint", {"x": 1.5}, False),
            ("r = {? x: c}\nc = uint / tstr", {}, True),
            ("r = {? x: c}\nc = uint / tstr", {"x": None}, False),
        ])

    def test_error_lists_each_alternative(self):
        result = validate("r = {x: c}\nc = uint / tstr", {"x": 1.5})
        self.assertEqual(result.errors[0],
                         "Field 'x' in 'r' matches none of uint / tstr "
                         "(uint: expected uint, got 1.5; tstr: expected tstr, got 1.5)")


class TestArrayChoices(ChoiceTestCase):

    def test_declared_array(self):
        self.check([
            ("r = [* c]\nc = m / n" + RULES, [{"a": 1}, {"b": "x"}], True),  # second was rejected
            ("r = [* c]\nc = m / n" + RULES, [{"zz": 1}], False),
            ("r = [* uint / tstr]", [1, "a"], True),
            ("r = [* uint / tstr]", [1.5], False),
        ])

    def test_inline_array_field(self):
        self.check([
            ("r = {x: [* c]}\nc = m / n" + RULES, {"x": [{"b": "x"}]}, True),
            ("r = {x: [* c]}\nc = m / n" + RULES, {"x": [5]}, False),
        ])

    def test_group_choice_is_not_split(self):
        def split(text):
            return [format_node(alt) for alt in query.alternatives(parse_type_expr(text))]

        self.assertEqual(split("[a / b] / c"), ["[ a / b ]", "c"])
        self.assertEqual(split('tstr .regexp "x/y"'), ['tstr .regexp "x/y"'])
        self.assertEqual(split("#6.1(a / b) / c"), ["#6.1(a / b)", "c"])


class TestRootRules(ChoiceTestCase):

    def test_root_choice(self):
        self.check([
            ("n = uint / tstr", "x", True),
            ("n = uint / tstr", 1, True),
            ("n = uint / tstr", 1.5, False),
            ("r = c\nc = t / m" + RULES, CBORTag(7, {"a": 1}), True),
            ("r = c\nc = t / m" + RULES, {"a": 1}, True),
            ("r = c\nc = t / m" + RULES, CBORTag(8, {"a": 1}), False),
        ])

    def test_root_controlled_primitive(self):
        self.check([
            ("n = tstr .size 2", "ab", True),
            ("n = tstr .size 2", "abc", False),
            ("n = uint .le 5", 5, True),
            ("n = uint .le 5", 6, False),
            ('n = tstr .regexp "[a-z]+"', "abc", True),
            ('n = tstr .regexp "[a-z]+"', "ABC", False),
            ("n = uint", -1, False),
            ("a = n\nn = tstr", "x", True),
        ])

    def test_root_error_message(self):
        result = validate("n = uint / tstr", 1.5)
        self.assertEqual(result.errors[0],
                         "Value does not match type 'n': matches none of uint / tstr "
                         "(uint: expected uint, got 1.5; tstr: expected tstr, got 1.5)")


class TestParenthesizedTypes(ChoiceTestCase):
    """``(T)`` is checked as ``T``; controls outside the parentheses apply to
    each alternative inside them (#118)."""

    def test_field(self):
        self.check([
            ("r = { o: (tstr) }", {"o": "x"}, True),
            ("r = { o: (tstr) }", {"o": 1}, False),              # was accepted
            ("r = { o: ((tstr)) }", {"o": 1}, False),
            ("r = { o: (m) }" + RULES, {"o": {"a": 1}}, True),
            ("r = { o: (m) }" + RULES, {"o": {"a": "x"}}, False),
            ("r = { o: (#6.7(uint)) }", {"o": CBORTag(7, 1)}, True),
            ("r = { o: (#6.7(uint)) }", {"o": CBORTag(7, "x")}, False),
        ])

    def test_controls_apply_to_each_alternative(self):
        self.check([
            ("r = { o: (uint) .size 1 }", {"o": 255}, True),
            ("r = { o: (uint) .size 1 }", {"o": 1000}, False),   # was accepted
            ("r = { o: (uint / nil) .size 1 }", {"o": 10}, True),
            ("r = { o: (uint / nil) .size 1 }", {"o": 1000}, False),
            # .size is not defined for nil (RFC 8610 3.8.1)
            ("r = { o: (uint / nil) .size 1 }", {"o": None}, False),
            ("r = { o: (uint / tstr) .size 1 }", {"o": "a"}, True),
            ("r = { o: (uint / tstr) .size 1 }", {"o": "ab"}, False),
            # controls inside the parentheses still apply
            ('r = { o: (tstr .size 3) .regexp "a+" }', {"o": "aaa"}, True),
            ('r = { o: (tstr .size 3) .regexp "a+" }', {"o": "aab"}, False),
            ('r = { o: (tstr .size 3) .regexp "a+" }', {"o": "aaaa"}, False),
            ("r = { o: (bstr) .cbor uint }", {"o": b"\x01"}, True),
            ("r = { o: (bstr) .cbor uint }", {"o": b"\x61a"}, False),
        ])

    def test_control_on_named_choice(self):
        # 'm .size 1' with 'm = uint / nil' is '(uint / nil) .size 1'
        self.check([
            ("r = { o: m .size 1 }\nm = uint / nil", {"o": 5}, True),
            ("r = { o: m .size 1 }\nm = uint / nil", {"o": 1000}, False),  # was accepted
            ("r = { o: m .size 1 }\nm = uint / nil", {"o": "abc"}, False),
            ("r = { o: m .size 2 }\nm = bstr / tstr", {"o": "ab"}, True),
            ("r = { o: m .size 2 }\nm = bstr / tstr", {"o": b"abc"}, False),
            ("r = m .size 2\nm = bstr / tstr", encode(b"ab"), True),
            ("r = m .size 2\nm = bstr / tstr", "abc", False),
        ])

    def test_generic_instance_under_a_control(self):
        schema = "r = { o: opt<uint> .size 1 }\nopt<T> = T / nil"
        self.assertEqual(format_node(CDDLParser(schema).types["r"]["fields"]["o"]["type"]),
                         "(uint / nil) .size 1")
        self.check([
            (schema, {"o": 7}, True),
            (schema, {"o": 1000}, False),                       # was accepted
        ])

    def test_arrays_and_root(self):
        self.check([
            ("r = [ (tstr) ]", ["a"], True),
            ("r = [ (tstr) ]", [1], False),                      # was accepted
            ("r = [ * (tstr) ]", ["a", 1], False),
            ("r = { o: [* (uint) .le 3] }", {"o": [1, 3]}, True),
            ("r = { o: [* (uint) .le 3] }", {"o": [1, 5]}, False),
            ("r = (tstr)", 1, False),
            ("r = (tstr) .size 2", "ab", True),
            ("r = (tstr) .size 2", "abc", False),
        ])

    def test_error_message(self):
        result = validate("r = { o: (uint / nil) .size 1 }", {"o": 1000})
        self.assertEqual(list(result.errors), [
            "Field 'o' in 'r' matches none of (uint / nil) .size 1 "
            "(uint: .size 1000 does not fit in 1 byte(s) (.size 1); "
            "nil: expected nil, got 1000)"])
        result = validate("r = { o: (tstr) }", {"o": 1})
        self.assertEqual(list(result.errors), ["Field 'o' in 'r' expected tstr, got 1"])


class TestSockets(ChoiceTestCase):
    """A field or element typed ``$socket`` is checked like the inline
    choice of its ``/=`` alternatives (#119)."""

    ROLE = "\n$role /= uint\n$role /= tstr"

    def test_primitive_alternatives_in_a_field(self):
        schema = "r = { role: $role }" + self.ROLE
        self.check([
            (schema, {"role": 1}, True),
            (schema, {"role": "x"}, True),
            (schema, {"role": 1.5}, False),                     # was accepted
            (schema, {"role": [1]}, False),                     # was accepted
        ])

    def test_arrays(self):
        self.check([
            ("r = [ * $role ]" + self.ROLE, [1, "a"], True),
            ("r = [ * $role ]" + self.ROLE, [1, 1.5], False),   # was accepted
            ("r = { o: [ * $role ] }" + self.ROLE, {"o": [1]}, True),
            ("r = { o: [ * $role ] }" + self.ROLE, {"o": [1, 1.5]}, False),
        ])

    def test_mixed_and_single_alternatives(self):
        mixed = "r = { o: $s }\n$s /= uint\n$s /= m" + RULES
        one = "r = { o: $one }\n$one /= text"
        defined = "r = { o: $s }\n$s = { a: uint }"
        self.check([
            (mixed, {"o": 1}, True),
            (mixed, {"o": {"a": 1}}, True),
            (mixed, {"o": {"a": "x"}}, False),
            (mixed, {"o": "x"}, False),                         # was accepted
            (one, {"o": "a"}, True),
            (one, {"o": 5}, False),                             # was accepted
            (defined, {"o": {"a": 1}}, True),
            (defined, {"o": {"a": "x"}}, False),                # was accepted
        ])

    def test_socket_under_a_control(self):
        schema = "r = { o: $s .size 1 }\n$s /= uint\n$s /= tstr"
        self.check([
            (schema, {"o": "a"}, True),
            (schema, {"o": "abc"}, False),                      # was accepted
        ])

    def test_empty_socket_matches_nothing(self):
        schema = "r = { ? o: $e }"
        self.check([(schema, {}, True), (schema, {"o": 1}, False)])
        # a control does not give an empty socket any values (review of #131)
        self.check([
            ("r = { ? o: $e .size 1 }", {}, True),
            ("r = { ? o: $e .size 1 }", {"o": 1}, False),
            ("r = [ * $e .size 1 ]", [], True),
            ("r = [ * $e .size 1 ]", [1], False),
        ])
        self.assertEqual(list(validate(schema, {"o": 1}).errors),
                         ["Field 'o' in 'r' type socket $e has no alternatives, "
                          "so no value matches it"])

    def test_choice_from_group(self):
        colors = "r = { c: &colors }\ncolors = (red: 0, green: 1, ? blue: 2)"
        nested = "r = { c: &(base, z: 9) }\nbase = (x: 1, y: 2)"
        roles = "r = { o: $r }\n$r /= &(a: 0)\n$r /= &(b: 1)"
        self.check([
            (colors, {"c": 2}, True),
            (colors, {"c": 3}, False),                          # was accepted
            ('r = { c: &(x: 1, y: "s") }', {"c": "s"}, True),
            ('r = { c: &(x: 1, y: "s") }', {"c": 2}, False),
            (nested, {"c": 9}, True),
            (nested, {"c": 2}, True),
            (nested, {"c": 3}, False),
            (roles, {"o": 1}, True),
            (roles, {"o": 7}, False),                           # was accepted
            (roles, {"o": "x"}, False),
        ])

    def test_choice_from_group_under_a_control(self):
        schema = "r = { c: &colors .size 1 }\ncolors = (red: 0, green: 1, big: 1000)"
        self.check([
            (schema, {"c": 0}, True),
            (schema, {"c": 1000}, False),                       # in the group, too big
            (schema, {"c": 5}, False),                          # not in the group
            (schema, {"c": "x"}, False),
        ])

    def test_deep_and_recursive_groups(self):
        def chain(n):
            return ("r = { c: &g0 }\n"
                    + "\n".join(f"g{i} = (g{i + 1}, a{i}: {i})" for i in range(n))
                    + f"\ng{n} = (z: 99)")
        recursive = "r = { c: &g }\ng = (a: 1, ? g)"
        self.check([
            (chain(20), {"c": 99}, True),
            (chain(20), {"c": 500}, False),                     # was accepted (review of #131)
            (recursive, {"c": 1}, True),
            (recursive, {"c": 2}, False),
        ])
        # a group too deep to expand is reported, not accepted
        result = validate(chain(70), {"c": 0})
        self.assertFalse(result.valid)
        self.assertIn("could not be expanded", result.errors[0])

    def test_socket_as_the_root(self):
        schema = "$s /= uint\n$s /= tstr"
        for data, expected in ((1, True), ("x", True), (1.5, False)):
            with self.subTest(data=data):
                result = validate(schema, data, root_type="$s")
                self.assertEqual(result.valid, expected, result.errors)


class TestUncheckableAlternatives(ChoiceTestCase):
    """An alternative the validator cannot check matches nothing (#106)."""

    UNWRAP = "r = {x: c}\nc = ~m / tstr\nm = {a: int}"

    def test_unwrap_alternative_does_not_accept_everything(self):
        self.check([
            (self.UNWRAP, {"x": 1.5}, False),
            (self.UNWRAP, {"x": b"a"}, False),
            (self.UNWRAP, {"x": "a"}, True),   # the checkable alternative still matches
        ])

    def test_unwrap_alternative_in_an_inline_choice_and_an_array(self):
        self.check([
            ("r = {x: ~m / tstr}\nm = {a: int}", {"x": 1.5}, False),
            ("r = {x: ~m / tstr}\nm = {a: int}", {"x": "a"}, True),
            ("r = [c]\nc = ~m / tstr\nm = [int]", [1.5], False),
            ("r = [c]\nc = ~m / tstr\nm = [int]", ["a"], True),
        ])

    def test_unwrap_as_a_field_type(self):
        self.check([
            ("r = {x: ~m}\nm = {a: int}", {"x": 1.5}, False),
        ])

    def test_unwrap_error_says_it_is_unsupported(self):
        result = validate(self.UNWRAP, {"x": 1.5})
        self.assertFalse(result.valid)
        self.assertIn("unwrapping a map or array gives a group", result.errors[0])
        result = validate("r = {x: ~m}\nm = {a: int}", {"x": 1.5})
        self.assertIn("cannot check '~m'", result.errors[0])

    def test_unwrap_group_splice_in_an_array_is_unchanged(self):
        self.check([
            ("r = [ ~hdr, x: int ]\nhdr = [ a: int ]", [1, 2], True),
        ])

    def test_checkable_alternatives_still_reject_and_accept(self):
        # range, inline map, generic and '.and' alternatives are checked (#71, #100)
        self.check([
            ("r = {x: c}\nc = 0..10 / tstr", {"x": 1.5}, False),
            ("r = {x: c}\nc = 0..10 / tstr", {"x": 5}, True),
            ("r = {x: c}\nc = {a: int} / tstr", {"x": 1.5}, False),
            ("r = {x: c}\nc = {a: int} / tstr", {"x": {"a": 1}}, True),
        ])


class TestCorimChoices(unittest.TestCase):

    def test_svn_type_choice(self):
        schema = Path(__file__).resolve().parent.parent / "cddl-schemas" / "unified.cddl"
        cases = [
            (5, True),                    # svn = uint
            (CBORTag(552, 5), True),             # tagged-svn
            (CBORTag(553, 5), True),             # tagged-min-svn
            (CBORTag(554, 5), False),            # some other tag
            ("five", False),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                result = validate(schema, data, root_type="svn-type-choice")
                self.assertEqual(result.valid, expected, result.errors)

    def test_socket_fields(self):
        # '$entity-name-type-choice' (text) and '$corim-role-type-choice'
        # ('&(manifest-creator: 1)' / ...) were not checked in fields (#119)
        schema = Path(__file__).resolve().parent.parent / "cddl-schemas" / "unified.cddl"
        cases = [
            ({0: "name", 2: [1]}, True),
            ({0: "name", 2: [7]}, False),
            ({0: 5, 2: [1]}, False),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                result = validate(schema, data, root_type="corim-entity-map")
                self.assertEqual(result.valid, expected, result.errors)



class TestTypeExtension(unittest.TestCase):
    """``a = int`` then ``a /= tstr`` is ``a = int / tstr``: the original definition stays (#86)."""

    def check(self, schema, valid, invalid):
        v = Validator(schema)
        for data in valid:
            self.assertTrue(v.validate(data).valid, data)
        for data in invalid:
            self.assertFalse(v.validate(data).valid, data)

    def test_alias_base(self):
        self.check("r = a\na = int\na /= tstr", [1, "x"], [b"x"])

    def test_extension_before_its_base(self):
        self.check("r = a\na /= tstr\na = int", [1, "x"], [b"x"])

    def test_map_base(self):
        self.check("r = a\na = {x: int}\na /= tstr", [{"x": 1}, "x"], [{"x": "y"}, 1])

    def test_several_extensions_and_a_choice_base(self):
        self.check("r = [* a]\na = int / bool\na /= tstr\na /= nil", [[1, True, "s", None]], [[1.5]])

    def test_as_field_type_and_through_an_alias(self):
        self.check("r = {f: b}\na = uint\na /= tstr\nb = a", [{"f": 1}, {"f": "x"}], [{"f": -1}])

    def test_base_with_an_inline_map_field(self):
        # the field's inline map is already named 'a@base'
        self.check("r = a\na = {base: {x: int}}\na /= tstr",
                   [{"base": {"x": 1}}, "s"], [{"base": {"x": "no"}}, 1])

    def test_sockets_still_start_empty(self):
        self.check("r = $s\n$s /= int\n$s /= tstr", [1, "x"], [b"x"])


if __name__ == "__main__":
    unittest.main()
