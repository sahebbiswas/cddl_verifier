#!/usr/bin/env python3
"""
CBOR sequences (RFC 8742) and ``.cborseq`` (#77).

``decode_sequence`` is the API that reads a sequence; ``decode`` stays strict
and still rejects trailing data. ``bstr .cborseq T`` holds a sequence whose
items, taken as an array, match ``T``.
"""

import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, validate
from cddl_verifier import cbor
from cddl_verifier._cbor import CBOR


def seq(*items):
    """The CBOR sequence of *items*."""
    return b''.join(cbor.encode(item) for item in items)


def ok(schema, data, root='r'):
    return validate(schema, cbor.encode(data), root_type=root).valid


class TestDecodeSequence(unittest.TestCase):

    def test_empty_input_is_the_empty_sequence(self):
        self.assertEqual(cbor.decode_sequence(b''), [])

    def test_items_come_back_in_order(self):
        self.assertEqual(cbor.decode_sequence(seq(1, 'ab', [1, 2], {'k': None})),
                         [1, 'ab', [1, 2], {'k': None}])
        self.assertEqual(cbor.decode_sequence(b'\x05'), [5])

    def test_one_item_is_a_sequence_of_one(self):
        self.assertEqual(cbor.decode_sequence(cbor.encode([1, 2])), [[1, 2]])

    def test_accepts_bytes_like_input(self):
        self.assertEqual(cbor.decode_sequence(bytearray(b'\x01\x02')), [1, 2])
        self.assertEqual(cbor.decode_sequence(memoryview(b'\x01\x02')), [1, 2])
        with self.assertRaises(TypeError):
            cbor.decode_sequence('text')

    def test_truncated_item_names_the_item_and_offset(self):
        with self.assertRaises(cbor.CBORTruncatedError) as caught:
            cbor.decode_sequence(b'\x01\x62a')   # item 1 needs two bytes of text
        self.assertEqual(caught.exception.offset, 2)
        self.assertIn('item 1 of the sequence', str(caught.exception))

    def test_malformed_item_names_the_item_and_offset(self):
        for data, item, offset in [(b'\x1c', 0, 0), (b'\x01\x02\xff', 2, 2),
                                   (b'\x01\x1c', 1, 1)]:
            with self.assertRaises(cbor.CBORDecodeError) as caught:
                cbor.decode_sequence(data)
            self.assertEqual(caught.exception.offset, offset, data)
            self.assertIn(f'item {item} of the sequence', str(caught.exception))

    def test_a_truncated_array_is_truncated(self):
        with self.assertRaises(cbor.CBORTruncatedError):
            cbor.decode_sequence(b'\x01\x82\x01')

    def test_decode_still_rejects_trailing_data(self):
        with self.assertRaises(cbor.CBORTrailingDataError):
            cbor.decode(b'\x01\x02')
        with self.assertRaises(cbor.CBORTrailingDataError):
            CBOR.loads(b'\x01\x02')
        with self.assertRaises(cbor.CBORTruncatedError):
            cbor.decode(b'')

    def test_keep_encoding_is_passed_on(self):
        (item,) = cbor.decode_sequence(b'\x18\x05', keep_encoding=True)  # 5 in two bytes
        self.assertEqual(item, 5)
        self.assertEqual(cbor.head_info(item), 24)


class TestCborseqControl(unittest.TestCase):

    def test_items_match_the_array(self):
        s = 'r = bstr .cborseq [* uint]'
        self.assertTrue(ok(s, seq(1, 2, 3)))
        self.assertFalse(ok(s, seq(1, 'x')))

    def test_empty_sequence(self):
        self.assertTrue(ok('r = bstr .cborseq [* uint]', b''))
        self.assertFalse(ok('r = bstr .cborseq [+ uint]', b''))

    def test_positional_array(self):
        s = 'r = bstr .cborseq [uint, tstr]'
        self.assertTrue(ok(s, seq(1, 'x')))
        self.assertFalse(ok(s, seq(1)))
        self.assertFalse(ok(s, seq('x', 1)))
        self.assertFalse(ok(s, seq(1, 'x', 2)))

    def test_does_not_decode(self):
        s = 'r = bstr .cborseq [* uint]'
        self.assertFalse(ok(s, b'\x01\x62a'))   # truncated second item
        self.assertFalse(ok(s, b'\x01\xff'))
        result = validate(s, cbor.encode(b'\x01\x62a'), root_type='r')
        self.assertIn('does not decode', result.errors[0])
        self.assertIn('item 1 of the sequence', result.errors[0])

    def test_the_target_must_be_bytes(self):
        self.assertFalse(ok('r = bstr .cborseq [* uint]', 'text'))
        self.assertFalse(ok('r = bstr .cborseq [* uint]', [1, 2]))

    def test_is_not_a_single_item(self):
        # .cbor takes one item; .cborseq takes any number
        self.assertFalse(ok('r = bstr .cbor uint', seq(1, 2)))
        self.assertTrue(ok('r = bstr .cborseq [* uint]', seq(1, 2)))

    def test_through_a_rule_and_the_bytes_own_controls(self):
        self.assertTrue(ok('s = [* tstr]\nr = bstr .cborseq s', seq('a', 'b')))
        self.assertFalse(ok('s = [* tstr]\nr = bstr .cborseq s', seq('a', 1)))
        self.assertTrue(ok('r = bstr .cborseq [* uint] .size 2', seq(1, 2)))
        self.assertFalse(ok('r = bstr .cborseq [* uint] .size 2', seq(1, 2, 3)))

    def test_in_field_element_and_named_rule(self):
        s = 'r = {a: bstr .cborseq [* uint]}'
        self.assertTrue(ok(s, {'a': seq(1, 2)}))
        self.assertFalse(ok(s, {'a': seq(1, 'x')}))
        self.assertFalse(ok(s, {'a': 'x'}))
        s = 'r = [* bstr .cborseq [* uint]]'
        self.assertTrue(ok(s, [seq(1), seq(2, 3), b'']))
        self.assertFalse(ok(s, [seq(1), seq('x')]))
        s = 'r = {a: s}\ns = bstr .cborseq [* tstr]'
        self.assertTrue(ok(s, {'a': seq('x', 'y')}))
        self.assertFalse(ok(s, {'a': seq('x', 1)}))

    def test_error_is_reported_at_the_byte_string(self):
        s = 'r = {a: bstr .cborseq [* uint]}'
        result = validate(s, cbor.encode({'a': seq(1, 'x')}), root_type='r')
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].path, ('a',))

    def test_nested_structures_in_the_items(self):
        s = 'r = bstr .cborseq [* {k: uint}]'
        self.assertTrue(ok(s, seq({'k': 1}, {'k': 2})))
        self.assertFalse(ok(s, seq({'k': 1}, {'k': 'x'})))

    def test_every_embedded_control_applies(self):
        two = 'r = bstr .cborseq [* uint] .cborseq [* tstr]'
        self.assertFalse(ok(two, seq(1)))        # the second control wants text
        self.assertFalse(ok(two, seq('x')))      # the first wants uints
        self.assertTrue(ok(two, b''))            # both accept the empty sequence
        both = 'r = bstr .cbor uint .cborseq [* uint]'
        self.assertTrue(ok(both, seq(1)))
        self.assertFalse(ok(both, seq(1, 2)))    # .cbor takes one item
        field = 'r = {a: bstr .cborseq [* uint] .cborseq [* tstr]}'
        self.assertFalse(ok(field, {'a': seq(1)}))
        self.assertTrue(ok(field, {'a': b''}))

    def test_parenthesized_array_argument(self):
        for control, data in (('.cborseq', seq('x')), ('.cbor', cbor.encode(['x']))):
            for arg in ('[* uint]', '([* uint])', '(([* uint]))'):
                self.assertFalse(ok(f'r = bstr {control} {arg}', data), (control, arg))
        self.assertTrue(ok('r = bstr .cborseq ([* tstr])', seq('x')))
        self.assertTrue(ok('r = bstr .cbor ([* tstr])', cbor.encode(['x'])))

    def test_argument_must_be_an_array(self):
        for arg in ('uint', 'tstr', '{a: int}'):
            with self.assertRaises(SchemaError, msg=arg):
                validate(f'r = bstr .cborseq {arg}', b'\x40', root_type='r')

    def test_target_must_be_bstr(self):
        with self.assertRaises(SchemaError):
            validate('r = tstr .cborseq [* uint]', b'\x60', root_type='r')


if __name__ == '__main__':
    unittest.main()
