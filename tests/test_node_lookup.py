#!/usr/bin/env python3
"""Name lookup and structure validation on nodes (#176, Part 2 of #115).

``get_type`` takes a rule name or a type expression as text or as a ``Type``
node, and the structure validator reads the nodes the tables carry. These tests
check that node and text give the same answers, over the bundled schemas, and
that the shapes the validator hands on (the ``.cbor`` inner type, a peeled tag)
are nodes.
"""

import logging
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier._analyzer import CBORAnalyzer, CDDLParser  # noqa: E402
from cddl_verifier._cddl.ast import Type  # noqa: E402
from cddl_verifier._cddl.printer import format_node  # noqa: E402
from cddl_verifier._cddl.typeexpr import as_type  # noqa: E402

from .test_type_nodes import schemas  # noqa: E402

SCHEMA = """
root = #6.7(inner)
inner = {1: tstr}
wrapped = bstr .cbor inner
n = uint
tagged-n = #6.9(n)
alias = tagged-n
$s /= inner
$s /= tstr
choice = inner / tstr
"""


def parser(text=SCHEMA):
    return CDDLParser(text)


class Quiet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        warnings.simplefilter("ignore")

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)
        warnings.resetwarnings()


class GetTypeOnNodes(Quiet):
    def test_a_name_and_its_node_find_the_same_definition(self):
        p = parser()
        for name in ("root", "inner", "wrapped", "n", "alias", "tagged-n", "$s", "choice"):
            with self.subTest(name):
                self.assertEqual(p.get_type(name), p.get_type(as_type(name)))
                self.assertEqual(p.get_type(name, {1: "x"}), p.get_type(as_type(name), {1: "x"}))

    def test_expressions_as_text_and_as_nodes(self):
        p = parser()
        for text in ("#6.7(inner)", "bstr .cbor inner", "tstr", "uint .size 2", "nope"):
            with self.subTest(text):
                self.assertEqual(p.get_type(text), p.get_type(as_type(text)))

    def test_every_table_type_resolves_alike_over_the_schemas(self):
        for name, text in schemas().items():
            try:
                p = CDDLParser(text)
            except Exception:  # schemas some probes use to test errors
                continue
            for rule in list(p.types) + list(p.type_aliases) + list(p.type_choices):
                with self.subTest(f"{name} {rule}"):
                    self.assertEqual(p.get_type(rule), p.get_type(as_type(rule)))

    def test_the_embedded_cbor_type_is_a_node(self):
        for expr in ("bstr .cbor inner", as_type("bstr .cbor inner")):
            inner = parser().get_type(expr)['cbor_inner_type']
            self.assertIsInstance(inner, Type)
            self.assertEqual(format_node(inner), "inner")

    def test_a_tag_is_recorded_for_nodes_too(self):
        self.assertEqual(parser().get_type(as_type("#6.7(inner)"))['cbor_tags'], (7,))

    def test_choice_alternatives_for_names_and_nodes(self):
        p = parser()
        self.assertEqual(p.choice_alternatives("$s"), p.choice_alternatives(as_type("$s")))
        self.assertEqual([format_node(a) for a in p.choice_alternatives("m1 / m2")], ["m1", "m2"])
        self.assertEqual([format_node(a) for a in p.choice_alternatives(as_type("m1 / m2"))],
                         ["m1", "m2"])
        self.assertEqual([format_node(a) for a in p.choice_alternatives(as_type("$s"))],
                         ["inner", "tstr"])
        self.assertIsNone(p.choice_alternatives(as_type("m1")))


class PeelTags(Quiet):
    def setUp(self):
        self.analyzer = CBORAnalyzer(parser())

    def test_untagged_types_come_back_as_they_were(self):
        node = as_type("uint")
        peeled, value, error = self.analyzer._peel_tagged_primitive(node, 5)
        self.assertIs(peeled, node)
        self.assertEqual((value, error), (5, None))

    def test_a_tagged_primitive_is_peeled_to_a_node(self):
        peeled, value, error = self.analyzer._peel_tagged_primitive("alias", (9, 5))
        self.assertIsInstance(peeled, Type)
        self.assertEqual((format_node(peeled), value, error), ("n", 5, None))

    def test_a_missing_or_wrong_tag_is_described(self):
        _, _, error = self.analyzer._peel_tagged_primitive(as_type("alias"), 5)
        self.assertIn("not tagged", error)
        _, _, error = self.analyzer._peel_tagged_primitive(as_type("alias"), (8, 5))
        self.assertIn("got tag 8", error)


class Validation(Quiet):
    def check(self, data, name):
        analyzer = CBORAnalyzer(parser())
        return analyzer.validate(data, name), analyzer.get_errors()

    def test_roots_through_aliases_and_tags(self):
        self.assertEqual(self.check((9, 5), "alias"), (True, []))
        ok, errors = self.check((8, 5), "alias")
        self.assertFalse(ok)
        self.assertIn("requires CBOR tag 9", errors[0])
        self.assertEqual(self.check((7, {1: "x"}), "root"), (True, []))

    def test_an_unknown_root_is_reported_by_name(self):
        ok, errors = self.check(1, "nope")
        self.assertFalse(ok)
        self.assertIn("'nope' not found", errors[0])

    def test_embedded_cbor(self):
        ok, errors = self.check(b"\xa1\x01\x61x", "wrapped")
        self.assertTrue(ok, errors)
        ok, errors = self.check(b"\xa1\x01\x01", "wrapped")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
