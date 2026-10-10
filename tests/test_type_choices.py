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

from cddl_verifier import validate
from cddl_verifier._analyzer import CBORAnalyzer, CDDLParser
from cddl_verifier.cbor import encode

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
            ("r = {x: c}\nc = t / m" + RULES, {"x": (7, {"a": 1})}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": {"a": 1}}, True),
            ("r = {x: c}\nc = t / m" + RULES, {"x": (8, {"a": 1})}, False),  # was accepted
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
            ("r = {x: #6.7(uint) / tstr}", {"x": (7, 1)}, True),
            ("r = {x: #6.7(uint) / tstr}", {"x": (8, 1)}, False),
            ("r = [#6.7(uint) / tstr]", ["s"], True),
            ("n = #6.7(uint) / tstr", "s", True),
            ("n = #6.7(uint) / tstr", (8, 1), False),
        ])

    def test_tagged_text_literal_with_parenthesis(self):
        schema = 'n = #6.7(")") / uint'
        self.check([
            (schema, (7, ")"), True),
            (schema, 5, True),
            (schema, (7, "x"), False),
            (schema, (8, ")"), False),
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
        self.assertEqual(CBORAnalyzer._split_choice("a // b"), ["a // b"])
        self.assertEqual(CBORAnalyzer._split_choice("[a / b] / c"), ["[a / b]", "c"])
        self.assertEqual(CBORAnalyzer._split_choice('tstr .regexp "x/y"'), ['tstr .regexp "x/y"'])
        self.assertEqual(CBORAnalyzer._split_choice("#6.1(a / b) / c"), ["#6.1(a / b)", "c"])


class TestRootRules(ChoiceTestCase):

    def test_root_choice(self):
        self.check([
            ("n = uint / tstr", "x", True),
            ("n = uint / tstr", 1, True),
            ("n = uint / tstr", 1.5, False),
            ("r = c\nc = t / m" + RULES, (7, {"a": 1}), True),
            ("r = c\nc = t / m" + RULES, {"a": 1}, True),
            ("r = c\nc = t / m" + RULES, (8, {"a": 1}), False),
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
            ("r = { o: (#6.7(uint)) }", {"o": (7, 1)}, True),
            ("r = { o: (#6.7(uint)) }", {"o": (7, "x")}, False),
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
        self.assertEqual(CDDLParser(schema).types["r"]["fields"]["o"]["type"],
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


class TestCorimChoices(unittest.TestCase):

    def test_svn_type_choice(self):
        schema = Path(__file__).resolve().parent.parent / "cddl-schemas" / "unified.cddl"
        cases = [
            (5, True),                    # svn = uint
            ((552, 5), True),             # tagged-svn
            ((553, 5), True),             # tagged-min-svn
            ((554, 5), False),            # some other tag
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


if __name__ == "__main__":
    unittest.main()
