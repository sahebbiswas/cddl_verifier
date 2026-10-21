"""``*`` width and precision in ``.printf`` (RFC 9741 section 2.3, C 7.23.6.1, #241).

C takes a ``*`` from an extra ``int`` argument that comes before the value, so the
array of a ``.printf`` has one more data item for each ``*``, and the text does not say
what it was: the width is read back from the text.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import SchemaError, Validator, cbor
from cddl_verifier._cddl import conversions


def accepts(schema, text):
    return Validator(schema).validate(cbor.encode(text)).valid


class StarFormats(unittest.TestCase):
    def test_parse(self):
        specs = [p for p in conversions.parse_format("%*d|%.*f|%*.*s|%-*d|%5d") if not isinstance(p, str)]
        self.assertEqual([(s.star_width, s.star_precision) for s in specs],
                         [(True, False), (False, True), (True, True), (True, False), (False, False)])
        self.assertEqual([s.arity for s in specs], [2, 2, 3, 2, 1])
        self.assertEqual(conversions.item_count(conversions.parse_format("%*d%%%.*f")), 4)
        self.assertEqual(specs[3].flags, "-")
        self.assertEqual(specs[4].width, 5)

    def test_star_is_not_allowed_on_percent(self):
        with self.assertRaises(conversions.PrintfError):
            conversions.parse_format("%*%")

    def test_with_fills_in_the_star_values(self):
        spec = conversions.parse_format("%*.*d")[0]
        self.assertEqual(conversions.format_int(spec.with_(5, 3), 7), "  007")
        self.assertEqual(conversions.format_int(spec.with_(-5, 3), 7), "007  ")
        self.assertEqual(conversions.format_int(spec.with_(0, -1), 7), "7")  # a negative precision is none


class StarWidth(unittest.TestCase):
    def test_width_is_read_from_the_padding(self):
        schema = 'r = text .printf ["%*d", 5, uint]'
        self.assertTrue(accepts(schema, "   42"))
        self.assertFalse(accepts(schema, "42"))
        self.assertFalse(accepts(schema, "  42"))
        self.assertFalse(accepts(schema, "    42"))

    def test_negative_width_pads_on_the_right(self):
        self.assertTrue(accepts('r = text .printf ["%*d", -5, uint]', "42   "))
        self.assertFalse(accepts('r = text .printf ["%*d", -5, uint]', "   42"))
        self.assertTrue(accepts('r = text .printf ["%*d", nint, uint]', "42   "))
        self.assertFalse(accepts('r = text .printf ["%*d", nint, uint]', "   42"))

    def test_width_that_does_not_pad_is_any_width_up_to_the_length(self):
        for width in (0, 1, 2, -1, -2):
            self.assertTrue(accepts(f'r = text .printf ["%*d", {width}, uint]', "42"), width)
        for width in (3, 4, -3):
            self.assertFalse(accepts(f'r = text .printf ["%*d", {width}, uint]', "42"), width)

    def test_the_width_item_is_a_type(self):
        self.assertTrue(accepts('r = text .printf ["%*d", 3..8, uint]', "   42"))
        self.assertFalse(accepts('r = text .printf ["%*d", 6..8, uint]', "   42"))
        self.assertTrue(accepts('r = text .printf ["%*d", uint .le 3, uint]', "42"))
        self.assertFalse(accepts('r = text .printf ["%*d", tstr, uint]', "42"))
        self.assertFalse(accepts('r = text .printf ["%*d", uint, tstr]', "42"))

    def test_zero_flag_and_other_conversions(self):
        self.assertTrue(accepts('r = text .printf ["%0*d", 4, uint]', "0042"))
        self.assertFalse(accepts('r = text .printf ["%0*d", 4, uint]', "  42"))
        self.assertTrue(accepts('r = text .printf ["%*x", 4, uint]', "  ff"))
        self.assertTrue(accepts('r = text .printf ["%*s", 6, tstr .size 2]', "    ab"))
        self.assertTrue(accepts('r = text .printf ["%-*s|", 6, tstr .size 2]', "ab    |"))
        self.assertTrue(accepts('r = text .printf ["%*c", 3, uint]', "  A"))
        self.assertTrue(accepts('r = text .printf ["%*.1f", 6, float]', "   3.1"))

    def test_padded_string_reads_with_or_without_the_padding(self):
        # "  ab" is the string "  ab", or "ab" padded to 4 (or " ab" padded to 4)
        self.assertTrue(accepts('r = text .printf ["%*s", 4, tstr .size 2]', "  ab"))
        self.assertTrue(accepts('r = text .printf ["%*s", 4, tstr .size 4]', "  ab"))
        self.assertTrue(accepts('r = text .printf ["%5s", tstr .size 2]', "   ab"))       # no '*'
        self.assertTrue(accepts('r = text .printf ["%-5s|", tstr .size 2]', "ab   |"))
        self.assertFalse(accepts('r = text .printf ["%5s", tstr .size 2]', "ab"))

    def test_value_type_decides_among_widths(self):
        # "  7" is 7 at width 3 only
        self.assertTrue(accepts('r = text .printf ["%*d", uint, 7]', "  7"))
        self.assertFalse(accepts('r = text .printf ["%*d", uint, 8]', "  7"))


class StarPrecision(unittest.TestCase):
    def test_precision_is_read_from_the_digits(self):
        self.assertTrue(accepts('r = text .printf ["%.*f", 2, float]', "3.14"))
        self.assertFalse(accepts('r = text .printf ["%.*f", 2, float]', "3.1"))
        self.assertTrue(accepts('r = text .printf ["%.*f", 0..3, float]', "3.1"))
        self.assertFalse(accepts('r = text .printf ["%.*f", 3..9, float]', "3.1"))
        self.assertTrue(accepts('r = text .printf ["%.*d", 4, uint]', "0042"))
        self.assertFalse(accepts('r = text .printf ["%.*d", 5, uint]', "0042"))

    def test_string_precision_cuts_the_string(self):
        self.assertTrue(accepts('r = text .printf ["%.*s", 2, tstr]', "ab"))
        self.assertTrue(accepts('r = text .printf ["%.*s", 2147483647, tstr]', "ab"))  # beyond the text
        self.assertTrue(accepts('r = text .printf ["%.*s", uint, tstr]', "ab"))
        self.assertTrue(accepts('r = text .printf ["%.*s", 1..5, tstr]', "abc"))
        self.assertFalse(accepts('r = text .printf ["%.*s", 0..2, tstr]', "abc"))

    def test_negative_precision_is_no_precision(self):
        self.assertTrue(accepts('r = text .printf ["%.*f", -1, float]', "3.141593"))
        self.assertFalse(accepts('r = text .printf ["%.*f", -1, float]', "3.14"))

    def test_width_and_precision_together(self):
        schema = 'r = text .printf ["%*.*f", 8, 2, float]'
        self.assertTrue(accepts(schema, "    3.14"))
        self.assertFalse(accepts(schema, "3.14"))
        self.assertFalse(accepts(schema, "   3.14"))
        self.assertTrue(accepts('r = text .printf ["%-*.*f|", -8, 2, float]', "3.14    |"))
        self.assertTrue(accepts('r = text .printf ["%*.*d", 6, 3, uint]', "   007"))
        self.assertFalse(accepts('r = text .printf ["%*.*d", 6, 3, uint]', "     7"))


class Cost(unittest.TestCase):
    """The readings of a long text take time in proportion to its length, and never format the largest ``int``."""

    def test_the_largest_precision_is_never_formatted(self):
        import time
        for conv, text in (('d', '42'), ('x', '2a'), ('f', '1.5'), ('e', '1.5e+00'), ('a', '0x1.8p+0'), ('s', 'ab')):
            spec = conversions.Spec('', 0, None, conv, False, True)
            start = time.perf_counter()
            list(conversions.star_readings(spec, text))
            self.assertLess(time.perf_counter() - start, 0.5, conv)

    def test_the_largest_precision_still_writes_what_ignores_it(self):
        precisions = lambda conv, text, flags='': [p for p, _v, _w in conversions.star_readings(
            conversions.Spec(flags, 0, None, conv, False, True), text)]
        top = conversions._INT_MAX
        self.assertIn(top, precisions('s', 'ab'))                 # %.*s: no cut at any precision >= 2
        self.assertIn(top, precisions('g', '1.5'))                # %.*g strips trailing zeros
        self.assertIn(top, precisions('g', '100000000000000000000'))   # positional, not 1e+20
        self.assertNotIn(top, precisions('g', '1e+20'))
        self.assertNotIn(top, precisions('g', '1.50000', '#'))   # '#' keeps the zeros: that long
        self.assertIn(top, precisions('f', 'inf'))
        self.assertNotIn(top, precisions('f', '1.5'))             # would be that many digits
        self.assertNotIn(top, precisions('d', '42'))

    def test_a_long_padded_text_is_read_in_linear_time(self):
        import time
        text = ' ' * 20000 + 'ab'
        start = time.perf_counter()
        for width, flags in ((len(text), ''), (len(text), '-'), (5, '')):
            spec = conversions.Spec(flags, width, None, 's', False, False)
            list(conversions.read_all(spec, text if flags == '' else 'ab' + ' ' * 20000))
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_the_cuts_of_a_padded_string_are_the_same(self):
        read = lambda width, flags, text, precision=None: list(conversions.read_all(
            conversions.Spec(flags, width, precision, 's', False, False), text))
        self.assertEqual(read(4, '', '  ab'), ['  ab', ' ab', 'ab'])
        self.assertEqual(read(4, '-', 'ab  '), ['ab  ', 'ab ', 'ab'][:3])
        self.assertEqual(read(2, '', ' ab'), [' ab'])             # longer than the width: nothing was padded
        self.assertEqual(read(4, '', 'a  b'), ['a  b'])           # only a leading run of spaces can be padding
        self.assertEqual(read(4, '', '  ab', 3), [' ab', 'ab'])   # precision 3 cuts the 4-character reading
        self.assertEqual(read(4, '', '  ab', 1), [])              # precision 1 cuts every reading to one character


class Several(unittest.TestCase):
    def test_items_are_taken_in_order_per_conversion(self):
        schema = 'r = text .printf ["%*d|%d|%.*f", 4, uint, int, 1, float]'
        self.assertTrue(accepts(schema, "  42|-3|2.5"))
        self.assertFalse(accepts(schema, " 42|-3|2.5"))
        self.assertFalse(accepts(schema, "  42|-3|2.50"))

    def test_computed_types_and_generics(self):
        schema = 'r = pad<4>\npad<W> = text .printf ["%*d", W, uint]'
        self.assertTrue(accepts(schema, "  12"))
        self.assertFalse(accepts(schema, "12"))

    def test_item_count_is_checked_with_the_stars(self):
        for schema in ('r = text .printf ["%*d", uint]', 'r = text .printf ["%d", 5, uint]',
                       'r = text .printf ["%*.*d", 5, uint]', 'r = text .printf ["%*d"]'):
            with self.assertRaises(SchemaError, msg=schema) as caught:
                Validator(schema)
            self.assertIn("data item", str(caught.exception))

    def test_a_pile_of_candidates_is_bounded(self):
        validator = Validator('r = text .printf ["%*.*s", tstr, tstr, tstr]')
        self.assertFalse(validator.validate(cbor.encode("x" * 3000)).valid)  # no integer fits tstr: stops


if __name__ == "__main__":
    unittest.main()
