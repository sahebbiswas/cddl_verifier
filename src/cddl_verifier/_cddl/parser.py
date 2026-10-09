"""Recursive-descent parser for CDDL (RFC 8610 Appendix B, RFC 9682).

One method per grammar rule. See ``docs/CDDL_AST_DESIGN.md`` §6 and §7.
"""

from bisect import bisect_left
from typing import List, Optional, Tuple

from .ast import (AnyItem, Array, BytesLit, ChoiceFrom, Control, FloatLit, Group,
                  GroupChoice, GroupRef, GroupRule, InlineGroup, IntLit, Major, Map,
                  Member, MemberKey, Name, Occurrence, Paren, Range, Schema, Source,
                  Span, Tag, TextLit, Type, TypeRule, Unwrap, entry_type)
from .errors import CDDLSyntaxError, describe
from .lexer import Lexer, Token

#: Deepest nesting of brackets, parentheses, tags and generic arguments
#: accepted. Everything that recurses over the tree (the parser, the printer,
#: and the dataclass ``__eq__``/``__hash__``/``__repr__``) uses several Python
#: frames per level, and on Python 3.9-3.11 C calls count toward the recursion
#: limit too. ``==`` and ``repr()`` fail at about 54 levels there, so 32 keeps
#: room for the caller's own frames. Real schemas nest far less (CoRIM: ~8).
MAX_DEPTH = 32

_VALUE_KINDS = ('INT', 'FLOAT', 'TEXT', 'BYTES')
_ASSIGN = ('=', '/=', '//=')
# Tokens that, after a parenthesised group entry, show it was really a type.
_TYPE_CONTINUES = ('/', '..', '...', 'CTLOP', '=>', '^')


class Parser:
    def __init__(self, source: Source):
        self.source = source
        lexer = Lexer(source)
        self.tokens: List[Token] = lexer.tokenize()
        self.comments = lexer.comments
        self._comment_starts = [c.start for c in self.comments]
        self.pos = 0
        self.prev_end = 0
        self.depth = 0

    # ------------------------------------------------------------ helpers

    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, k: int = 1) -> Token:
        return self.tokens[min(self.pos + k, len(self.tokens) - 1)]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.kind != 'EOF':
            self.pos += 1
        self.prev_end = t.end
        return t

    def accept(self, kind: str) -> Optional[Token]:
        return self.advance() if self.tok.kind == kind else None

    def expect(self, kind: str, what: Optional[str] = None) -> Token:
        if self.tok.kind != kind:
            raise self.error(f"expected {what or repr(kind)}", (what or repr(kind),))
        return self.advance()

    def error(self, expected_msg: str, expected: Tuple[str, ...] = ()) -> CDDLSyntaxError:
        t = self.tok
        found = describe(t.kind, t.text)
        span = Span(t.start, max(t.end, t.start + 1)) if t.kind != 'EOF' else Span(t.start, t.start)
        return CDDLSyntaxError(f"{expected_msg}, found {found}", span, self.source, expected)

    def span_from(self, start: int) -> Span:
        return Span(start, self.prev_end)

    def span_since(self, start: int) -> Span:
        """Like :meth:`span_from`, but empty (not reversed) when nothing was read."""
        return Span(start, max(start, self.prev_end))

    def enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            t = self.tok
            raise CDDLSyntaxError(f"nesting deeper than {MAX_DEPTH} levels",
                                  Span(t.start, t.end), self.source)

    def leave(self):
        self.depth -= 1

    def adjacent(self, kind: str) -> bool:
        """True if the current token is *kind* with no space before it."""
        return self.tok.kind == kind and not self.tok.ws_before

    # -------------------------------------------------------------- rules

    def parse_schema(self) -> Schema:
        rules = []
        while self.tok.kind != 'EOF':
            rules.append(self.parse_rule())
        return Schema(tuple(rules), self.source, span=Span(0, len(self.source.text)))

    def parse_rule(self):
        start = self.tok.start
        name = self.expect('ID', 'a rule name').value
        params: Tuple[str, ...] = ()
        if self.adjacent('<'):
            params = self.parse_generic_params()
        if self.tok.kind not in _ASSIGN:
            raise self.error("expected '=', '/=' or '//=' after the rule name", _ASSIGN)
        assign = self.advance().kind
        if assign == '/=':
            ty = self.parse_type()
            return TypeRule(name, params, assign, ty, span=self.span_from(start))
        entry = self.parse_grpent()
        if assign == '//=':
            return GroupRule(name, params, assign, entry, span=self.span_from(start))
        ty = entry_type(entry)
        if ty is None:
            return GroupRule(name, params, assign, entry, span=self.span_from(start))
        return TypeRule(name, params, assign, ty, maybe_group=_is_single_name(ty),
                        span=self.span_from(start))

    def parse_generic_params(self) -> Tuple[str, ...]:
        self.expect('<')
        names = [self.expect('ID', 'a generic parameter name').value]
        while self.accept(','):
            names.append(self.expect('ID', 'a generic parameter name').value)
        self.expect('>', "',' or '>'")
        return tuple(names)

    def parse_generic_args(self) -> Tuple:
        self.enter()
        self.expect('<')
        args = [self.parse_type1()]
        while self.accept(','):
            args.append(self.parse_type1())
        self.expect('>', "',' or '>'")
        self.leave()
        return tuple(args)

    # -------------------------------------------------------------- types

    def parse_type(self) -> Type:
        start = self.tok.start
        alts = [self.parse_type1()]
        while self.accept('/'):
            alts.append(self.parse_type1())
        return Type(tuple(alts), span=self.span_from(start))

    def parse_type1(self):
        return self.continue_type1(self.parse_type2(), self.tok.start)

    def continue_type1(self, t2, start: int):
        """Parse an optional range or control operator after *t2*."""
        start = t2.span.start if t2.span.end else start
        kind = self.tok.kind
        if kind in ('..', '...'):
            self.advance()
            high = self.parse_type2()
            return Range(t2, high, kind == '..', span=self.span_from(start))
        if kind == 'CTLOP':
            op = self.advance().value
            arg = self.parse_type2()
            return Control(t2, op, arg, span=self.span_from(start))
        return t2

    def parse_type2(self):
        t = self.tok
        start = t.start
        kind = t.kind

        if kind == 'INT':
            self.advance()
            return IntLit(t.value, t.text, span=self.span_from(start))
        if kind == 'FLOAT':
            self.advance()
            return FloatLit(t.value, t.text, span=self.span_from(start))
        if kind == 'TEXT':
            self.advance()
            return TextLit(t.value, t.text, span=self.span_from(start))
        if kind == 'BYTES':
            self.advance()
            return BytesLit(t.value, t.text, span=self.span_from(start))

        if kind == 'ID':
            self.advance()
            args = self.parse_generic_args() if self.adjacent('<') else ()
            return Name(t.value, args, span=self.span_from(start))

        if kind == '(':
            self.enter()
            self.advance()
            ty = self.parse_type()
            self.expect(')', "')'")
            self.leave()
            return Paren(ty, span=self.span_from(start))

        if kind in ('{', '['):
            self.enter()
            self.advance()
            closer = '}' if kind == '{' else ']'
            group = self.parse_group(closer)
            self.expect(closer, repr(closer))
            self.leave()
            node = Map if kind == '{' else Array
            return node(group, span=self.span_from(start))

        if kind == '~':
            self.advance()
            name = self.expect('ID', "a name after '~'").value
            args = self.parse_generic_args() if self.adjacent('<') else ()
            return Unwrap(name, args, span=self.span_from(start))

        if kind == '&':
            self.advance()
            if self.tok.kind == '(':
                self.enter()
                self.advance()
                group = self.parse_group(')')
                self.expect(')', "')'")
                self.leave()
                return ChoiceFrom(group=group, span=self.span_from(start))
            name = self.expect('ID', "'(' or a group name after '&'").value
            args = self.parse_generic_args() if self.adjacent('<') else ()
            return ChoiceFrom(name=name, args=args, span=self.span_from(start))

        if kind == 'HASH':
            return self.parse_hash()

        raise self.error("expected a type", ('a type',))

    def parse_hash(self):
        t = self.advance()
        start = t.start
        major, number, angle = t.value
        if major is None:
            return AnyItem(span=self.span_from(start))
        head = number
        if angle:
            if major not in (6, 7):
                raise CDDLSyntaxError(f"'#{major}.<type>' is only allowed for major types 6 and 7",
                                      Span(t.start, t.end), self.source)
            self.enter()
            self.expect('<')
            head = self.parse_type()
            self.expect('>', "'>'")
            self.leave()
        if major == 6 and self.tok.kind == '(':
            self.enter()
            self.advance()
            ty = self.parse_type()
            self.expect(')', "')'")
            self.leave()
            return Tag(head, ty, span=self.span_from(start))
        return Major(major, head, span=self.span_from(start))

    # ------------------------------------------------------------- groups

    def parse_group(self, closer: str) -> Group:
        start = self.tok.start
        choices = []
        choice_start = start
        entries = []
        while True:
            kind = self.tok.kind
            if kind == closer or kind == 'EOF':
                break
            if kind == '//':
                choices.append(GroupChoice(tuple(entries), span=self.span_since(choice_start)))
                self.advance()
                choice_start = self.tok.start
                entries = []
                continue
            entry = self.parse_grpent()
            comma = self.accept(',')
            end = comma.end if comma else entry.span.end
            comment = self.trailing_comment(end)
            if comment is not None:
                entry = type(entry)(**{**_fields(entry), 'comment': comment})
            entries.append(entry)
        choices.append(GroupChoice(tuple(entries), span=self.span_since(choice_start)))
        return Group(tuple(choices), span=self.span_since(start))

    def parse_grpent(self):
        start = self.tok.start
        occurrence = None
        if self.tok.kind == 'OCCUR':
            t = self.advance()
            low, high = t.value
            occurrence = Occurrence(low, high, t.text, span=Span(t.start, t.end))

        t, nxt = self.tok, self.peek()
        if nxt.kind == ':' and t.kind in ('ID',) + _VALUE_KINDS:
            key_start = t.start
            if t.kind == 'ID':
                self.advance()
                key = MemberKey('bareword', name=t.value)
            else:
                value = self.parse_type2()
                key = MemberKey('value', value=value)
            self.advance()  # ':'
            key = MemberKey(key.kind, name=key.name, value=key.value,
                            span=self.span_from(key_start))
            ty = self.parse_type()
            return Member(occurrence, key, ty, span=self.span_from(start))

        if t.kind == '(':
            self.enter()
            self.advance()
            group = self.parse_group(')')
            self.expect(')', "')'")
            self.leave()
            if self.tok.kind in _TYPE_CONTINUES:
                inner = entry_type(InlineGroup(None, group))
                if inner is None:
                    raise CDDLSyntaxError(
                        "a parenthesised group can't be used as a type",
                        Span(t.start, self.prev_end), self.source)
                paren = Paren(inner.alternatives[0].type, span=self.span_from(t.start))
                t1 = self.continue_type1(paren, t.start)
                return self.finish_entry(occurrence, t1, start)
            return InlineGroup(occurrence, group, span=self.span_from(start))

        t1 = self.parse_type1()
        return self.finish_entry(occurrence, t1, start)

    def finish_entry(self, occurrence, t1, start: int):
        """After a type1: a ``=>`` member key, more alternatives, or a group name."""
        if self.tok.kind in ('^', '=>'):
            cut = bool(self.accept('^'))
            self.expect('=>', "'=>'")
            key = MemberKey('type', cut=cut, type=t1, span=self.span_from(t1.span.start))
            ty = self.parse_type()
            return Member(occurrence, key, ty, span=self.span_from(start))
        alts = [t1]
        while self.accept('/'):
            alts.append(self.parse_type1())
        if len(alts) == 1 and isinstance(t1, Name):
            return GroupRef(occurrence, t1.name, t1.args, span=self.span_from(start))
        ty = Type(tuple(alts), span=self.span_from(t1.span.start))
        return Member(occurrence, None, ty, span=self.span_from(start))

    def trailing_comment(self, end: int) -> Optional[str]:
        """The comment on the same line after *end*, with nothing but spaces between."""
        idx = bisect_left(self._comment_starts, end)
        if idx >= len(self.comments):
            return None
        comment = self.comments[idx]
        between = self.source.text[end:comment.start]
        if between.strip(' \t') or not comment.text:
            return None
        return comment.text


def _fields(node) -> dict:
    return {name: getattr(node, name) for name in node.__dataclass_fields__}


def _is_single_name(ty: Type) -> bool:
    if len(ty.alternatives) != 1:
        return False
    alt = ty.alternatives[0]
    if isinstance(alt, Paren):
        return _is_single_name(alt.type)
    return isinstance(alt, Name)


def parse_cddl(text: str, *, source_name: Optional[str] = None) -> Schema:
    """Parse CDDL *text* into a :class:`Schema`.

    Raises :class:`CDDLSyntaxError` (a ``SchemaError``) for text that does not
    match the grammar. An empty schema, or one holding only comments, gives
    ``Schema(rules=())``.
    """
    if not isinstance(text, str):
        raise TypeError(f"CDDL text must be str, not {type(text).__name__}")
    return Parser(Source(text, source_name)).parse_schema()
