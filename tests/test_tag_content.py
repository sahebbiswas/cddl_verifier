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

    def test_other_tags_are_left_alone(self):
        self.assertIsNone(content_error(1, 'x'))
        self.assertIsNone(content_error(100, 5))
        self.assertEqual(CHECKED_TAGS, {0, 2, 3, 24, 32, 33, 34, 35, 36})

    def test_content_of_another_type_is_reported_without_the_schema(self):
        # #234: the type of the content is part of what the tag means
        for tag, wrong in [(0, 5), (36, 5), (32, b'x'), (2, 0), (2, 'not-bytes'), (3, ''),
                           (3, [1]), (24, 'text'), (24, 5), (2, None), (2, True), (0, {})]:
            self.assertIn(f"tag {tag} content must be", content_error(tag, wrong), (tag, wrong))
        self.assertIn('not an integer', content_error(2, 0))
        self.assertIn('not a text string', content_error(3, ''))
        self.assertIn('not a boolean', content_error(2, True))
        self.assertIn('not null', content_error(2, None))

    def test_any_byte_string_is_a_bignum(self):
        for content in (b'', b'\x00', b'\x01', b'\xff' * 20):
            self.assertIsNone(content_error(2, content))
            self.assertIsNone(content_error(3, content))

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


class TestContentTypeWhateverTheSchemaSays(unittest.TestCase):
    """#234: ``#6.2(any)`` does not accept ``2(0)``; the content type belongs to the tag."""

    SCHEMAS = ['r = #6.2(any)', 'r = #6.<uint>(any)', 'r = #6(any)', 'r = #6.<2 / 3>(any)',
               'r = #6.2(bstr)', 'r = #6.2(uint)', 'r = b\nb = #6.2(any)',
               'r = {a: #6.2(any)}', 'r = [* #6.2(any)]', 'r = #6.2(any) / nil']

    def wrap(self, schema, item):
        if schema.startswith('r = {'):
            return {'a': item}
        return [item] if schema.startswith('r = [') else item

    def test_not_bytes_is_rejected(self):
        for schema in self.SCHEMAS:
            for bad in (CBORTag(2, 0), CBORTag(2, 'not-bytes'), CBORTag(3, ''), CBORTag(2, [1])):
                value = self.wrap(schema, bad)
                if bad.tag == 3 and '#6.2' in schema:
                    continue  # the tag number is wrong as well
                self.assertFalse(ok(schema, value), (schema, bad))

    def test_bytes_is_accepted(self):
        for schema in ('r = #6.2(any)', 'r = #6.<uint>(any)', 'r = #6(any)', 'r = #6.2(bstr)',
                       'r = {a: #6.2(any)}', 'r = [* #6.2(any)]'):
            self.assertTrue(ok(schema, self.wrap(schema, CBORTag(2, b'\x01'))), schema)
            self.assertTrue(ok(schema, self.wrap(schema, CBORTag(2, b''))), schema)
        self.assertTrue(ok('r = #6.3(any)', CBORTag(3, b'\x01')))

    def test_message(self):
        result = validate('r = #6.2(any)', bytes.fromhex('c200'), root_type='r')
        self.assertFalse(result.valid)
        self.assertIn('tag 2 content must be a byte string, not an integer', result.errors[0])
        result = validate('r = #6(any)', bytes.fromhex('c360'), root_type='r')
        self.assertIn('tag 3 content must be a byte string, not a text string', result.errors[0])

    def test_registered_tags_with_the_wrong_content_type(self):
        for schema, bad in [('r = #6.0(any)', CBORTag(0, 5)), ('r = #6.24(any)', CBORTag(24, 5)),
                            ('r = #6.32(any)', CBORTag(32, 5)), ('r = #6.36(any)', CBORTag(36, b'x')),
                            ('r = #6.<uint>(any)', CBORTag(33, 5))]:
            self.assertFalse(ok(schema, bad), schema)

    def test_unregistered_tags_take_any_content(self):
        self.assertTrue(ok('r = #6.1(any)', CBORTag(1, 5)))
        self.assertTrue(ok('r = #6.100(any)', CBORTag(100, 'x')))
        self.assertTrue(ok('r = #6(any)', CBORTag(100, 5)))

    def test_big_bignums_are_seen_as_the_tag(self):
        big, negative = 2 ** 64, -1 - 2 ** 64
        for schema in ('r = #6.2(bstr)', 'r = #6.2(any)', 'r = #6.<2 / 3>(bstr)', 'r = b\nb = #6.2(bstr)',
                       'r = {a: #6.2(bstr)}', 'r = [* #6.2(bstr)]'):
            self.assertTrue(ok(schema, self.wrap(schema, big)), schema)
        self.assertTrue(ok('r = #6.3(bstr)', negative))
        self.assertFalse(ok('r = #6.3(bstr)', big))       # positive: tag 2
        self.assertFalse(ok('r = #6.2(uint)', big))       # the content is a byte string
        self.assertTrue(ok('r = biguint', big))
        self.assertTrue(ok('r = bigint', negative))

    def test_decoder_still_reads_the_bytes(self):
        for hex_ in ('c200', 'c360'):
            self.assertIsInstance(cbor.decode(bytes.fromhex(hex_)), CBORTag)
            self.assertIsInstance(cbor.decode(bytes.fromhex(hex_), require_deterministic=True), CBORTag)


if __name__ == '__main__':
    unittest.main()
