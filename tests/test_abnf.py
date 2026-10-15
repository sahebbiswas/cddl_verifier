#!/usr/bin/env python3
"""ABNF for ``.abnf`` and ``.abnfb`` (RFC 9165 section 3, RFC 5234, RFC 7405) (#205)."""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest
from unittest import mock

from cddl_verifier import SchemaError, Validator, cbor
from cddl_verifier._cddl import abnf


def accepts(text, string):
    return abnf.matches(text, [ord(c) for c in string])


class Parsing(unittest.TestCase):
    def test_element_then_rules(self):
        grammar = 'a\na = *"a" *"b"\n'
        for text, expected in (("", True), ("a", True), ("b", True), ("aabbb", True), ("c", False), ("ba", False)):
            self.assertEqual(accepts(grammar, text), expected, text)

    def test_element_may_be_an_expression(self):
        self.assertTrue(accepts('"x" d\nd = %x30-39\n', "x5"))
        self.assertFalse(accepts('"x" d\nd = %x30-39\n', "5"))

    def test_rules_may_be_indented_and_continue_over_lines(self):
        grammar = '\n  n\n  n = d\n    / "x"\n  d = %x30-39\n'
        self.assertTrue(accepts(grammar, "7"))
        self.assertTrue(accepts(grammar, "x"))
        self.assertFalse(accepts(grammar, "y"))

    def test_comments_are_skipped(self):
        self.assertTrue(accepts('a ; the element\na = "x" ; one x\n', "x"))

    def test_incremental_alternatives(self):
        grammar = 'a\na = "x"\na =/ "y"\n'
        self.assertTrue(accepts(grammar, "y"))
        self.assertFalse(accepts(grammar, "z"))

    def test_rule_names_ignore_case(self):
        self.assertTrue(accepts('Digit\ndigit = %x30-39\n', "4"))

    def test_strings_ignore_case_unless_marked(self):
        self.assertTrue(accepts('s\ns = "Ab"\n', "aB"))
        self.assertTrue(accepts('s\ns = %i"Ab"\n', "aB"))
        self.assertTrue(accepts('s\ns = %s"Ab"\n', "Ab"))
        self.assertFalse(accepts('s\ns = %s"Ab"\n', "ab"))

    def test_numeric_values(self):
        self.assertTrue(accepts("s\ns = %x61.62.63\n", "abc"))
        self.assertTrue(accepts("s\ns = %d97-99\n", "b"))
        self.assertTrue(accepts("s\ns = %b1100001\n", "a"))
        self.assertTrue(accepts("s\ns = %x20-10FFFF\n", "€"))
        self.assertFalse(accepts("s\ns = %x30-39\n", "a"))

    def test_repetition_forms(self):
        for grammar, text, expected in (
                ('s\ns = 3"a"\n', "aaa", True), ('s\ns = 3"a"\n', "aa", False),
                ('s\ns = 2*3"a"\n', "aaaa", False), ('s\ns = 2*3"a"\n', "aa", True),
                ('s\ns = 2*"a"\n', "aaaaa", True), ('s\ns = *2"a"\n', "aaa", False),
                ('s\ns = ["a"] "b"\n', "b", True), ('s\ns = ["a"] "b"\n', "ab", True),
                ('s\ns = ("a" / "b") "c"\n', "bc", True), ('s\ns = 1*("a" "b")\n', "abab", True)):
            self.assertEqual(accepts(grammar, text), expected, (grammar, text))

    def test_rfc3339_date(self):
        grammar = ('date\ndate = 4DIGIT "-" 2DIGIT "-" 2DIGIT\nDIGIT = %x30-39\n')
        self.assertTrue(accepts(grammar, "2024-02-29"))
        self.assertFalse(accepts(grammar, "2024-2-29"))

    def test_errors(self):
        for text, fragment in (
                ('a\na = <prose>', "prose"),
                ('a\n', "not defined"),
                ('a\na = b', "'b' is not defined"),
                ('a = "x"', "must start with the element"),
                ('a\na = "x"\na = "y"', "defined twice"),
                ('a\na = ("x"', "not closed"),
                ('a\nb =/ "x"', "extends a rule"),
                ('a\na = 3*2"x"', "no repeats"),
                ('a\na = %x39-30', "empty"),
                ('a\na = "ä"', "characters ABNF does not allow"),
                ('a\na = @', "unexpected")):
            with self.assertRaises(abnf.AbnfError, msg=text) as caught:
                abnf.compile_abnf(text)
            self.assertIn(fragment, str(caught.exception))


class Matching(unittest.TestCase):
    def test_left_recursion(self):
        self.assertTrue(accepts('x\nx = x "a" / "b"\n', "baaa"))
        self.assertFalse(accepts('x\nx = x "a" / "b"\n', "aaa"))

    def test_right_and_mutual_recursion(self):
        grammar = 'e\ne = "" / "(" e ")" e\n'
        self.assertTrue(accepts(grammar, "(()())"))
        self.assertFalse(accepts(grammar, "(()"))
        self.assertTrue(accepts('a\na = "x" b\nb = "y" a / ""\n', "xyxyx"))

    def test_an_empty_repeat_ends(self):
        self.assertTrue(accepts('e\ne = *e "a" / ""\n', "aaa"))
        self.assertTrue(accepts('e\ne = 100000000*""\n', ""))  # a huge minimum of empty matches
        self.assertFalse(accepts('e\ne = e\n', ""))  # no base case, so no match

    def test_ambiguity_does_not_blow_up(self):
        grammar = 'a\na = *("a" / "a" / "aa")\n'
        self.assertTrue(accepts(grammar, "a" * 400))
        self.assertFalse(accepts(grammar, "a" * 400 + "b"))

    def test_deep_recursion_is_a_limit_not_a_crash(self):
        # one level of recursion per symbol: a long string runs out of stack, which is a limit
        with self.assertRaises(abnf.AbnfLimit):
            accepts('s\ns = "a" s / ""\n', "a" * 50000)
        self.assertTrue(accepts('s\ns = "a" s / ""\n', "a" * 100))
        validator = Validator('r = text .abnf "s\\ns = \\"a\\" s / \\"\\"\\n"')
        result = validator.validate(cbor.encode("a" * 50000))
        self.assertFalse(result.valid)
        self.assertIn("too much work", str(result.errors[0]))

    def test_deeply_nested_text_is_an_error(self):
        text = "x\nx = " + "(" * 20000 + '"a"' + ")" * 20000
        with self.assertRaises(abnf.AbnfError):
            abnf.compile_abnf(text)

    def test_work_is_bounded(self):
        with mock.patch.object(abnf, "MAX_STEPS", 50):
            with self.assertRaises(abnf.AbnfLimit):
                accepts('a\na = *("a" / "b")\n', "ab" * 200)


class Controls(unittest.TestCase):
    def test_text(self):
        schema = 'r = text .abnf "a\\na = *\\"a\\" *\\"b\\"\\n"'
        validator = Validator(schema)
        for value, expected in (("aabbb", True), ("", True), ("c", False), ("ba", False)):
            self.assertEqual(validator.validate(cbor.encode(value)).valid, expected, value)
        self.assertFalse(validator.validate(cbor.encode(5)).valid)

    def test_bytes_are_utf8_for_abnf_and_raw_for_abnfb(self):
        text = Validator('r = bytes .abnf "a\\na = %xe4\\n"')   # one code point U+00E4
        self.assertTrue(text.validate(cbor.encode("ä".encode("utf-8"))).valid)
        self.assertFalse(text.validate(cbor.encode(b"\xe4")).valid)  # not UTF-8
        raw = Validator('r = bytes .abnfb "a\\na = %xe4\\n"')
        self.assertTrue(raw.validate(cbor.encode(b"\xe4")).valid)
        self.assertFalse(raw.validate(cbor.encode("ä".encode("utf-8"))).valid)

    def test_text_for_abnfb_is_utf8_bytes(self):
        validator = Validator('r = text .abnfb "a\\na = %xc3.a4\\n"')
        self.assertTrue(validator.validate(cbor.encode("ä")).valid)
        self.assertFalse(validator.validate(cbor.encode("a")).valid)

    def test_controller_can_be_computed(self):
        schema = ('full-date = "full-date" .cat rfc3339\n'
                  "rfc3339 = '\n  full-date = 4DIGIT \"-\" 2DIGIT \"-\" 2DIGIT\n  DIGIT = %x30-39\n'\n"
                  "r = text .abnf full-date")
        validator = Validator(schema, )
        self.assertTrue(validator.validate(cbor.encode("2024-02-29"), "r").valid)
        self.assertFalse(validator.validate(cbor.encode("2024-2-29"), "r").valid)

    def test_dedented_controller(self):
        schema = ("grammar = \"oid\" .det '\n    oid = 1*arc\n    arc = %x30-39\n'\n"
                  "r = text .abnf grammar")
        validator = Validator(schema)
        self.assertTrue(validator.validate(cbor.encode("123"), "r").valid)
        self.assertFalse(validator.validate(cbor.encode("12a"), "r").valid)

    def test_in_maps_arrays_and_tags(self):
        digits = 'd\\nd = 1*%x30-39\\n'
        validator = Validator(f'r = {{a: text .abnf "{digits}", b: [* text .abnf "{digits}"]}}\nt = #6.1004(text .abnf "{digits}")')
        self.assertTrue(validator.validate(cbor.encode({"a": "1", "b": ["2", "3"]}), "r").valid)
        self.assertFalse(validator.validate(cbor.encode({"a": "x", "b": []}), "r").valid)
        self.assertFalse(validator.validate(cbor.encode({"a": "1", "b": ["2", "y"]}), "r").valid)

    def test_target_still_applies(self):
        validator = Validator('r = (text .size 2) .abnf "a\\na = 1*%x61\\n"')
        self.assertTrue(validator.validate(cbor.encode("aa")).valid)
        self.assertFalse(validator.validate(cbor.encode("aaa")).valid)

    def test_hostile_input_does_not_stall(self):
        validator = Validator('r = text .abnf "a\\na = *(*\\"a\\" *\\"a\\")\\n"')
        self.assertFalse(validator.validate(cbor.encode("a" * 3000 + "b")).valid)

    def test_invalid_abnf_is_a_schema_error(self):
        for schema in ('r = text .abnf "a\\na = b\\n"', 'r = text .abnf "a"', 'r = text .abnf <x>'.replace("<x>", "1"),
                       'r = text .abnf ("a" / "b")', 'r = text .abnf tstr', 'r = text .abnf "a\\na = <prose>\\n"'):
            with self.assertRaises(SchemaError, msg=schema):
                Validator(schema)


if __name__ == "__main__":
    unittest.main()
