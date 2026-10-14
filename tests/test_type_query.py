#!/usr/bin/env python3
"""
Questions about type expressions answered from the AST (#111, #177).

The validator and EDN generator hold type expressions as nodes, and nothing in
``_analyzer.py`` picks type text apart with regular expressions:
``_cddl/query.py`` answers from the node. Also covers
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
from cddl_verifier._cddl.ast import Type
from cddl_verifier._cddl.parser import parse_type_expr
from cddl_verifier._cddl.printer import format_node


def T(text):
    """The node of *text*: query answers questions about nodes only (#177)."""
    return parse_type_expr(text)


def F(value):
    """Canonical text of a node, or of each node in a list."""
    if isinstance(value, list):
        return [F(v) for v in value]
    return format_node(value)


class TestQuery(unittest.TestCase):

    def test_alternatives_are_nodes(self):
        self.assertEqual(F(query.alternatives(T("[a / b] / c"))), ["[ a / b ]", "c"])
        self.assertEqual(F(query.alternatives(T('tstr .regexp "x/y"'))), ['tstr .regexp "x/y"'])
        self.assertEqual(F(query.alternatives(T("#6.1(a / b) / c"))), ["#6.1(a / b)", "c"])
        self.assertEqual(F(query.alternatives(T("a"))), ["a"])
        self.assertTrue(all(isinstance(a, Type) for a in query.alternatives(T("a / b"))))

    def test_text_is_refused(self):
        with self.assertRaises(TypeError):
            query.alternatives("a / b")

    def test_head(self):
        self.assertEqual(query.head(T("tstr .size 2")), "tstr")
        self.assertEqual(query.head(T("uint .ge 0")), "uint")
        self.assertEqual(query.head(T("coswid.tag-id")), "coswid.tag-id")
        self.assertEqual(query.head(T("coswid.tag-id .size 2")), "coswid.tag-id")
        self.assertEqual(query.head(T("#6.1(x)")), "#6.1(x)")
        self.assertEqual(query.head(T("a / b")), "a / b")

    def test_controls(self):
        ops = query.controls(T("uint .ge 0"))
        self.assertEqual([(c.op, c.arg_text) for c in ops], [("ge", "0")])
        # a control on a controlled type is its own: the target is the parentheses
        ops = query.controls(T("(uint .ge 0) .le 150"))
        self.assertEqual([(c.op, c.arg_text) for c in ops], [("le", "150")])
        self.assertEqual(query.controls(T("uint / tstr .size 2")), [])

    def test_paren(self):
        parts = query.paren(T("(uint / nil) .size 1"))
        self.assertEqual(F(parts.inner), ["uint", "nil"])
        self.assertEqual([(c.op, c.arg_text) for c in parts.controls], [("size", "1")])
        parts = query.paren(T('(tstr .size 3) .regexp "a+"'))
        self.assertEqual(F(parts.inner), ["tstr .size 3"])
        self.assertEqual([c.op for c in parts.controls], ["regexp"])
        parts = query.paren(T("(tstr)"))
        self.assertEqual((F(parts.inner), parts.controls), (["tstr"], []))
        self.assertIsNone(query.paren(T("tstr .size 1")))
        self.assertIsNone(query.paren(T("(a) / b")))
        self.assertIsNone(query.paren(T("#6.1(a)")))

    def test_without_controls(self):
        self.assertEqual(F(query.without_controls(T("tstr .size 3"))), "tstr")
        self.assertEqual(F(query.without_controls(T("foo<uint>"))), "foo<uint>")
        self.assertEqual(F(query.without_controls(T("(uint) .le 3"))), "(uint)")
        self.assertIsNone(query.without_controls(T("1..5")))
        self.assertIsNone(query.without_controls(T("a / b")))

    def test_choice_from(self):
        self.assertIsNotNone(query.choice_from(T("&(a: 0, b: 1)")).group)
        self.assertEqual(query.choice_from(T("&colors")).name, "colors")
        self.assertIsNone(query.choice_from(T("&a / b")))
        self.assertIsNone(query.choice_from(T("tstr")))

    def test_tag_and_cbor(self):
        number, inner = query.tag(T("#6.1(#6.2(m))"))
        self.assertEqual((number, F(inner)), (1, "#6.2(m)"))
        self.assertIsNone(query.tag(T("#6.<1..3>(m)")))
        self.assertIsNone(query.cbor_control(T("bstr .cbor x / y")))
        base, inner = query.cbor_control(T("bytes .cbor inner"))
        self.assertEqual((base, F(inner)), ("bytes", "inner"))

    def test_inline_array(self):
        occurrence, element = query.inline_array(T("[ + tstr ]"))
        self.assertEqual((occurrence, F(element)), ("+", "tstr"))
        occurrence, element = query.inline_array(T("[* tstr .size 2]"))
        self.assertEqual((occurrence, F(element)), ("*", "tstr .size 2"))
        occurrence, element = query.inline_array(T("[ int ]"))
        self.assertEqual((occurrence, F(element)), ("", "int"))
        self.assertIsNone(query.inline_array(T("[ a, b ]")))
        self.assertIsNone(query.inline_array(T("[ a: int ]")))
        self.assertTrue(query.is_array(T("[ a, b ]")))

    def test_cbor_control_through_parentheses(self):
        for text in ("bstr .cbor uint", "(bstr .cbor uint) .size 1", "(bstr .size 1) .cbor uint"):
            base, inner = query.cbor_control(T(text))
            self.assertEqual((base, F(inner)), ("bstr", "uint"))
        self.assertIsNone(query.cbor_control(T("tstr .cbor uint")))

    def test_long_choice(self):
        self.assertEqual(len(query.alternatives(T("tstr / " * 300 + "uint"))), 301)

    def test_names(self):
        self.assertEqual(query.names(T("#6.1(r@a) / [ + b ]")), {"r@a", "b"})

    def test_no_regex_over_type_text_in_analyzer(self):
        # No regular expression is run over type text; .regexp patterns are
        # matched by xsd_regex (#182).
        source = inspect.getsource(_analyzer)
        calls = re.findall(r"\bre\.(\w+)\(", source)
        self.assertEqual(set(calls), set())


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
        self.assertEqual(CDDLParser("").extract_regexp(T('tstr .regexp "\\\\d+"')), "\\d+")

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
        self.assertTrue(validate("r = (1 .ge 0) .le 9", 1).valid)
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
            ("r = (bstr .cbor uint) .size 1", lambda d: encode(d)),          # root
            ("r = { a: (bstr .cbor uint) .size 1 }", lambda d: {"a": d}),   # field
            ("r = { a: bstr .cbor uint }", lambda d: {"a": d}),          # field, no chain
            ("r = [ * (bstr .cbor uint) .size 1 ]", lambda d: [d]),         # array element
            ("r = { a: [ * bstr .cbor uint ] }", lambda d: {"a": [d]}),   # inline array
            ("r = { a: (bstr .cbor uint) .size 1 / tstr }", lambda d: {"a": d}),  # choice
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
