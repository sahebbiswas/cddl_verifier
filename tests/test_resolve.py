#!/usr/bin/env python3
"""
CDDL semantic analysis and name resolution (#70).

``resolve()`` checks a parsed schema for what the grammar cannot express and
builds the resolved model the ``CDDLParser`` tables are made from.
See docs/CDDL_AST_DESIGN.md §8.
"""

import pickle
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError, Validator, validate
from cddl_verifier._analyzer import CDDLParser
from cddl_verifier._cddl import CDDLSemanticError, format_node, parse_cddl
from cddl_verifier._cddl.ast import Group, IntLit, Map, Name, Paren, Range, Type
from cddl_verifier._cddl.resolve import KNOWN_CONTROLS, resolve, substitute

SCHEMAS = Path(__file__).resolve().parent.parent / "cddl-schemas"


def resolved(text):
    return resolve(parse_cddl(text))


class TestRejected(unittest.TestCase):
    """Each problem is a CDDLSemanticError with its position."""

    CASES = [
        # undefined and conflicting names
        ("r = { a: tsrt }", (1, 10), "undefined name 'tsrt'; did you mean 'tstr'?"),
        ("r = [ * item ]", (1, 7), "undefined name 'item'"),
        ("r = { a: lo..hi }\nlo = 1\nhi = 2", (1, 10), "write 'lo .. hi' for a range"),
        ("a = 1\nb = 2\na = 3", (3, 1), "'a' is already defined (line 1); use '/=' or '//='"),
        ("a = uint / nint\na //= (x: int)", (2, 1), "'a' is a type (line 1), so it cannot be defined "
                                             "as a group with '//='"),
        ("g = (x: int)\ng /= tstr", (2, 1), "'g' is a group (line 1), so it cannot be defined "
                                            "as a type with '/='"),
        ("$$s /= int", (1, 1), "'$$s' is a group socket: extend it with '//=', not '/='"),
        ("$t //= (x: int)", (1, 1), "'$t' is a type socket: extend it with '/=', not '//='"),
        ("a = b\nb = c\nc = a", (1, 1), "'a' is defined only in terms of itself (a -> b -> c -> a)"),
        ("a = (a)", (1, 1), "'a' is defined only in terms of itself (a -> a)"),
        # types and groups used as the other
        ("r = { a: g }\ng = (x: int)", (1, 10), "'g' is a group and cannot be used as a type"),
        ("r = { u }\nu = uint", (1, 7), "'u' is a type, so it needs a key in a map"),
        ("r = { uint }", (1, 7), "'uint' is a type, so it needs a key in a map"),
        ("r = { [uint] }", (1, 7), "a map entry needs a key"),
        ("r = $$ext", None, None),  # a group socket alone makes 'r' a group: fine
        ("r = { a: $$ext }", (1, 10), "'$$ext' is a group socket and cannot be used as a type"),
        ("r = &t\nt = uint", (1, 5), "'&t' needs a group, but 't' is a type"),
        ("r = [ ~t ]\nt = uint", (1, 7), "'~t' needs a map, array or tag, but 'uint' is not one"),
        ("r = [ ~g ]\ng = (x: int)", (1, 7), "'~g' needs a map, array or tag, but 'g' is a group"),
        # generics
        ("r = pair<int>\npair<A, B> = [A, B]", (1, 5),
         "'pair' takes 2 generic arguments (<A, B>), but is given 1"),
        ("r = box\nbox<T> = [T]", (1, 5), "'box' takes 1 generic argument (<T>), but is given 0"),
        ("r = uint<1>", (1, 5), "'uint' is not generic, but is given 1 argument(s)"),
        ("box<T> = [T<int>]", (1, 11), "generic parameter 'T' takes no arguments"),
        ("box<T, T> = [T]", (1, 1), "generic parameter 'T' is listed twice in 'box'"),
        ("box<T> = [T]\nbox /= tstr", (2, 1), "'box' is defined (line 1) with 1 generic argument"),
        ("box<T> = [T]\nr = T", (2, 5), "undefined name 'T'"),
        # controls
        ("r = tstr .sise 3", (1, 5), "unknown control operator '.sise'; did you mean '.size'?"),
        ("r = tstr .frobnicate 3", (1, 5), "unknown control operator '.frobnicate'"),
        ("r = float .size 4", (1, 5), "'.size' cannot be applied to a float"),
        ("r = { a: uint } .size 4", (1, 5), "'.size' cannot be applied to a map"),
        ("r = tstr .size -1", (1, 16), "the argument of '.size' must be an unsigned integer, "
                                       "not a negative integer"),
        ("r = uint .regexp \"a\"", (1, 5), "'.regexp' cannot be applied to an unsigned integer"),
        ("r = tstr .regexp 'a'", (1, 18), "the argument of '.regexp' must be a text string, "
                                          "not a byte string"),
        ("r = tstr .cbor uint", (1, 5), "'.cbor' cannot be applied to a text string"),
        ("r = tstr .le 5", (1, 5), "'.le' cannot be applied to a text string"),
        ("r = uint .ge \"0\"", (1, 14), "the argument of '.ge' must be"),
        ("r = text .bits flags\nflags = &(a: 0)", (1, 5), "'.bits' cannot be applied to a text"),
        ("r = n .size 1\nn = 1.5", (1, 5), "'.size' cannot be applied to a float"),
        ("r = #6.<tstr>(int)", (1, 5), "the number in '#6.<...>' or '#7.<...>' must be an "
                                       "unsigned integer type"),
        # ranges and occurrences
        ("r = 5..1", (1, 5), "range '5..1' is empty"),
        ("r = 1...1", (1, 5), "range '1...1' is empty"),
        ("r = 1..2.5", (1, 5), "range bounds must both be integers or both be floats"),
        ("r = \"a\"..\"z\"", (1, 5), "range bounds must be numbers"),
        ("r = 0..max\nmax = tstr", (1, 8), "range bound 'max' is not a number"),
        ("r = lo .. 3\nlo = 7", (1, 5), "range '7..3' is empty"),
        ("r = [ 3*2 int ]", (1, 7), "occurrence '3*2' allows no entries: 3 is more than 2"),
    ]

    def test_cases(self):
        for text, position, fragment in self.CASES:
            with self.subTest(text=text):
                if position is None:
                    resolved(text)
                    continue
                with self.assertRaises(CDDLSemanticError) as cm:
                    resolved(text)
                self.assertIn(fragment, cm.exception.message)
                self.assertEqual((cm.exception.line, cm.exception.column), position,
                                 str(cm.exception))

    def test_error_is_a_schema_error_with_location(self):
        with self.assertRaises(SchemaError) as cm:
            CDDLParser("r = {\n  a: nope\n}", source_name="s.cddl")
        self.assertIsInstance(cm.exception, CDDLSemanticError)
        self.assertIsInstance(cm.exception, ValueError)
        self.assertEqual(str(cm.exception), "s.cddl:2:6: undefined name 'nope'")

    def test_error_pickles(self):
        with self.assertRaises(CDDLSemanticError) as cm:
            resolved("r = nope")
        copy = pickle.loads(pickle.dumps(cm.exception))
        self.assertEqual(str(copy), str(cm.exception))
        self.assertEqual((copy.line, copy.column), (1, 5))

    def test_public_api_and_cli_reject_the_schema(self):
        import os
        import subprocess
        import tempfile
        with self.assertRaisesRegex(SchemaError, "undefined name 'nope'"):
            validate("r = { a: nope }", {"a": 1})
        with self.assertRaises(SchemaError):
            Validator("r = 1\nr = 2")
        with tempfile.TemporaryDirectory() as tmp:
            schema = Path(tmp) / "bad.cddl"
            schema.write_text("r = { a: tstr .sise 3 }\n", encoding="utf-8")
            data = Path(tmp) / "d.cbor"
            data.write_bytes(b"\xa0")
            result = subprocess.run(
                [sys.executable, "-m", "cddl_verifier", str(schema), str(data)],
                capture_output=True, text=True,
                env={**os.environ,
                     "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")})
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{schema}:1:10: unknown control operator '.sise'",
                      result.stdout + result.stderr)


class TestAccepted(unittest.TestCase):
    """Schemas that are valid, though some look odd."""

    def test_valid_schemas(self):
        for text in [
            "",
            "r = { * $$ext }",                       # an empty group socket
            "r = [ * $item ]",                       # an empty type socket
            "r = { a: $kind }\n$kind /= uint\n$kind /= tstr",
            "$$ext //= (x: int)\nr = { $$ext }",     # used before it is defined
            "tree = [ uint, * tree ]",               # recursion is fine
            "a = { ? b: b }\nb = [ * a ]",
            "r = { pii }\npii = ( name: tstr )",
            "r = { g }\ng = h\nh = ( x: int )",      # 'g = h' is a group, as 'h' is
            "r = [ t ]\nt = uint",                   # a type in an array needs no key
            "r = { a: int } \na = 1",                # 'a' is a bareword key, not a reference
            "r = 1\nr = 1",                          # the same rule twice is harmless
            "uint = tstr\nr = uint",                 # a user rule may redefine the prelude
            "box<T> = [ * T ]\nr = box<uint>",
            "ent<role, ext> = { role: role, * ext }\nr = ent<$role, $$x>",
            "nonempty<M> = (M) .and ({ + any => any })\nr = nonempty<{ a: int }>",
            "r = tstr .size (1..63)\nlim = 3\ns = bstr .size lim",
            "r = uint .size 0x10 / uint .bits b\nb = &( x: 1 )",
            "r = int .ge -5 .le 5",                  # chained controls (extension)
            "r = number .ge 0.5",
            "r = tstr .regexp pat\npat = \"a+\"",
            "r = bstr .cbor { a: uint }",
            "r = tstr .b64u bstr\ns = tstr .feature \"x\"\nt = uint .default 0",
            "r = lo .. hi\nlo = 1\nhi = 2",
            "r = -1.5..1.5",
            "r = [ ~hdr, x: int ]\nhdr = [ a: int ]",
            "r = [ ~t ]\nt = #6.1([ int ])",
            "r = &g\ng = ( a: 0, b: 1 )",
            "r = [ 2*2 int, 0*0 tstr ]",
            "r = #6.<1..3>(tstr) / #7.<20..23>",
        ]:
            with self.subTest(text=text):
                resolved(text)

    def test_bundled_schemas_resolve(self):
        for path in sorted(SCHEMAS.glob("*.cddl")):
            with self.subTest(schema=path.name):
                resolve(parse_cddl(path.read_text(encoding="utf-8"), source_name=path.name))

    def test_imported_modules(self):
        # draft-ietf-cbor-cddl-modules: names from an imported module are not checked
        model = resolved(";# import rfc9393 as coswid\nr = [ coswid.tag-id, * coswid.$x ]")
        self.assertEqual(model.external, frozenset({'coswid.tag-id', 'coswid.$x'}))
        with self.assertRaisesRegex(CDDLSemanticError, "undefined name 'other.x'"):
            resolved(";# import rfc9393 as coswid\nr = other.x")
        self.assertEqual(resolved(";# include rfc9052\nr = COSE_Sign1").external,
                         frozenset({'COSE_Sign1'}))
        with self.assertRaises(CDDLSemanticError):
            resolved("; import rfc9052\nr = COSE_Sign1")  # an ordinary comment

    def test_known_controls(self):
        for op in ('size', 'bits', 'regexp', 'cbor', 'cborseq', 'within', 'and', 'lt', 'le',
                   'gt', 'ge', 'eq', 'ne', 'default', 'plus', 'cat', 'det', 'abnf', 'abnfb',
                   'feature', 'b64u', 'hex', 'json', 'join', 'printf', 'base10'):
            self.assertIn(op, KNOWN_CONTROLS)


class TestModel(unittest.TestCase):

    def test_lookup_and_kinds(self):
        model = resolved("r = { pii, a: c }\npii = ( name: tstr )\nc = uint / tstr\n"
                         "c /= bstr\ng = pii\n$$ext //= (x: int)")
        self.assertEqual(model.root, 'r')
        self.assertEqual(model.lookup('pii').kind, 'group')
        self.assertEqual(model.lookup('g').kind, 'group')
        self.assertEqual(model.kind('c'), 'type')
        self.assertEqual(model.kind('$$ext'), 'group')
        self.assertEqual(model.kind('$empty'), 'type')
        self.assertIsNone(model.kind('nope'))
        self.assertTrue(model.is_group('pii'))
        self.assertEqual(format_node(model.lookup('c').type()), 'uint / tstr / bstr')
        self.assertTrue(model.lookup('uint').prelude)
        self.assertFalse(model.lookup('c').prelude)
        self.assertEqual(format_node(model.lookup('pii').group()), 'name: tstr')
        self.assertEqual(format_node(model.lookup('g').group()), 'pii')

    def test_group_choices_from_extensions(self):
        model = resolved("d = ( a: int // b: int )\nd //= ( c: int )\nr = { d }")
        self.assertEqual(format_node(model.lookup('d').group()), 'a: int // b: int // c: int')

    def test_user_rule_shadows_prelude(self):
        model = resolved("uint = tstr\nr = uint")
        self.assertEqual(format_node(model.lookup('uint').type()), 'tstr')
        self.assertFalse(model.lookup('uint').prelude)

    def test_instantiate(self):
        model = resolved("pair<A, B> = [ first: A, second: B ]\nr = pair<uint, tstr>\n"
                         "g<T> = ( v: T )\nopt<T> = T / nil\nsized<N> = bstr .size N")
        uint, tstr = Name('uint'), Name('tstr')
        self.assertEqual(format_node(model.instantiate('pair', (uint, tstr))),
                         '[ first: uint, second: tstr ]')
        self.assertIs(model.instantiate('pair', (uint, tstr)),
                      model.instantiate('pair', (Name('uint'), Name('tstr'))))  # memoized
        self.assertIsInstance(model.instantiate('g', (uint,)), Group)
        self.assertEqual(format_node(model.instantiate('opt', (uint,))), 'uint / nil')
        # a range substituted where only a simple type fits is parenthesised
        rng = Range(IntLit(1, '1'), IntLit(4, '4'), True)
        self.assertEqual(format_node(model.instantiate('sized', (rng,))), 'bstr .size (1..4)')
        self.assertIsNone(model.instantiate('nope'))
        with self.assertRaises(ValueError):
            model.instantiate('pair', (uint,))

    def test_substitute_group_entries(self):
        entry_rule = parse_cddl("x<T> = [ * T, ~T, &T ]").rules[0]
        out = substitute(entry_rule.type, {'T': Name('m')})
        self.assertEqual(format_node(out), '[* m, ~m, &m]'.replace('[', '[ ').replace(']', ' ]'))
        out = substitute(entry_rule.type, {'T': Map(Group(()))})
        self.assertIsInstance(out, Type)

    def test_literal(self):
        model = resolved("a = b\nb = (7)\nc = uint")
        self.assertEqual(model.literal(Name('a')), IntLit(7, '7'))
        self.assertIsNone(model.literal(Name('c')))
        self.assertEqual(model.literal(Paren(Type((IntLit(1, '1'),)))), IntLit(1, '1'))


class TestTablesUseTheModel(unittest.TestCase):
    """The CDDLParser tables the validator and EDN generator read (#70)."""

    def test_rule_kind_decides_group_or_alias(self):
        p = CDDLParser("r = { a: h }\nh = ( name )\nname = tstr\n"
                       "g = ( other )\nother = ( x: int )")
        self.assertEqual(p.type_aliases['h'], 'name')
        self.assertNotIn('h', p.groups)
        self.assertEqual(p.groups['g'], ['other'])
        self.assertIs(p.resolved.lookup('g').kind, 'group')

    def test_generic_structure_rule(self):
        p = CDDLParser("r = pair<uint, tstr>\npair<A, B> = [A, B]")
        self.assertEqual(p.types['r']['element_types'], {0: 'uint', 1: 'tstr'})
        self.assertTrue(validate("r = pair<uint, tstr>\npair<A, B> = [A, B]", [1, "a"]).valid)
        self.assertFalse(validate("r = pair<uint, tstr>\npair<A, B> = [A, B]", [1, 2]).valid)

    def test_generic_field_types(self):
        schema = ("r = { p: pair<uint, tstr>, o: opt<uint>, ? n: non-empty<{ a: int }> }\n"
                  "pair<A, B> = [A, B]\nopt<T> = T / nil\n"
                  "non-empty<M> = (M) .and ({ + any => any })")
        self.assertTrue(validate(schema, {"p": [1, "a"], "o": None}).valid)
        self.assertTrue(validate(schema, {"p": [1, "a"], "o": 3, "n": {"a": 1}}).valid)
        result = validate(schema, {"p": [1, 2], "o": "x", "n": {"a": "no"}})
        self.assertFalse(result.valid)
        text = "\n".join(result.errors)
        self.assertIn("expected tstr", text)
        self.assertIn("matches none of uint / nil", text)
        self.assertIn("'a'", text)

    def test_generic_array_element(self):
        schema = "r = [ * pair<uint, tstr> ]\npair<A, B> = [A, B]"
        self.assertTrue(validate(schema, [[1, "a"], [2, "b"]]).valid)
        self.assertFalse(validate(schema, [[1, "a"], [2, 3]]).valid)

    def test_recursive_generics(self):
        # Each instance gets one synthetic structure, so building the tables
        # terminates (review of #122). An unused recursive generic is fine too.
        tree = "r = tree<uint>\ntree<T> = { v: T, ? l: tree<T> }"
        self.assertTrue(validate(tree, {"v": 1, "l": {"v": 2, "l": {"v": 3}}}).valid)
        self.assertFalse(validate(tree, {"v": 1, "l": {"v": 2, "l": {"v": "x"}}}).valid)
        CDDLParser("tree<T> = { v: T, ? l: tree<T> }")
        nested = "r = [ * tree<uint> ]\ntree<T> = [ T, * tree<T> ]"
        self.assertTrue(validate(nested, [[1], [2, [3]]]).valid)
        self.assertFalse(validate(nested, [[1], ["x"]]).valid)
        # instances whose arguments change at each level stop at a fixed depth
        CDDLParser("r = grow<uint>\ngrow<T> = { ? next: grow<[T]> }")
        CDDLParser("grow<T> = { next: grow<[T]> }")
        # a recursive choice is expanded once and then left as a reference
        # (a choice with an inline array is not checked yet: #106)
        chain = "r = list<uint>\nlist<T> = nil / [x: T, rest: list<T>]"
        self.assertEqual(CDDLParser(chain).type_aliases['r'],
                         'nil / [ x: uint, rest: nil / [ x: uint, rest: list<uint> ] ]')
        self.assertTrue(validate(chain, [1, [2, None]]).valid)

    def test_generic_alias_rule(self):
        schema = "r = opt<uint>\nopt<T> = T / nil"
        self.assertEqual(CDDLParser(schema).type_aliases['r'], 'uint / nil')
        self.assertTrue(validate(schema, None).valid)
        self.assertFalse(validate(schema, "x").valid)

    def test_generic_with_group_argument(self):
        schema = ("r = ent<$role>\nent<role> = { role: role }\n"
                  "$role /= uint\n$role /= tstr")
        p = CDDLParser(schema)
        self.assertEqual(p.types['r']['fields']['role']['type'], '$role')
        self.assertTrue(validate(schema, {"role": "x"}).valid)


if __name__ == "__main__":
    unittest.main()
