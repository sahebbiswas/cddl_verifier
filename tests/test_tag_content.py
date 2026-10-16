#!/usr/bin/env python3
"""
The content of the prelude tag types (#169).

``tdate``, ``uri``, ``b64url``, ``b64legacy``, ``regexp`` and ``encoded-cbor``
check what their content means, not only that it has the right type. Data is
passed as Python values: a tag is the tuple ``(number, content)``.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import cbor, validate
from cddl_verifier._cddl.tagcontent import CHECKED_TAGS, content_error
from cddl_verifier.cbor import CBORTag


def ok(schema, value, root='r'):
    return validate(schema, cbor.encode(value), root_type=root).valid


class TestContentError(unittest.TestCase):
    """``content_error`` for each tag: what is accepted and what is not."""

    CASES = {
        0: (['2020-01-01T00:00:00Z', '2020-02-29T23:59:59.123+05:30', '1999-12-31T23:59:60Z',
             '2020-01-01t00:00:00z', '0001-01-01T00:00:00-23:59',
             '1990-12-31T15:59:60-08:00', '2016-12-31T23:59:60Z', '2015-06-30T23:59:60Z',
             '9999-12-31T23:59:60Z', '9999-12-31T22:59:60-01:00'],
            ['not a date', '', '2021-02-29T00:00:00Z', '2020-13-01T00:00:00Z',
             '2020-00-10T00:00:00Z', '2020-01-32T00:00:00Z', '2020-01-01T24:00:00Z',
             '2020-01-01T00:60:00Z', '2020-01-01T00:00:61Z', '2020-01-01 00:00:00Z',
             '2020-01-01T00:00:00', '2020-01-01T00:00:00+24:00', '2020-01-01T00:00:00+05:60',
             '2020-1-1T00:00:00Z', '1900-02-29T00:00:00Z',
             '2020-01-01T12:00:60Z',       # second 60 only ends a UTC month
             '2020-06-29T23:59:60Z', '2020-06-30T23:59:60+01:00', '2020-07-31T23:59:60Z',
             '9999-12-31T23:59:60-01:00', '0001-01-01T00:59:60+01:00']),
        32: (['http://example.com', '', '/a/b?q=1#frag', 'mailto:a@b.example', 'a%20b',
              'http://u:p@host.example:8080/p?x=y#z', 'http://[::1]:80/x', './a:b', 'a:b:c',
              'urn:isbn:0451450523', '?q', '#f', 'file:///etc/hosts', 'http://[v1.x]/'],
             ['::not uri', 'a b', '%zz', '%4', '1a:b', 'http://h/a#b#c', 'café',
              'http://h/[x]', 'http://[::g]/', 'http://a:b/', 'http://[::1', '<a>',
              'http://[::1]x/', 'http://u@v@h/', '\x00', 'a\nb', ':a']),
        33: (['', 'YQ', 'YWJj', 'YWJjZA', 'a-_b', 'YWI'],
             ['YQ==', 'a', 'YWJjZ', 'a+b/', 'a b', 'Y\nQ',
              'YR', 'YWJ', 'YWJjZB']),   # the unused bits of the last character are not 0
        34: (['', 'YQ==', 'YWI=', 'YWJj', 'a+b/'],
             ['YQ', 'YQ=', 'YQ===', 'a-_b', '=YQ=', 'YWJj\n',
              'YR==', 'YWJ=', 'YWK=']),
        35: (['a+', '[a-z]*', '(?<n>a)\\k<n>', '(?P<n>a)(?P=n)', '^\\d{2,3}$'],
             ['(', '[a', 'a**', '(?<n>a', '*a', '\\']),
    }

    def test_text_tags(self):
        for tag, (good, bad) in self.CASES.items():
            for text in good:
                self.assertIsNone(content_error(tag, text), (tag, text))
            for text in bad:
                self.assertIsNotNone(content_error(tag, text), (tag, text))

    def test_encoded_cbor(self):
        for data in (b'\x01', b'\x62ab', b'\x82\x01\x02', cbor.encode({'a': [1, 2]})):
            self.assertIsNone(content_error(24, data), data)
        for data in (b'', b'\xff\xff', b'\x01\x02', b'\x62a', b'\x1c'):
            self.assertIsNotNone(content_error(24, data), data)

    def test_other_tags_and_other_types_are_left_alone(self):
        self.assertIsNone(content_error(36, 5))          # not text: the type check reports that
        self.assertIsNone(content_error(1, 'x'))
        self.assertIsNone(content_error(0, 5))          # the type check reports that
        self.assertIsNone(content_error(24, 'text'))
        self.assertEqual(CHECKED_TAGS, {0, 24, 32, 33, 34, 35, 36})

    def test_message_names_the_tag_and_the_problem(self):
        message = content_error(0, '2021-02-29T00:00:00Z')
        self.assertIn('tag 0', message)
        self.assertIn('day 29 does not exist in 2021-02', message)


class TestInTheValidator(unittest.TestCase):
    """The check runs wherever a tag is matched: root, field, element, rule."""

    GOOD = {'tdate': CBORTag(0, '2020-01-01T00:00:00Z'), 'uri': CBORTag(32, 'http://a/b'),
            'b64url': CBORTag(33, 'YQ'), 'b64legacy': CBORTag(34, 'YQ=='), 'regexp': CBORTag(35, 'a+'),
            'encoded-cbor': CBORTag(24, b'\x01')}
    BAD = {'tdate': CBORTag(0, 'not a date'), 'uri': CBORTag(32, '::not uri'),
           'b64url': CBORTag(33, 'YQ=='), 'b64legacy': CBORTag(34, 'YQ'), 'regexp': CBORTag(35, '('),
           'encoded-cbor': CBORTag(24, b'\xff\xff')}

    def test_root(self):
        for name in self.GOOD:
            self.assertTrue(ok(f'r = {name}', self.GOOD[name]), name)
            self.assertFalse(ok(f'r = {name}', self.BAD[name]), name)

    def test_field_element_and_choice(self):
        for name in self.GOOD:
            self.assertTrue(ok(f'r = {{a: {name}}}', {'a': self.GOOD[name]}), name)
            self.assertFalse(ok(f'r = {{a: {name}}}', {'a': self.BAD[name]}), name)
            self.assertTrue(ok(f'r = [* {name}]', [self.GOOD[name], self.GOOD[name]]), name)
            self.assertFalse(ok(f'r = [* {name}]', [self.GOOD[name], self.BAD[name]]), name)
            self.assertTrue(ok(f'r = {{a: {name} / nil}}', {'a': self.GOOD[name]}), name)
            self.assertFalse(ok(f'r = {{a: {name} / nil}}', {'a': self.BAD[name]}), name)
            self.assertFalse(ok(f'r = {{? a: {name}}}', {'a': self.BAD[name]}), name)

    def test_written_as_a_tag_and_through_a_rule(self):
        self.assertFalse(ok('r = #6.0(tstr)', CBORTag(0, 'not a date')))
        self.assertTrue(ok('r = #6.0(tstr)', CBORTag(0, '2020-01-01T00:00:00Z')))
        self.assertFalse(ok('when = tdate\nr = {a: when}', {'a': CBORTag(0, 'nope')}))
        self.assertFalse(ok('r = #6.32(tstr)', CBORTag(32, 'a b')))

    def test_nested_tags(self):
        self.assertFalse(ok('r = #6.100(tdate)', CBORTag(100, CBORTag(0, 'bad'))))
        self.assertTrue(ok('r = #6.100(tdate)', CBORTag(100, CBORTag(0, '2020-01-01T00:00:00Z'))))

    def test_message_and_diagnostic_path(self):
        result = validate('r = {a: tdate}', cbor.encode({'a': CBORTag(0, 'bad')}), root_type='r')
        self.assertFalse(result.valid)
        self.assertIn("tag 0 content 'bad' is not valid", result.errors[0])
        self.assertEqual(result.diagnostics[0].path, ('a',))

    def test_wrong_type_and_wrong_tag_still_reported_as_before(self):
        result = validate('r = tdate', cbor.encode(CBORTag(0, 5)), root_type='r')
        self.assertFalse(result.valid)
        self.assertNotIn('is not valid', result.errors[0])
        self.assertFalse(ok('r = tdate', CBORTag(1, '2020-01-01T00:00:00Z')))

    def test_encoded_cbor_inside_is_still_checked_by_its_type(self):
        self.assertTrue(ok('r = #6.24(bstr .cbor uint)', CBORTag(24, b'\x01')))
        self.assertFalse(ok('r = #6.24(bstr .cbor uint)', CBORTag(24, b'\x61a')))
        self.assertFalse(ok('r = #6.24(bstr .cbor uint)', CBORTag(24, b'\xff')))


if __name__ == '__main__':
    unittest.main()
