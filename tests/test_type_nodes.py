#!/usr/bin/env python3
"""Type expressions held as nodes (#175, #177: Parts 1 and 3 of #115).

The ``CDDLParser`` tables hold every type expression as a ``Type`` node, and
``query`` answers questions about nodes only. These tests check, over the
bundled schemas and every schema in the conformance matrix, that no table
holds text, and cover ``as_type`` (where text enters), ``resolve_alias`` and
the pieces ``query`` hands back.
"""

import json
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

from cddl_verifier._analyzer import CDDLParser  # noqa: E402
from cddl_verifier._cddl import query  # noqa: E402
from cddl_verifier._cddl.ast import Name, Type  # noqa: E402
from cddl_verifier._cddl.parser import parse_type_expr  # noqa: E402
from cddl_verifier._cddl.printer import format_node  # noqa: E402
from cddl_verifier._cddl.typeexpr import as_type, bare_name, rename  # noqa: E402


def schemas():
    """Every bundled schema and every single-module schema in the conformance matrix."""
    found = {}
    for path in sorted((ROOT / "cddl-schemas").glob("*.cddl")):
        found[path.name] = path.read_text(encoding="utf-8")
    matrix = json.loads((ROOT / "docs" / "conformance.json").read_text(encoding="utf-8"))
    for row in matrix["rows"]:
        for index, group in enumerate(row.get("probes", ())):
            if "modules" not in group:
                found[f"{row['id']}#{index}"] = group["schema"]
    return found


def table_types(parser):
    """Every table entry that holds a type expression, with where it is."""
    for name, value in parser.type_aliases.items():
        yield f"alias {name}", value
    for name, values in parser.type_choices.items():
        for value in values:
            yield f"choice {name}", value

    def walk_def(owner, type_def):
        for key, field in type_def.get('fields', {}).items():
            yield f"{owner} field {key}", field['type']
        for index, value in type_def.get('element_types', {}).items():
            yield f"{owner} element {index}", value
        for computed in type_def.get('computed_keys', ()):
            yield f"{owner} computed key", computed['key']
            yield f"{owner} computed value", computed['type']
        for value in type_def.get('constraints', ()):
            yield f"{owner} constraint", value
        for alternative in type_def.get('alternatives', ()):
            yield from walk_def(owner, alternative)
        for choice in type_def.get('sequence', ()) or ():
            yield from walk_sequence(owner, choice)

    def walk_sequence(owner, entries):
        for entry in entries:
            if 'type' in entry:
                yield f"{owner} sequence", entry['type']
            for inner in entry.get('group', ()) or ():
                yield from walk_sequence(owner, inner if isinstance(inner, list) else [inner])

    for name, type_def in parser.types.items():
        yield from walk_def(name, type_def)


class Quiet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        warnings.simplefilter("ignore")

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)
        warnings.resetwarnings()


class AsType(Quiet):
    def test_as_type_accepts_nodes_and_text(self):
        node = parse_type_expr("uint / tstr")
        self.assertIs(as_type(node), node)
        self.assertEqual(as_type("uint / tstr"), node)
        self.assertEqual(as_type(Name("uint")), Type((Name("uint"),)))

    def test_text_that_is_not_a_type_is_one_opaque_name(self):
        fragment = as_type("a: int, b: tstr")
        self.assertEqual(bare_name(fragment), "a: int, b: tstr")
        self.assertEqual(query.alternatives(fragment), [fragment])
        self.assertEqual(query.controls(fragment), [])

    def test_bare_name_is_a_name_without_arguments_or_controls(self):
        self.assertEqual(bare_name(as_type("foo")), "foo")
        for text in ("foo<uint>", "foo .size 1", "foo / bar", "1", "[* foo]"):
            self.assertIsNone(bare_name(as_type(text)), text)

    def test_rename_changes_only_that_name(self):
        node = parse_type_expr("#6.4(a@tag) / b")
        self.assertEqual(format_node(rename(node, "a@tag", "a@tag@prelude")),
                         "#6.4(a@tag@prelude) / b")
        self.assertIs(rename(node, "zzz", "y"), node)
        generic = parse_type_expr("a<a>")
        self.assertEqual(format_node(rename(generic, "a", "z")), "a<z>")


class TablesHoldNodes(Quiet):
    def test_every_table_type_is_a_node_over_the_schemas(self):
        checked = 0
        for label, source in schemas().items():
            try:
                parser = CDDLParser(source)
            except Exception:  # a schema the matrix lists as an error
                continue
            for where, value in table_types(parser):
                with self.subTest(schema=label, where=where):
                    self.assertIsInstance(value, Type)
                checked += 1
        self.assertGreater(checked, 500)

    def test_a_table_type_prints_and_parses_back_to_itself(self):
        for label, source in schemas().items():
            try:
                parser = CDDLParser(source)
            except Exception:
                continue
            for where, value in table_types(parser):
                with self.subTest(schema=label, where=where):
                    self.assertEqual(as_type(format_node(value)), value)

    def test_prelude_renaming_is_applied_to_the_node(self):
        parser = CDDLParser("decfrac@tag = uint\nr = [decfrac]")
        alias = parser.type_aliases["decfrac"]
        self.assertIsInstance(alias, Type)
        self.assertIn("@prelude", format_node(alias))

    def test_resolve_alias_follows_names_only(self):
        parser = CDDLParser("a = b\nb = c\nc = tstr .size 3\nd = uint / nil\ne = d")
        expected = {"a": "tstr .size 3", "b": "tstr .size 3", "c": "tstr .size 3",
                    "d": "uint / nil", "e": "uint / nil", "uint": "uint", "text": "tstr",
                    "missing": "missing"}
        for name, text in expected.items():
            with self.subTest(name=name):
                self.assertEqual(format_node(parser.resolve_alias(name)), text)
                self.assertEqual(format_node(parser.resolve_alias(as_type(name))), text)

    def test_resolve_alias_stops_at_the_depth(self):
        parser = CDDLParser("a = b\nb = c\nc = tstr")
        self.assertEqual(format_node(parser.resolve_alias("a", max_depth=2)), "c")
        self.assertEqual(format_node(parser.resolve_alias("a", max_depth=1)), "b")

    def test_alias_node(self):
        parser = CDDLParser("a = tstr .size 3")
        self.assertEqual(format_node(parser.alias_node("a")), "tstr .size 3")
        self.assertIsNone(parser.alias_node("nope"))


class QueryOnNodes(Quiet):
    def test_query_does_not_take_text(self):
        for name in ("alternatives", "head", "controls", "paren", "tag", "cbor_control",
                     "inline_array", "is_array", "names", "base"):
            with self.subTest(query=name):
                with self.assertRaises(TypeError):
                    getattr(query, name)("uint / tstr")

    def test_pieces_of_a_node_are_nodes(self):
        node = parse_type_expr("uint / tstr")
        self.assertTrue(all(isinstance(alt, Type) for alt in query.alternatives(node)))
        inner = query.tag(parse_type_expr("#6.32(tstr)"))[1]
        self.assertIsInstance(inner, Type)
        self.assertIsInstance(query.cbor_control(parse_type_expr("bstr .cbor uint"))[1], Type)
        self.assertIsInstance(query.item_ref(parse_type_expr("#6.<uint>(tstr)")).inner, Type)
        paren = query.paren(parse_type_expr("(uint / tstr) .size 1"))
        self.assertTrue(all(isinstance(alt, Type) for alt in paren.inner))
        self.assertIsInstance(query.inline_array(parse_type_expr("[+ tstr]"))[1], Type)

    def test_head_and_control_arguments_are_text(self):
        self.assertEqual(query.head(parse_type_expr("tstr .size 2")), "tstr")
        self.assertEqual(query.head(parse_type_expr("coswid.tag-id")), "coswid.tag-id")
        self.assertEqual(query.head(parse_type_expr("#6.1(x)")), "#6.1(x)")
        controls = query.controls(parse_type_expr("uint .ge 0 .le 150"))
        self.assertEqual([(c.op, c.arg_text) for c in controls], [("ge", "0"), ("le", "150")])

    def test_base_looks_through_controls(self):
        self.assertEqual(query.base(parse_type_expr("tstr .size 1 .regexp \"a\"")), Name("tstr"))
        self.assertIsNone(query.base(parse_type_expr("a / b")))


if __name__ == "__main__":
    unittest.main()
