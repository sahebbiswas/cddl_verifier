#!/usr/bin/env python3
"""Type text that carries its node, and ``query`` on nodes (#175, Part 1 of #115).

The ``CDDLParser`` tables hold type expressions as :class:`TypeText`: the
canonical text, which also has the node it was made from. ``query`` answers
the same questions about a node as about its text. These tests check, over the
bundled schemas and every schema in the conformance matrix, that the text is
the node's text and that the node and text answers agree, so the validator can
read nodes without changing what it decides.
"""

import copy
import json
import logging
import pickle
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
from cddl_verifier._cddl.typetext import TypeText, as_type, bare_name, rename  # noqa: E402


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


def type_texts(parser):
    """Every table entry that holds type text, with where it is."""
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


class TypeTextValue(Quiet):
    def test_text_is_the_nodes_text(self):
        node = parse_type_expr("tstr .size (1..3) / nil")
        text = TypeText(node)
        self.assertEqual(text, "tstr .size (1..3) / nil")
        self.assertIs(text.node, node)
        self.assertIsInstance(text, str)

    def test_copies_and_pickles_keep_the_node(self):
        text = TypeText(parse_type_expr("#6.7(uint)"))
        self.assertIs(copy.deepcopy(text), text)
        self.assertIs(copy.copy(text), text)
        again = pickle.loads(pickle.dumps(text))
        self.assertEqual((again, again.node), (text, text.node))

    def test_as_type_accepts_nodes_typetext_and_text(self):
        node = parse_type_expr("uint / tstr")
        self.assertIs(as_type(node), node)
        self.assertIs(as_type(TypeText(node)), node)
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


class TablesCarryNodes(Quiet):
    def test_every_table_text_is_its_nodes_text(self):
        checked = 0
        for label, source in schemas().items():
            try:
                parser = CDDLParser(source)
            except Exception:  # a schema the matrix lists as an error
                continue
            for where, text in type_texts(parser):
                if not isinstance(text, TypeText):
                    continue  # a synthetic type name (a plain str)
                with self.subTest(schema=label, where=where):
                    self.assertEqual(str(text), format_node(text.node))
                    self.assertEqual(as_type(str(text)), text.node,
                                     "the text parses back to the node")
                checked += 1
        self.assertGreater(checked, 500)

    def test_prelude_renaming_keeps_text_and_node_together(self):
        parser = CDDLParser("decfrac@tag = uint\nr = [decfrac]")
        alias = parser.type_aliases["decfrac"]
        self.assertIsInstance(alias, TypeText)
        self.assertEqual(str(alias), format_node(alias.node))
        self.assertIn("@prelude", alias)

    def test_resolve_alias_walks_like_resolve_type_alias(self):
        parser = CDDLParser("a = b\nb = c\nc = tstr .size 3\nd = uint / nil\ne = d")
        for name in ("a", "b", "c", "d", "e", "uint", "text", "number", "missing"):
            with self.subTest(name=name):
                self.assertEqual(format_node(parser.resolve_alias(name)),
                                 parser.resolve_type_alias(name))

    def test_alias_node(self):
        parser = CDDLParser("a = tstr .size 3")
        self.assertEqual(format_node(parser.alias_node("a")), "tstr .size 3")
        self.assertIsNone(parser.alias_node("nope"))


class QueryOnNodes(Quiet):
    EXPRESSIONS = [
        "uint", "tstr .size (1..4)", "uint .ge 1 .le 5", "uint / tstr / nil", "(uint / tstr) .size 1",
        "#6.32(tstr)", "#6.<uint>(tstr)", "#7.25", "#", "#2", "bstr .cbor uint",
        "bstr .cbor (uint / tstr)", "[* uint]", "[+ tstr]", "[uint, tstr]", "1..5", "0.5 ... 1.5",
        '"a"', "h'01'", "1.5", "true", "&(a: 0, b: 1)", "&colors", "~t", "$socket",
        "foo<uint, tstr>", "{ a: int }", "tstr .regexp \"[a-z]+\"",
    ]

    def test_the_same_answers_for_a_node_and_its_text(self):
        def shape(value):
            """Pieces compare by text, whichever form they come in."""
            if isinstance(value, Type):
                return format_node(value)
            if hasattr(value, '_fields'):
                return {f: shape(getattr(value, f)) for f in value._fields if f != 'parsed'}
            if isinstance(value, (list, tuple)):
                return [shape(v) for v in value]
            if hasattr(value, '__dataclass_fields__'):
                return format_node(value)
            return value

        for expression in self.EXPRESSIONS:
            node = parse_type_expr(expression)
            text = format_node(node)  # the tables hold canonical text
            for name in ("alternatives", "head", "controls", "paren", "without_controls",
                         "tag", "item_ref", "cbor_control", "literal", "range_of",
                         "inline_array", "is_array", "names", "choice_from"):
                with self.subTest(text=text, query=name):
                    function = getattr(query, name)
                    self.assertEqual(shape(function(node)), shape(function(text)))

    def test_pieces_of_a_node_are_nodes(self):
        node = parse_type_expr("uint / tstr")
        self.assertTrue(all(isinstance(alt, Type) for alt in query.alternatives(node)))
        inner = query.tag(parse_type_expr("#6.32(tstr)"))[1]
        self.assertIsInstance(inner, Type)
        self.assertIsInstance(query.cbor_control(parse_type_expr("bstr .cbor uint"))[1], Type)
        self.assertIsInstance(query.item_ref(parse_type_expr("#6.<uint>(tstr)")).inner, Type)
        paren = query.paren(parse_type_expr("(uint / tstr) .size 1"))
        self.assertTrue(all(isinstance(alt, Type) for alt in paren.inner))

    def test_base_looks_through_controls(self):
        self.assertEqual(query.base(parse_type_expr("tstr .size 1 .regexp \"a\"")), Name("tstr"))
        self.assertIsNone(query.base(parse_type_expr("a / b")))


if __name__ == "__main__":
    unittest.main()
