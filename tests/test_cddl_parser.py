#!/usr/bin/env python3
"""
CDDL lexer, AST and parser (#109, Phase A of #69).

The parser runs in shadow mode: ``CDDLParser`` builds ``ast`` but nothing
reads it yet. See docs/CDDL_AST_DESIGN.md.
"""

import pickle
import sys
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import SchemaError
from cddl_verifier._analyzer import CDDLParser
from cddl_verifier._cddl import (MAX_DEPTH, CDDLSyntaxError, entry_type, format_node,
                                 parse_cddl, parse_prelude, registered_label, walk)
from cddl_verifier._cddl.ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control,
                                     FloatLit, Group, GroupChoice, GroupRef, GroupRule,
                                     InlineGroup, IntLit, Major, Map, Member, MemberKey,
                                     Name, Occurrence, Paren, Range, Span, Tag, TextLit,
                                     Type, TypeRule, Unwrap)

SCHEMAS = Path(__file__).resolve().parent.parent / "cddl-schemas"


def T(*alts):
    return Type(tuple(alts))


def N(name, *args):
    return Name(name, tuple(args))


def I(value, text=None):
    return IntLit(value, str(value) if text is None else text)


def G(*choices):
    """G([entry, ...], [entry, ...]) -> Group with one GroupChoice per list."""
    return Group(tuple(GroupChoice(tuple(c)) for c in choices))


OPT = Occurrence(0, 1, '?')
STAR = Occurrence(0, None, '*')
PLUS = Occurrence(1, None, '+')


def bare(name):
    return MemberKey('bareword', name=name)


def rule(src):
    rules = parse_cddl(src).rules
    assert len(rules) == 1, rules
    return rules[0]


def rhs(src):
    """The single alternative on the right of a one-rule type schema."""
    r = rule(src)
    assert isinstance(r, TypeRule) and len(r.type.alternatives) == 1, r
    return r.type.alternatives[0]


# RFC 8610 examples (section numbers in comments) plus RFC 9682 additions.
RFC_EXAMPLES = r'''
person = { age: int, name: tstr, employer: tstr }          ; 3.5.1
pii = ( age: int, name: tstr, employer: tstr )               ; 3.5.1
person2 = { pii }
unlimited-people = [* person]                               ; 3.4
located-samples = { sample-point: int, samples: [+ float] } ; 3.4
extensible-map-example = { ? "optional-key" ^ => int, * tstr => any }  ; 3.5.4
Geography = [ city: tstr, gpsCoordinates: GpsCoordinates ]  ; 3.4
GpsCoordinates = { longitude: uint, latitude: uint }
my_uri = #6.32(tstr) / tstr                                 ; 3.6
my-int = #0 / #1                                            ; 3.6
byte = 0..255                                               ; 3.7
ip4 = bstr .size 4                                          ; 3.8.1
label = bstr .size (1..63)
tcpflagbytes = bstr .bits flags                             ; 3.8.2
flags = &( fin: 8, syn: 9, rst: 10, psh: 11, ack: 12, urg: 13, ece: 14, cwr: 15 )
nai = tstr .regexp "[A-Za-z0-9]+@[A-Za-z0-9]+(\\.[A-Za-z0-9]+)+"  ; 3.8.3
speed = number .ge 0                                        ; 3.8.6
message<t, v> = { type: t, value: v }                       ; 3.10
messages = message<"reboot", "now"> / message<"sleep", 1..100>
attire = "bow tie" / "necktie" / "Internet attire"         ; 3.9
attire /= "swimwear"
delivery = ( street: tstr, ? number: uint, city // po-box: uint, city // per-pickup: true )
city = ( name: tstr, zip-code: uint )
delivery //= ( lat: float, long: float, drone-type: tstr )
$$tcp-option //= ( sack: [+ (left: uint, right: uint)] )
color = &colors
colors = ( red: 0, green: 1, blue: 2 )
basic-header = [ field1: int, field2: text ]
advanced-header = [ ~basic-header, field3: bytes, field4: ~time ]
oneortwo = [ 1*2 int ]
unknown-bytes = h'00 01 02'
b64 = b64'AQID'
text-bytes = 'abc'
hexfloat = 0x1.8p3
neg = -1 / -0x10 / 1.5e-3
computed-tag = #6.<1..3>(tstr)                              ; RFC 9682
simple-range = #7.<20..23>
escaped = "é\u{1F600}\""
'''


class TestTypes(unittest.TestCase):

    def test_names_and_alternatives(self):
        self.assertEqual(rule("a = tstr / uint").type, T(N('tstr'), N('uint')))

    def test_literals(self):
        self.assertEqual(rhs("a = 42"), I(42))
        self.assertEqual(rhs("a = 0x10"), I(16, '0x10'))
        self.assertEqual(rhs("a = 0b101"), I(5, '0b101'))
        self.assertEqual(rhs("a = -7"), I(-7))
        self.assertEqual(rhs("a = -0x10"), I(-16, '-0x10'))
        self.assertEqual(rhs("a = 1.5"), FloatLit(1.5, '1.5'))
        self.assertEqual(rhs("a = 1e3"), FloatLit(1000.0, '1e3'))
        self.assertEqual(rhs("a = 0x1.8p3"), FloatLit(12.0, '0x1.8p3'))
        self.assertEqual(rhs('a = "hi"'), TextLit('hi', '"hi"'))
        self.assertEqual(rhs("a = h'0aFF'"), BytesLit(b'\x0a\xff', "h'0aFF'"))
        self.assertEqual(rhs("a = b64'AQID'").value, b'\x01\x02\x03')
        self.assertEqual(rhs("a = b64'-_8'").value, b'\xfb\xff')  # base64url
        self.assertEqual(rhs("a = 'x\\'y'").value, b"x'y")
        self.assertEqual(rhs("a = 18446744073709551616").value, 2 ** 64)

    def test_text_escapes(self):
        self.assertEqual(rhs(r'a = "\"\\\/\b\f\n\r\t"').value, '"\\/\b\f\n\r\t')
        self.assertEqual(rhs(r'a = "é"').value, 'é')
        self.assertEqual(rhs(r'a = "😀"').value, '\U0001F600')
        self.assertEqual(rhs(r'a = "\u{1F600}"').value, '\U0001F600')

    def test_hex_bytes_allow_whitespace_and_comments(self):
        self.assertEqual(rhs("a = h'00 01 ; first two\n ff'").value, b'\x00\x01\xff')

    def test_range(self):
        self.assertEqual(rhs("a = 0..255"), Range(I(0), I(255), True))
        self.assertEqual(rhs("a = 0...255"), Range(I(0), I(255), False))
        self.assertEqual(rhs("a = -1.5..1.5"),
                         Range(FloatLit(-1.5, '-1.5'), FloatLit(1.5, '1.5'), True))
        self.assertEqual(rhs("a = min .. max"), Range(N('min'), N('max'), True))
        self.assertEqual(rhs("a = 0x10..0x20"), Range(I(16, '0x10'), I(32, '0x20'), True))

    def test_greedy_identifiers(self):
        # The ABNF makes '.' and '-' part of a name: write spaces around operators.
        self.assertEqual(rhs("a = min..max"), N('min..max'))
        self.assertEqual(rhs("a = tstr.size"), N('tstr.size'))

    def test_control(self):
        self.assertEqual(rhs("a = bstr .size 16"), Control(N('bstr'), 'size', I(16)))
        self.assertEqual(rhs("a = tstr .size (1..64)"),
                         Control(N('tstr'), 'size', Paren(T(Range(I(1), I(64), True)))))
        self.assertEqual(rhs('a = tstr .regexp "[a-z]+"'),
                         Control(N('tstr'), 'regexp', TextLit('[a-z]+', '"[a-z]+"')))
        self.assertEqual(rhs("a = bytes .cbor inner"), Control(N('bytes'), 'cbor', N('inner')))
        # Any control name parses; #70 decides which are known.
        self.assertEqual(rhs("a = uint .frobnicate 1"), Control(N('uint'), 'frobnicate', I(1)))

    def test_chained_controls_extension(self):
        # Not RFC 8610 (one control per type), accepted for compatibility:
        # 'uint .ge 0 .le 150' reads as '(uint .ge 0) .le 150'.
        self.assertEqual(rhs("a = uint .ge 0 .le 150"),
                         Control(Control(N('uint'), 'ge', I(0)), 'le', I(150)))
        r = rule("a = uint .ge 0 .le 150 / tstr")
        self.assertEqual(format_node(r), "a = uint .ge 0 .le 150 / tstr")
        self.assertEqual(parse_cddl(format_node(r)).rules[0], r)

    def test_paren(self):
        self.assertEqual(rhs("a = (tstr / uint) .size 2"),
                         Control(Paren(T(N('tstr'), N('uint'))), 'size', I(2)))

    def test_generic_args_need_no_space(self):
        self.assertEqual(rhs("a = b<tstr, 1..2>"), N('b', N('tstr'), Range(I(1), I(2), True)))
        r = rule("x<K, V> = { k: K, v: V }")
        self.assertEqual(r.params, ('K', 'V'))

    def test_map_and_array(self):
        self.assertEqual(rhs("a = {}"), Map(G([])))
        self.assertEqual(rhs("a = []"), Array(G([])))
        self.assertEqual(rhs("a = { b: int }"), Map(G([Member(None, bare('b'), T(N('int')))])))

    def test_unwrap_and_choice_from(self):
        self.assertEqual(rhs("a = ~b"), Unwrap('b'))
        self.assertEqual(rhs("a = &colors"), ChoiceFrom(name='colors'))
        self.assertEqual(rhs("a = & ( red: 0 )"),
                         ChoiceFrom(group=G([Member(None, bare('red'), T(I(0)))])))

    def test_tags_and_major_types(self):
        self.assertEqual(rhs("a = #6.501(m)"), Tag(501, T(N('m'))))
        self.assertEqual(rhs("a = #6(m)"), Tag(None, T(N('m'))))
        self.assertEqual(rhs("a = #6.0x10(m)"), Tag(16, T(N('m'))))
        self.assertEqual(rhs("a = #6.<1..3>(m)"), Tag(T(Range(I(1), I(3), True)), T(N('m'))))
        self.assertEqual(rhs("a = #6.1(#6.2(m))"), Tag(1, T(Tag(2, T(N('m'))))))
        self.assertEqual(rhs("a = #0"), Major(0))
        self.assertEqual(rhs("a = #7.25"), Major(7, 25))
        self.assertEqual(rhs("a = #7.<20..21>"), Major(7, T(Range(I(20), I(21), True))))
        self.assertEqual(rhs("a = #"), AnyItem())


class TestGroups(unittest.TestCase):

    def entries(self, src):
        return rhs(src).group.choices[0].entries

    def test_member_keys(self):
        b, v, s, t, c = self.entries('a = { b: int, 1: int, "s": int, tstr => int, int ^ => any }')
        self.assertEqual(b.key, bare('b'))
        self.assertEqual(v.key, MemberKey('value', value=I(1)))
        self.assertEqual(s.key, MemberKey('value', value=TextLit('s', '"s"')))
        self.assertEqual(t.key, MemberKey('type', type=N('tstr')))
        self.assertEqual(c.key, MemberKey('type', cut=True, type=N('int')))

    def test_key_literal_members(self):
        # #104: 'key => type' members
        one, two = self.entries("k = { 1 => tstr / int, ? 2 => bstr }")
        self.assertEqual(one, Member(None, MemberKey('type', type=I(1)), T(N('tstr'), N('int'))))
        self.assertEqual(two.occurrence, OPT)

    def test_occurrences(self):
        entries = self.entries("a = [ ? a, * b, + c, 1*2 d, *3 e, 2* f ]")
        self.assertEqual([e.occurrence for e in entries], [
            OPT, STAR, PLUS, Occurrence(1, 2, '1*2'), Occurrence(0, 3, '*3'),
            Occurrence(2, None, '2*')])

    def test_bare_names_are_group_refs(self):
        self.assertEqual(self.entries("a = [ + tstr ]"), (GroupRef(PLUS, 'tstr'),))
        self.assertEqual(self.entries("a = { pii, x: int }")[0], GroupRef(None, 'pii'))
        # a choice or a control is a type, not a group name
        self.assertEqual(self.entries("a = [ * tstr / int ]"),
                         (Member(STAR, None, T(N('tstr'), N('int'))),))

    def test_inline_group_and_paren_type(self):
        entries = self.entries("a = [ + (left: uint, right: uint) ]")
        self.assertEqual(entries, (InlineGroup(PLUS, G([
            Member(None, bare('left'), T(N('uint'))),
            Member(None, bare('right'), T(N('uint')))])),))
        # followed by a type operator, the parenthesis is a type
        self.assertEqual(self.entries("a = [ (a / b) .size 2 ]"),
                         (Member(None, None, T(Control(Paren(T(N('a'), N('b'))), 'size', I(2)))),))

    def test_group_choices(self):
        g = rhs("a = { x: int // y: tstr, z: uint // }").group
        self.assertEqual([len(c.entries) for c in g.choices], [1, 2, 0])

    def test_entries_without_commas(self):
        entries = self.entries("a = {\n  x: int\n  ? y: tstr\n}")
        self.assertEqual([e.key.name for e in entries], ['x', 'y'])

    def test_registered_parameters(self):
        e, = self.entries("m = { ? & ( name : 0 ) => tstr }")
        self.assertEqual(e.occurrence, OPT)
        self.assertEqual(registered_label(e), ('name', I(0)))
        self.assertIsNone(registered_label(self.entries("m = { a: int }")[0]))

    def test_trailing_comments(self):
        entries = self.entries("m = {\n  0: tstr ; name\n  ? 1: uint, ; age\n"
                               "  2: { a: int } ; outer\n  ; own line\n  3: int\n}")
        self.assertEqual([e.comment for e in entries], ['name', 'age', 'outer', None])
        inner = entries[2].value.alternatives[0].group.choices[0].entries[0]
        self.assertIsNone(inner.comment)

    def test_comments_do_not_affect_equality(self):
        self.assertEqual(parse_cddl("m = { a: int ; x\n}"), parse_cddl("m = { a: int }"))


class TestRules(unittest.TestCase):

    def test_type_and_group_rules(self):
        self.assertEqual(rule("a = int"), TypeRule('a', (), '=', T(N('int')), maybe_group=True))
        self.assertEqual(rule("a = (b)"),
                         TypeRule('a', (), '=', T(Paren(T(N('b')))), maybe_group=True))
        self.assertFalse(rule("a = int / tstr").maybe_group)
        self.assertEqual(rule("a = (x: int, y: int)"), GroupRule('a', (), '=', InlineGroup(None, G([
            Member(None, bare('x'), T(N('int'))), Member(None, bare('y'), T(N('int')))]))))
        self.assertIsInstance(rule("a = x: int"), GroupRule)
        self.assertIsInstance(rule("a = * b"), GroupRule)

    def test_choice_assignments(self):
        self.assertEqual(rule("$a /= int / tstr"), TypeRule('$a', (), '/=', T(N('int'), N('tstr'))))
        self.assertEqual(rule("$$b //= c: int"),
                         GroupRule('$$b', (), '//=', Member(None, bare('c'), T(N('int')))))
        # an IANA-style choice of a registered value
        self.assertIsInstance(rule("$role /= & ( admin : 0 )").type.alternatives[0], ChoiceFrom)

    def test_rules_need_no_separator(self):
        names = [r.name for r in parse_cddl("a = b c = d\ne = { f: g } h = i").rules]
        self.assertEqual(names, ['a', 'c', 'e', 'h'])

    def test_empty_schema(self):
        for text in ("", "  \n", "; only a comment\n"):
            with self.subTest(text=text):
                self.assertEqual(parse_cddl(text).rules, ())

    def test_entry_type(self):
        self.assertEqual(entry_type(GroupRef(None, 'x')), T(N('x')))
        self.assertIsNone(entry_type(GroupRef(OPT, 'x')))
        self.assertIsNone(entry_type(Member(None, bare('a'), T(N('x')))))


class TestSpans(unittest.TestCase):

    def test_line_and_column(self):
        schema = parse_cddl("a = int\n\nperson = {\n  name: tstr,\n  ? age: uint\n}\n")
        source = schema.source
        person = schema.rules[1]
        self.assertEqual(source.line_col(person.span.start), (3, 1))
        self.assertEqual(source.text[person.span.start:person.span.end].splitlines()[-1], '}')
        age = person.type.alternatives[0].group.choices[0].entries[1]
        self.assertEqual(source.line_col(age.span.start), (5, 3))
        self.assertEqual(source.text[age.span.start:age.span.end], '? age: uint')

    def test_crlf_and_cr(self):
        schema = parse_cddl("a = int\r\nb = {\r\n  c: tstr\r\n}\rd = uint")
        lc = [schema.source.line_col(r.span.start) for r in schema.rules]
        self.assertEqual(lc, [(1, 1), (2, 1), (5, 1)])

    def test_spans_cover_the_node(self):
        text = 'm = { ? &(a: 0) => tstr .size 2 }'
        schema = parse_cddl(text)
        for node in walk(schema):
            span = node.span
            self.assertLessEqual(span.start, span.end, node)
            if isinstance(node, Control):
                self.assertEqual(text[span.start:span.end], 'tstr .size 2')
            if isinstance(node, MemberKey) and node.kind == 'type':
                self.assertEqual(text[span.start:span.end], '&(a: 0) =>')

    def test_spans_excluded_from_equality(self):
        self.assertEqual(parse_cddl("a = int"), parse_cddl("\n\n   a   =   int"))


class TestErrors(unittest.TestCase):

    CASES = [
        # (schema, message fragment, line, column)
        ("r = {", "expected '}', found end of input", 1, 6),
        ("r = [ a: int", "expected ']'", 1, 13),
        ("r = (int", "expected ')'", 1, 9),
        ("r = ", "expected a type, found end of input", 1, 5),
        ("r", "expected '=', '/=' or '//=' after the rule name", 1, 2),
        ("= int", "expected a rule name", 1, 1),
        ("r = { a: }", "expected a type, found '}'", 1, 10),
        ("r = { a: int, => }", "expected a type, found '=>'", 1, 15),
        ("r = int\n  x", "expected '=', '/=' or '//=' after the rule name", 2, 4),
        ('r = "abc', "unterminated text string", 1, 5),
        ('r = "a\nb"', "unterminated text string", 1, 5),
        (r'r = "\q"', "unknown escape", 1, 6),
        (r'r = "\ud800"', "unpaired surrogate", 1, 6),
        ("r = h'0g'", "invalid h'' byte string", 1, 5),
        ("r = h'abc'", "invalid h'' byte string", 1, 5),
        ("r = 'abc", "unterminated byte string", 1, 5),
        ("r = 0x", "expected hex digits after '0x'", 1, 5),
        ("r = 0x1.8", "hex float needs a 'p' exponent", 1, 5),
        ("r = #6.(m)", "expected a number or '<' after '#6.'", 1, 5),
        ("r = #2.<1>", "only allowed for major types 6 and 7", 1, 5),
        ("r = a<b", "expected ',' or '>'", 1, 8),
        ("r = !", "unexpected character '!'", 1, 5),
        ("r = - 1", "expected a digit after '-'", 1, 5),
        ("x<A B> = int", "expected ',' or '>'", 1, 5),
        ("r = (a: int) .size 2", "can't be used as a type", 1, 5),
        ("r = { a: uint ?, }", "expected a type, found ','", 1, 16),
    ]

    def test_errors_report_position(self):
        for text, fragment, line, col in self.CASES:
            with self.subTest(text=text):
                with self.assertRaises(CDDLSyntaxError) as cm:
                    parse_cddl(text, source_name='s.cddl')
                err = cm.exception
                self.assertIn(fragment, err.message)
                self.assertEqual((err.line, err.column), (line, col), err)
                self.assertTrue(str(err).startswith(f's.cddl:{line}:{col}: '), str(err))

    def test_error_is_a_schema_error(self):
        with self.assertRaises(SchemaError):
            parse_cddl("r = {")
        with self.assertRaises(ValueError):
            parse_cddl("r = {")

    def test_expected_tokens(self):
        with self.assertRaises(CDDLSyntaxError) as cm:
            parse_cddl("r")
        self.assertEqual(cm.exception.expected, ('=', '/=', '//='))

    def test_unnamed_source(self):
        with self.assertRaises(CDDLSyntaxError) as cm:
            parse_cddl("r = {")
        self.assertTrue(str(cm.exception).startswith('<schema>:1:6: '))

    def test_error_pickles(self):
        with self.assertRaises(CDDLSyntaxError) as cm:
            parse_cddl("r = {")
        copy = pickle.loads(pickle.dumps(cm.exception))
        self.assertEqual(str(copy), str(cm.exception))

    # (opener, closer, nesting levels per repetition)
    NESTINGS = [("[", "]", 1), ("{ a: ", "}", 1), ("(", ")", 1), ("#6.1(", ")", 1),
                ("x<", ">", 1), ("[ + (", ") ]", 2), ("{ a: [", "] }", 2)]

    def test_trees_at_the_limit_are_usable(self):
        # Everything that recurses over the tree must work at MAX_DEPTH.
        for opener, closer, per in self.NESTINGS:
            n = MAX_DEPTH // per
            text = "r = " + opener * n + "int" + closer * n
            with self.subTest(opener=opener):
                schema = parse_cddl(text)
                self.assertEqual(parse_cddl(text), schema)
                hash(schema.rules[0].type)
                repr(schema)
                self.assertGreater(len(list(walk(schema))), n)
                self.assertEqual(parse_cddl(format_node(schema)), schema)

    def test_chained_controls_count_toward_the_limit(self):
        ok = parse_cddl("r = uint" + " .ge 0" * MAX_DEPTH)
        self.assertEqual(parse_cddl(format_node(ok)), ok)
        repr(ok)
        with self.assertRaises(CDDLSyntaxError) as cm:
            parse_cddl("r = uint" + " .ge 0" * (MAX_DEPTH + 2))
        self.assertIn("nesting deeper than", cm.exception.message)

    def test_nesting_limit(self):
        for opener, closer in (("[", "]"), ("{ a: ", "}"), ("(", ")"), ("#6.1(", ")"), ("x<", ">")):
            deep = "r = " + opener * (MAX_DEPTH + 1) + "int" + closer * (MAX_DEPTH + 1)
            with self.subTest(opener=opener):
                with self.assertRaises(CDDLSyntaxError) as cm:
                    parse_cddl(deep)
                self.assertIn("nesting deeper than", cm.exception.message)

    def test_very_deep_input_never_raises_recursion_error(self):
        with self.assertRaises(CDDLSyntaxError):
            parse_cddl("r = " + "[" * 5000)

    def test_non_string_input(self):
        with self.assertRaises(TypeError):
            parse_cddl(b"r = int")


class TestCorpus(unittest.TestCase):

    def corpus(self):
        for path in sorted(SCHEMAS.glob("*.cddl")):
            with open(path, encoding="utf-8", newline="") as f:  # keep CRLF
                yield path.name, f.read()
        yield "rfc-examples", RFC_EXAMPLES
        yield "<prelude>", parse_prelude().source.text

    def test_corpus_parses(self):
        for name, text in self.corpus():
            with self.subTest(schema=name):
                self.assertTrue(parse_cddl(text, source_name=name).rules)

    def test_printer_round_trip(self):
        for name, text in self.corpus():
            with self.subTest(schema=name):
                schema = parse_cddl(text)
                printed = format_node(schema)
                self.assertEqual(parse_cddl(printed), schema)
                # canonical form is a fixed point
                self.assertEqual(format_node(parse_cddl(printed)), printed)

    def test_rfc_examples(self):
        rules = {r.name: r for r in parse_cddl(RFC_EXAMPLES).rules if r.assign == '='}
        self.assertEqual(format_node(rules['extensible-map-example']),
                         'extensible-map-example = { ? "optional-key" ^ => int, * tstr => any }')
        self.assertEqual(rules['message'].params, ('t', 'v'))
        self.assertIsInstance(rules['pii'], GroupRule)
        self.assertEqual(rules['oneortwo'].type.alternatives[0].group.choices[0].entries[0].occurrence,
                         Occurrence(1, 2, '1*2'))
        self.assertEqual(rules['escaped'].type.alternatives[0].value, 'é\U0001F600"')

    def test_prelude(self):
        prelude = {r.name: r for r in parse_prelude().rules}
        self.assertEqual(prelude['uint'].type, T(Major(0)))
        self.assertEqual(prelude['tdate'].type, T(Tag(0, T(N('tstr')))))
        self.assertEqual(prelude['bool'].type, T(N('false'), N('true')))
        self.assertIs(parse_prelude(), parse_prelude())

    def test_bundled_corim_constructs(self):
        with open(SCHEMAS / "unified.cddl", encoding="utf-8", newline="") as f:
            rules = {r.name: r for r in parse_cddl(f.read()).rules}
        self.assertEqual(format_node(rules['non-empty']),
                         'non-empty<M> = (M) .and ({ + any => any })')
        corim_map = rules['corim-map'].type.alternatives[0]
        first = corim_map.group.choices[0].entries[0]
        self.assertEqual(registered_label(first), ('id', I(0)))
        cose_key = rules['COSE_Key'].type.alternatives[0].group.choices[0].entries
        self.assertEqual(cose_key[-1], Member(STAR, MemberKey('type', type=N('cose-label')),
                                              T(N('cose-value'))))


class TestStrictParsing(unittest.TestCase):
    """Phase B (#110): CDDLParser is built from the AST and rejects bad CDDL."""

    def test_ast_built_for_valid_schema(self):
        parser = CDDLParser("person = { name: tstr }")
        self.assertEqual(parser.ast.rules[0].name, 'person')
        self.assertIn('person', parser.types)

    def test_parser_raises_with_position(self):
        with self.assertRaises(CDDLSyntaxError) as cm:
            CDDLParser("a = int\nr = {\n  b: \n}", source_name="s.cddl")
        self.assertEqual((cm.exception.line, cm.exception.column), (4, 1))
        self.assertTrue(str(cm.exception).startswith("s.cddl:4:1: "))

    def test_bundled_schemas_parse(self):
        for path in sorted(SCHEMAS.glob("*.cddl")):
            with self.subTest(schema=path.name):
                self.assertIsNotNone(CDDLParser(path.read_text(encoding="utf-8")).ast)

    def test_public_api_raises_schema_error(self):
        from cddl_verifier import Validator, validate
        with self.assertRaises(SchemaError) as cm:
            validate("r = { a: uint ?, }", {"a": 1})
        self.assertEqual((cm.exception.line, cm.exception.column), (1, 16))
        self.assertIn("<schema>:1:16: expected a type", str(cm.exception))
        with self.assertRaises(SchemaError):
            Validator("r = {")

    def test_public_api_names_the_schema_file(self):
        import tempfile
        from cddl_verifier import Validator
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.cddl"
            path.write_text("r = [ a: int", encoding="utf-8")
            with self.assertRaises(SchemaError) as cm:
                Validator(path)
        self.assertTrue(str(cm.exception).startswith(f"{path}:1:13: "), str(cm.exception))

    def test_cli_reports_syntax_error(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            schema = Path(tmp) / "bad.cddl"
            schema.write_text("r = {\n  a: \n}\n", encoding="utf-8")
            data = Path(tmp) / "d.cbor"
            data.write_bytes(b"\xa0")
            result = subprocess.run(
                [sys.executable, "-m", "cddl_verifier", str(schema), str(data)],
                capture_output=True, text=True,
                env={**__import__("os").environ,
                     "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")})
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{schema}:3:1: expected a type", result.stdout + result.stderr)

    def test_non_standard_forms_are_rejected(self):
        # Accepted by the line-based parser before #110; see PR #114.
        for text in ("r = { &(a: 0) => uint ? }",     # '?' after the type
                     "r = #6.1([",                   # unterminated rule
                     "any = *"):                     # occurrence without a type
            with self.subTest(text=text):
                with self.assertRaises(CDDLSyntaxError):
                    CDDLParser(text)


if __name__ == "__main__":
    unittest.main()
