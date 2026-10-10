#!/usr/bin/env python3
"""
Questions about type text answered from the AST (#111, Phase C of #69).

The validator and EDN generator still pass type expressions as text, but
since #111 nothing in ``_analyzer.py`` picks that text apart with regular
expressions: ``_cddl/query.py`` parses it with the real grammar. Also covers
the behaviour that came with it: dotted names, decoded ``.regexp`` strings,
byte-string literals, controls on literals and choice-aware primitive checks.
"""

import inspect
import re
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, validate
from cddl_verifier import _analyzer
from cddl_verifier._analyzer import CDDLParser
from cddl_verifier._cddl import query


class TestQuery(unittest.TestCase):

    def test_alternatives_keep_their_spelling(self):
        self.assertEqual(query.alternatives("[a / b] / c"), ["[a / b]", "c"])
        self.assertEqual(query.alternatives('tstr .regexp "x/y"'), ['tstr .regexp "x/y"'])
        self.assertEqual(query.alternatives("#6.1(a / b) / c"), ["#6.1(a / b)", "c"])
        self.assertEqual(query.alternatives("a // b"), ["a // b"])  # not a type

    def test_head(self):
        self.assertEqual(query.head("tstr .size 2"), "tstr")
        self.assertEqual(query.head("uint .ge 0 .le 9"), "uint")
        self.assertEqual(query.head("coswid.tag-id"), "coswid.tag-id")
        self.assertEqual(query.head("coswid.tag-id .size 2"), "coswid.tag-id")
        self.assertEqual(query.head("#6.1(x)"), "#6.1(x)")
        self.assertEqual(query.head("not a type ("), "not a type (")

    def test_controls(self):
        ops = query.controls("uint .ge 0 .le 150")
        self.assertEqual([(c.op, c.arg_text) for c in ops], [("ge", "0"), ("le", "150")])
        self.assertEqual(query.controls("uint / tstr .size 2"), [])

    def test_paren(self):
        parts = query.paren("(uint / nil) .size 1")
        self.assertEqual(parts.inner, ["uint", "nil"])
        self.assertEqual([(c.op, c.arg_text) for c in parts.controls], [("size", "1")])
        parts = query.paren('(tstr .size 3) .regexp "a+"')
        self.assertEqual(parts.inner, ["tstr .size 3"])
        self.assertEqual([c.op for c in parts.controls], ["regexp"])
        self.assertEqual(query.paren("(tstr)"), (["tstr"], []))
        self.assertIsNone(query.paren("tstr .size 1"))
        self.assertIsNone(query.paren("(a) / b"))
        self.assertIsNone(query.paren("#6.1(a)"))

    def test_without_controls(self):
        self.assertEqual(query.without_controls("tstr .size 3 .regexp \"a\""), "tstr")
        self.assertEqual(query.without_controls("foo<uint>"), "foo<uint>")
        self.assertEqual(query.without_controls("(uint) .le 3"), "(uint)")
        self.assertIsNone(query.without_controls("1..5"))
        self.assertIsNone(query.without_controls("a / b"))

    def test_tag_and_cbor(self):
        self.assertEqual(query.tag("#6.1(#6.2(m))"), (1, "#6.2(m)"))
        self.assertIsNone(query.tag("#6.<1..3>(m)"))
        self.assertEqual(query.cbor_control("bstr .cbor x / y"), None)
        self.assertEqual(query.cbor_control("bytes .cbor inner"), ("bytes", "inner"))

    def test_inline_array(self):
        self.assertEqual(query.inline_array("[ + tstr ]"), ("+", "tstr"))
        self.assertEqual(query.inline_array("[* tstr .size 2]"), ("*", "tstr .size 2"))
        self.assertEqual(query.inline_array("[ int ]"), ("", "int"))
        self.assertIsNone(query.inline_array("[ a, b ]"))
        self.assertIsNone(query.inline_array("[ a: int ]"))
        self.assertTrue(query.is_array("[ a, b ]"))

    def test_cbor_control_through_a_chain(self):
        self.assertEqual(query.cbor_control("bstr .cbor uint .size 1"), ("bstr", "uint"))
        self.assertEqual(query.cbor_control("bstr .size 1 .cbor uint"), ("bstr", "uint"))
        self.assertIsNone(query.cbor_control("tstr .cbor uint"))

    def test_long_text_is_not_cached(self):
        long_text = "tstr / " * 300 + "uint"
        query.parse(long_text)
        cached = query._parse_cached.cache_info().currsize
        query.parse(long_text)
        self.assertEqual(query._parse_cached.cache_info().currsize, cached)
        self.assertEqual(len(query.alternatives(long_text)), 301)

    def test_names(self):
        self.assertEqual(query.names("#6.1(r@a) / [ + b ]"), {"r@a", "b"})
        self.assertEqual(query.names("not ( a type"), set())

    def test_no_regex_over_type_text_in_analyzer(self):
        # The only regular expression left matches data against a .regexp pattern.
        source = inspect.getsource(_analyzer)
        calls = re.findall(r"\bre\.(\w+)\(", source)
        self.assertEqual(set(calls), {"fullmatch"})


class TestBehaviour(unittest.TestCase):

    def test_dotted_names(self):
        # 'coswid.tag-id' used to be cut at the dot.
        schema = "r = { a: coswid.tag-id }\ncoswid.tag-id = tstr / bstr"
        self.assertTrue(validate(schema, {"a": "x"}).valid)
        self.assertFalse(validate(schema, {"a": 1}).valid)

    def test_regexp_string_escapes_are_decoded(self):
        # RFC 8610's example: "\\." in CDDL text is the regex \. (a literal dot)
        schema = 'r = { a: tstr .regexp "a\\\\.b" }'
        self.assertTrue(validate(schema, {"a": "a.b"}).valid)
        self.assertFalse(validate(schema, {"a": "axb"}).valid)
        self.assertEqual(CDDLParser("").extract_regexp('tstr .regexp "\\\\d+"'), "\\d+")

    def test_byte_string_literals(self):
        schema = "r = { a: h'0102' / b64'AQI' / 'x' }"
        self.assertTrue(validate(schema, {"a": b"\x01\x02"}).valid)
        self.assertTrue(validate(schema, {"a": b"x"}).valid)
        self.assertFalse(validate(schema, {"a": b"\x03"}).valid)
        self.assertFalse(validate(schema, {"a": "x"}).valid)

    def test_controls_on_literals(self):
        # At the root and in choices. A literal as a plain field or element
        # type is not checked yet (#73).
        self.assertTrue(validate('r = "t" .size (1..3)', "t").valid)
        self.assertFalse(validate('r = "t" .size (2..3)', "t").valid)
        self.assertTrue(validate("r = 1 .ge 0 .le 9", 1).valid)
        self.assertFalse(validate('r = { a: "t" .regexp "[a-c]+" / 1 }', {"a": "t"}).valid)
        self.assertTrue(validate('r = { a: "a" .regexp "[a-c]+" / 1 }', {"a": "a"}).valid)

    def test_choice_with_byte_literal_is_checked(self):
        # #106: one uncheckable alternative used to accept every value
        schema = "r = { a: h'00' / { b: int } }"
        self.assertFalse(validate(schema, {"a": 5}).valid)
        self.assertTrue(validate(schema, {"a": b"\x00"}).valid)
        self.assertTrue(validate(schema, {"a": {"b": 1}}).valid)

    def test_choice_aware_primitive_check(self):
        # An alias to a choice, used with a control
        schema = "r = [ m .size 2 ]\nm = bstr / tstr"
        self.assertFalse(validate(schema, [5]).valid)
        self.assertTrue(validate(schema, [b"ab"]).valid)

    def test_multi_entry_inline_array_field_must_be_an_array(self):
        schema = "r = { p: [ int, tstr ] }"
        self.assertFalse(validate(schema, {"p": 1}).valid)
        self.assertTrue(validate(schema, {"p": [1, "a"]}).valid)

    def test_embedded_cbor_is_checked_everywhere(self):
        from cddl_verifier.cbor import encode
        bad, good, too_big = encode(True), encode(5), encode(300)
        cases = [
            ("r = bstr .cbor uint .size 1", lambda d: encode(d)),          # root
            ("r = { a: bstr .cbor uint .size 1 }", lambda d: {"a": d}),   # field
            ("r = { a: bstr .cbor uint }", lambda d: {"a": d}),          # field, no chain
            ("r = [ * bstr .cbor uint .size 1 ]", lambda d: [d]),         # array element
            ("r = { a: [ * bstr .cbor uint ] }", lambda d: {"a": [d]}),   # inline array
            ("r = { a: bstr .cbor uint .size 1 / tstr }", lambda d: {"a": d}),  # choice
        ]
        for schema, wrap in cases:
            with self.subTest(schema=schema):
                self.assertFalse(validate(schema, wrap(bad)).valid)
                self.assertTrue(validate(schema, wrap(good)).valid)
                if ".size 1" in schema:
                    self.assertFalse(validate(schema, wrap(too_big)).valid)

    def test_dotted_range_hint(self):
        with self.assertRaisesRegex(SchemaError, "write 'lo .. hi' for a range"):
            validate("r = { a: tstr .size (lo..hi) }\nlo = 1\nhi = 2", {"a": "ab"})


if __name__ == "__main__":
    unittest.main()
