#!/usr/bin/env python3
"""
``.bits`` and ``.regexp`` (RFC 8610 section 3.8, #182).

``.bits`` allows only the bit positions its argument names. ``.regexp`` reads
its pattern as an XML Schema regular expression, which differs from Python's
``re``; each case below states the XSD meaning.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, validate
from cddl_verifier._cddl import xsd_regex


def ok(schema, data, root='r'):
    return validate(schema, data, root_type=root).valid


def bstr(raw):
    """CBOR for the byte string *raw* (up to 23 bytes)."""
    return bytes([0x40 + len(raw)]) + raw


FLAGS = 'f = &(a: 0, b: 2)\n'


class TestBitsUint(unittest.TestCase):

    def test_only_named_positions(self):
        s = 'r = uint .bits f\n' + FLAGS
        for good in (0, 1, 4, 5):
            self.assertTrue(ok(s, good), good)
        for bad in (2, 3, 6, 8):
            self.assertFalse(ok(s, bad), bad)

    def test_message_names_the_bit(self):
        result = validate('r = uint .bits f\n' + FLAGS, 10, root_type='r')
        self.assertIn('bit 1 is set', result.errors[0])
        self.assertIn('0, 2', result.errors[0])

    def test_argument_forms(self):
        for arg, extra, good, bad in [
            ('3', '', [0, 8], [1, 16]),
            ('(0..3)', '', [0, 15], [16]),
            ('(0...3)', '', [7], [8]),
            ('(1 / 5)', '', [0, 2, 32, 34], [1]),
            ('g', 'g = 0 / 4..5\n', [1, 16, 48], [2]),
            ('&h', 'h = (x: 1, y: 3)\n', [2, 8, 10], [1]),
            ('&(1, 2)', '', [6], [1]),
        ]:
            s = f'r = uint .bits {arg}\n{extra}'
            for v in good:
                self.assertTrue(ok(s, v), (arg, v))
            for v in bad:
                self.assertFalse(ok(s, v), (arg, v))

    def test_in_field_element_and_target_type(self):
        s = 'r = {a: uint .bits f, b: [* uint .bits f]}\n' + FLAGS
        self.assertTrue(ok(s, {'a': 5, 'b': [1, 4]}))
        self.assertFalse(ok(s, {'a': 2, 'b': []}))
        self.assertFalse(ok(s, {'a': 1, 'b': [2]}))
        self.assertFalse(ok('r = uint .bits f\n' + FLAGS, 'x'))
        self.assertFalse(ok('r = uint .bits f\n' + FLAGS, -1))


class TestBitsBstr(unittest.TestCase):

    def test_first_byte_is_least_significant(self):
        s = 'r = bstr .bits f\n' + FLAGS
        self.assertTrue(ok(s, bstr(b'\x05')))
        self.assertFalse(ok(s, bstr(b'\x02')))
        s = 'r = bstr .bits 8\n'
        self.assertTrue(ok(s, bstr(b'\x00\x01')))   # bit 8 is the lowest bit of byte 1
        self.assertFalse(ok(s, bstr(b'\x01')))
        self.assertFalse(ok(s, bstr(b'\x00\x02')))

    def test_field(self):
        s = 'r = {k: bstr .bits (0..3)}'
        self.assertTrue(ok(s, {'k': b'\x0f'}))
        self.assertFalse(ok(s, {'k': b'\x10'}))


class TestBitsSchemaErrors(unittest.TestCase):

    def test_argument_must_be_positions(self):
        for arg in ('"x"', 'h\'01\'', '{a: int}', '[* int]', '-1', '1.5', '(-2..3)', '70000'):
            with self.assertRaises(SchemaError, msg=arg):
                validate(f'r = uint .bits {arg}', 1, root_type='r')

    def test_target(self):
        with self.assertRaises(SchemaError):
            validate('r = tstr .bits 1', 'a', root_type='r')


class TestGenericArguments(unittest.TestCase):

    def test_bits_and_regexp_arguments_are_checked_after_substitution(self):
        self.assertTrue(ok('g<T> = uint .bits T\nr = g<3>', 8))
        self.assertFalse(ok('g<T> = uint .bits T\nr = g<3>', 1))
        self.assertTrue(ok('g<T> = tstr .regexp T\nr = g<"a+">', 'aa'))
        self.assertFalse(ok('g<T> = tstr .regexp T\nr = g<"a+">', 'ab'))
        for schema in ('g<T> = uint .bits T\nr = g<"x">',
                       'g<T> = tstr .regexp T\nr = g<"a+?">'):
            with self.assertRaises(SchemaError, msg=schema):
                validate(schema, 1, root_type='r')


class TestXsdRegexpMeaning(unittest.TestCase):
    """XSD says one thing, Python's ``re`` another."""

    def match(self, pattern, text):
        schema = 'r = tstr .regexp "' + pattern.replace('\\', '\\\\') + '"'
        return ok(schema, text)

    def test_whole_text_and_no_anchors(self):
        self.assertTrue(self.match('a+', 'aaa'))
        self.assertFalse(self.match('a+', 'aaab'))     # the match is of the whole text
        self.assertFalse(self.match('a+', 'baaa'))
        self.assertTrue(self.match('^a$', '^a$'))      # ^ and $ are ordinary characters
        self.assertFalse(self.match('^a$', 'a'))

    def test_digit_is_any_unicode_decimal(self):
        self.assertTrue(self.match('\\d+', '123'))
        self.assertTrue(self.match('\\d+', '١٢'))  # Arabic-Indic digits are Nd
        self.assertFalse(self.match('\\d+', '1a'))
        self.assertTrue(self.match('\\D+', 'ab'))

    def test_word_excludes_punctuation_and_separators(self):
        self.assertTrue(self.match('\\w+', 'ab1'))
        self.assertFalse(self.match('\\w+', 'a_b'))   # '_' is punctuation (Pc)
        self.assertFalse(self.match('\\w+', 'a-b'))
        self.assertFalse(self.match('\\w+', 'a b'))
        self.assertTrue(self.match('\\W', ' '))

    def test_space_is_only_four_characters(self):
        for ch in (' ', '\t', '\n', '\r'):
            self.assertTrue(self.match('\\s', ch), repr(ch))
        for ch in ('\x0b', '\x0c', ' '):
            self.assertFalse(self.match('\\s', ch), repr(ch))

    def test_dot_excludes_only_line_feed_and_carriage_return(self):
        self.assertTrue(self.match('a.c', 'a c'))
        self.assertFalse(self.match('a.c', 'a\nc'))
        self.assertFalse(self.match('a.c', 'a\rc'))

    def test_categories_and_blocks(self):
        self.assertTrue(self.match('\\p{Lu}\\p{Ll}+', 'Hello'))
        self.assertFalse(self.match('\\p{Lu}\\p{Ll}+', 'hello'))
        self.assertTrue(self.match('\\P{L}+', '123'))
        self.assertTrue(self.match('\\p{IsGreek}+', 'αβ'))
        self.assertFalse(self.match('\\p{IsBasicLatin}', 'α'))
        self.assertTrue(self.match('[\\p{L}\\p{N}]+', 'ab12'))

    def test_name_characters(self):
        self.assertTrue(self.match('\\i\\c*', 'a-b.c'))
        self.assertFalse(self.match('\\i\\c*', '1a'))
        self.assertTrue(self.match('\\I', '1'))
        self.assertFalse(self.match('\\C', 'a'))

    def test_class_subtraction(self):
        self.assertTrue(self.match('[a-z-[aeiou]]+', 'bcd'))
        self.assertFalse(self.match('[a-z-[aeiou]]+', 'bad'))
        self.assertFalse(self.match('[\\d-[5]]', '5'))
        self.assertFalse(self.match('[^a-c-[x]]', 'x'))  # [^a-c] without x
        self.assertTrue(self.match('[^a-c-[x]]', 'y'))
        self.assertFalse(self.match('[^a-c-[x]]', 'b'))

    def test_ordinary_constructs(self):
        self.assertTrue(self.match('(ab|cd){2}', 'abcd'))
        self.assertFalse(self.match('a{2,3}', 'aaaa'))
        self.assertTrue(self.match('a|', ''))
        self.assertTrue(self.match('[-a]+', '-a-'))
        self.assertTrue(self.match('\\-\\.\\|', '-.|'))


class TestXsdRegexpRejected(unittest.TestCase):
    """What XSD does not have is a schema error that names it."""

    def test_each_is_a_schema_error(self):
        for pattern, naming in [
            ('a+?', 'quantifier'), ('a**', 'quantifier'), ('(?:a)', "'(?'"),
            ('(?=a)', "'(?'"), ('\\\\bx', 'escape'), ('(a)\\\\1', 'back reference'),
            ('a{,3}', 'quantifier'), ('[b-a]', 'backwards'), ('\\\\p{Foo}', 'Foo'),
            ('\\\\p{IsKlingon}', 'IsKlingon'), ('[a', "']'"), ('(a', "')'"),
            ('*a', 'nothing to repeat'), ('\\\\u0041', 'escape'),
        ]:
            with self.assertRaises(SchemaError, msg=pattern) as caught:
                validate(f'r = tstr .regexp "{pattern}"', 'a', root_type='r')
            self.assertIn(naming, str(caught.exception), pattern)

    def test_rule_argument_is_checked_too(self):
        with self.assertRaises(SchemaError):
            validate('p = "a+?"\nr = tstr .regexp p', 'a', root_type='r')

    def test_compile_is_cached_and_total(self):
        self.assertEqual(xsd_regex.compile_pattern('ab'), xsd_regex.compile_pattern('ab'))
        with self.assertRaises(xsd_regex.XsdRegexError):
            xsd_regex.compile_pattern('(' * 100 + ')' * 100)


class LinearTimeTests(unittest.TestCase):
    """#185: a pattern and a text built to make a backtracking engine stall return quickly."""

    HOSTILE = ['(a+)+b', '(a|a)*b', '(a*)*b', '(a|aa)+b', '(a?){30}a{30}', '((a|b)*)*c', '(.*)*x']

    def test_pathological_patterns_return_a_verdict_quickly(self):
        import time
        text = 'a' * 50000
        for pattern in self.HOSTILE:
            started = time.monotonic()
            self.assertFalse(xsd_regex.fullmatch(pattern, text), pattern)
            self.assertLess(time.monotonic() - started, 5, pattern)

    def test_verdicts_are_still_right_on_the_same_patterns(self):
        self.assertTrue(xsd_regex.fullmatch('(a+)+b', 'a' * 50000 + 'b'))
        self.assertTrue(xsd_regex.fullmatch('(a|a)*b', 'aab'))
        self.assertTrue(xsd_regex.fullmatch('(a?){3}a{3}', 'aaa'))
        self.assertFalse(xsd_regex.fullmatch('(a?){3}a{3}', 'aa'))
        self.assertTrue(xsd_regex.fullmatch('(a*)*', ''))
        self.assertTrue(xsd_regex.fullmatch('(a|)*b', 'aab'))

    def test_validation_of_a_hostile_text_is_quick_and_fails(self):
        import time
        started = time.monotonic()
        self.assertFalse(validate('r = tstr .regexp "(a+)+b"', 'a' * 50000, root_type='r'))
        self.assertLess(time.monotonic() - started, 5)

    def test_a_pattern_too_big_to_build_is_a_schema_error(self):
        with self.assertRaisesRegex(xsd_regex.XsdRegexError, 'states'):
            xsd_regex.compile_pattern('((((a{1000}){1000}){1000}){1000})')
        with self.assertRaises(SchemaError):
            validate('r = tstr .regexp "((a{1000}){1000}){1000}"', 'a', root_type='r')

    def test_a_match_over_the_step_budget_is_refused_not_guessed(self):
        pattern = '(' + '|'.join(['a'] * 400) + ')*'
        original = xsd_regex.MATCH_BUDGET
        xsd_regex.MATCH_BUDGET = 10_000
        try:
            with self.assertRaises(xsd_regex.XsdRegexLimit):
                xsd_regex.fullmatch(pattern, 'a' * 1000)
            result = validate('r = tstr .regexp "%s"' % pattern, 'a' * 1000, root_type='r')
        finally:
            xsd_regex.MATCH_BUDGET = original
        self.assertFalse(result)

    def test_repeated_classes_share_their_range_lists(self):
        matcher = xsd_regex.compile_pattern('(\\W{200}){5}')
        lists = {id(cls[0]) for cls in matcher.nfa.classes if cls is not None}
        self.assertEqual(len(lists), 1)
        self.assertEqual(len([c for c in matcher.nfa.classes if c is not None]), 1000)

    def test_empty_alternatives_are_one_destination_and_work_is_charged(self):
        import time
        pattern = '(' + '|' * 5000 + 'a)*b'
        matcher = xsd_regex.compile_pattern(pattern)
        for targets in matcher.nfa.splits:
            if targets is not None:
                self.assertEqual(len(targets), len(set(targets)))
        started = time.monotonic()
        self.assertFalse(xsd_regex.fullmatch(pattern, 'a' * 50000))
        self.assertLess(time.monotonic() - started, 5)


if __name__ == '__main__':
    unittest.main()
