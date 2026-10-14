"""Chained controls (``uint .ge 0 .le 150``) are deprecated (#113)."""

import re
import tempfile
import unittest
import warnings
from pathlib import Path

from cddl_verifier import Validator, cbor


def _check(schema, value, **kw):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return Validator(schema, **kw).validate(cbor.encode(value))


def _warnings(schema, **kw):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        validator = Validator(schema, **kw)
    return validator, caught


class TestDeprecation(unittest.TestCase):
    def test_a_chain_warns_with_position_and_standard_spelling(self):
        validator, caught = _warnings("a = uint\nb = uint .ge 0 .le 150\n")
        self.assertEqual(len(validator.warnings), 1)
        message = validator.warnings[0]
        self.assertTrue(message.startswith("<schema>:2:5: "), message)
        self.assertIn("deprecated", message)
        self.assertIn("write `(uint .ge 0) .le 150` instead of `uint .ge 0 .le 150`", message)
        self.assertEqual([str(w.message) for w in caught], [message])
        self.assertTrue(issubclass(caught[0].category, UserWarning))

    def test_one_warning_per_chain_however_long(self):
        validator, _ = _warnings('a = tstr .size 1 .regexp "a" .ne "b"\n')
        self.assertEqual(len(validator.warnings), 1)
        self.assertIn('write `((tstr .size 1) .regexp "a") .ne "b"`', validator.warnings[0])

    def test_each_chain_is_reported_where_it_is_nested(self):
        validator, _ = _warnings("m = {x: uint .ge 0 .le 9, y: [* int .ge 1 .le 2]}\n")
        self.assertEqual(len(validator.warnings), 2)
        self.assertEqual([m.split(': ')[0] for m in validator.warnings],
                         ["<schema>:1:9", "<schema>:1:33"])

    def test_the_standard_forms_do_not_warn(self):
        for schema in ("a = (uint .ge 0) .le 150\n", "a = 0..150\n",
                       "a = (uint .ge 0) .and (uint .le 150)\n", "a = uint .size 1\n",
                       "a = (uint .ge 0 / tstr) .size 1\n"):
            with self.subTest(schema=schema):
                validator, caught = _warnings(schema)
                self.assertEqual(validator.warnings, ())
                self.assertEqual(caught, [])

    def test_the_suggested_spelling_means_the_same(self):
        for chained, standard in (
                ("uint .ge 0 .le 150", "(uint .ge 0) .le 150"),
                ('tstr .size 1 .regexp "a" .ne "b"', '((tstr .size 1) .regexp "a") .ne "b"'),
                ("bstr .cbor uint .size 1", "(bstr .cbor uint) .size 1")):
            validator, _ = _warnings(f"a = {chained}\n")
            suggested = re.search(r"write `(.*)` instead", validator.warnings[0]).group(1)
            self.assertEqual(suggested, standard)
            samples = [0, 5, 150, 151, -1, "a", "b", "ab", cbor.encode(7), cbor.encode(300), b"x"]
            for value in samples:
                with self.subTest(chained=chained, value=value):
                    self.assertEqual(_check(f"a = {chained}\n", value).valid,
                                     _check(f"a = {standard}\n", value).valid)

    def test_a_chain_is_still_enforced(self):
        schema = "a = uint .ge 10 .le 20\n"
        self.assertTrue(_check(schema, 15).valid)
        self.assertFalse(_check(schema, 5).valid)
        self.assertFalse(_check(schema, 25).valid)

    def test_a_chain_in_a_module_names_the_module(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'lib.cddl').write_text("n = uint\nage = n .ge 0 .le 150\n")
            validator, _ = _warnings(";# import lib as lib\nroot = lib.age\n",
                                     modules={'lib': Path(directory) / 'lib.cddl'})
        self.assertEqual(len(validator.warnings), 1)
        self.assertIn("lib.cddl:2:7: ", validator.warnings[0])

    def test_a_module_reached_twice_warns_once(self):
        modules = {'lib': "a = uint .ge 0 .le 5\n",
                   'mid': ";# import lib as lib\nb = lib.a\n"}
        schema = ";# import lib as lib\n;# import mid as mid\nroot = lib.a / mid.b\n"
        validator, _ = _warnings(schema, modules=modules)
        self.assertEqual(len(validator.warnings), 1)
        self.assertIn("<module lib>:1:5: ", validator.warnings[0])


if __name__ == '__main__':
    unittest.main()
