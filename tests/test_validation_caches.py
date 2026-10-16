"""What is remembered about schema nodes (#85) never changes an answer."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator
from cddl_verifier import _schema
from cddl_verifier._cddl import ast
from cddl_verifier._cddl.parser import parse_cddl
from cddl_verifier._cddl.printer import format_node
from cddl_verifier._validator import primitives

SCHEMA = ("r = [* item]\n"
          "item = { id: uint, name: tstr .size (1..8), tags: [* tstr], ? mode: mode }\n"
          "mode = \"a\" / \"b\"\n")
GOOD = [{"id": 1, "name": "x", "tags": ["a"], "mode": "a"}, {"id": 2, "name": "y", "tags": []}]
BAD = [{"id": 1, "name": "x", "tags": ["a"], "mode": "c"}, {"id": "2", "name": "", "tags": [1]}]


def _reference_walk(node):
    """The order ``walk`` has always had: depth first, in source order."""
    from dataclasses import fields
    yield node
    for f in fields(node):
        if f.name in ("span", "source", "comment"):
            continue
        child = getattr(node, f.name)
        if isinstance(child, tuple):
            for item in child:
                if hasattr(item, "__dataclass_fields__") and not isinstance(item, ast.Span):
                    yield from _reference_walk(item)
        elif hasattr(child, "__dataclass_fields__") and not isinstance(child, ast.Span):
            yield from _reference_walk(child)


class Walk(unittest.TestCase):
    def test_same_nodes_in_the_same_order_as_the_recursive_walk(self):
        for path in sorted((ROOT / "cddl-schemas").glob("*.cddl")):
            with self.subTest(path.name):
                schema = parse_cddl(path.read_text(encoding="utf-8"), source_name=path.name)
                self.assertEqual([id(n) for n in ast.walk(schema)],
                                 [id(n) for n in _reference_walk(schema)])

    def test_a_schema_of_many_rules(self):
        schema = parse_cddl("\n".join(f"r{i} = {{ a: uint, b: [* tstr] }}" for i in range(500)) + "\n")
        nodes = list(ast.walk(schema))
        self.assertEqual(len(nodes), len({id(n) for n in nodes}))
        self.assertEqual([id(n) for n in nodes], [id(n) for n in _reference_walk(schema)])


class NodeText(unittest.TestCase):
    def test_the_text_is_kept_off_equality_hash_and_repr(self):
        node = parse_cddl("r = [* tstr .size 4]\n").rules[0].type
        before = (repr(node), hash(node))
        text = format_node(node)
        self.assertEqual(format_node(node), text)
        self.assertEqual((repr(node), hash(node)), before)
        self.assertEqual(node, parse_cddl("r = [* tstr .size 4]\n").rules[0].type)

    def test_a_changed_copy_writes_its_own_text(self):
        node = parse_cddl("r = uint\n").rules[0].type
        other = parse_cddl("r = tstr\n").rules[0].type
        format_node(node)
        copy = replace(node, alternatives=other.alternatives)
        self.assertEqual(format_node(copy), "tstr")
        self.assertEqual(format_node(node), "uint")


class Memos(unittest.TestCase):
    def test_a_validator_answers_alike_every_time(self):
        validator = Validator(SCHEMA)
        for data, valid in ((GOOD, True), (BAD, False), (GOOD, True), (BAD, False)):
            result = validator.validate(data)
            self.assertEqual(result.valid, valid)
        first = [str(e) for e in validator.validate(BAD).errors]
        self.assertEqual(first, [str(e) for e in Validator(SCHEMA).validate(BAD).errors])

    def test_type_definitions_are_remembered_only_once_the_schema_is_complete(self):
        parser = Validator(SCHEMA)._parser
        node = parser.ast.rules[1].type
        self.assertIsNotNone(parser._type_memo)
        self.assertIsNotNone(node)
        first = parser.get_type(node)
        self.assertIs(parser.get_type(node), first)
        self.assertEqual(len(parser._type_memo), 1)

    def test_data_still_decides_a_type_choice(self):
        validator = Validator("r = c\nc = a / b\na = { x: uint }\nb = { y: tstr }\n")
        for _ in range(2):
            self.assertTrue(validator.validate({"x": 1}).valid)
            self.assertTrue(validator.validate({"y": "s"}).valid)
            self.assertFalse(validator.validate({"z": 1}).valid)

    def test_the_tables_are_bounded(self):
        original = (_schema._TYPE_MEMO_LIMIT, primitives._MEMO_LIMIT)
        _schema._TYPE_MEMO_LIMIT, primitives._MEMO_LIMIT = 2, 2
        try:
            validator = Validator(SCHEMA)
            for data, valid in ((GOOD, True), (BAD, False), (GOOD, True)):
                self.assertEqual(validator.validate(data).valid, valid)
            self.assertLessEqual(len(validator._parser._type_memo), 2)
        finally:
            _schema._TYPE_MEMO_LIMIT, primitives._MEMO_LIMIT = original

    def test_something_that_is_not_a_node_is_not_remembered(self):
        from cddl_verifier._analyzer import CBORAnalyzer
        analyzer = CBORAnalyzer(Validator(SCHEMA)._parser)
        calls = []
        compute = lambda expr: calls.append(expr) or len(calls)  # noqa: E731
        self.assertEqual(analyzer._remembered("k", "name", compute), 1)
        self.assertEqual(analyzer._remembered("k", "name", compute), 2)
        self.assertEqual(analyzer._memo.get("k", {}), {})


if __name__ == "__main__":
    unittest.main()
