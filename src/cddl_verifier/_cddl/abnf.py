"""ABNF (RFC 5234, with the case-sensitive strings of RFC 7405) for ``.abnf`` and ``.abnfb`` (RFC 9165 section 3).

The controller of ``.abnf`` is one *element* followed by the *rules* it refers to,
separated by a newline: ``"a\\na = *\\"a\\" *\\"b\\"\\n"`` is the language of ``a``. A
single linefeed counts as a newline, as RFC 9165 says. :func:`compile_abnf` reads
such a text and :meth:`Grammar.matches` says whether a sequence of symbols (the
code points of a text string for ``.abnf``, the bytes of a string for ``.abnfb``)
is in the language.

Matching follows every way the grammar can read the input at once, as the set of
positions each part can reach, so a grammar that is ambiguous or recursive (even
left recursive) is handled and no input makes it take exponential time. The work
is still bounded (:data:`MAX_STEPS`): a grammar and an input that need more raise
:class:`AbnfLimit` instead of stalling the validator.
"""

import re
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

#: How many grammar nodes one match may visit before it is abandoned.
MAX_STEPS = 2_000_000

#: Longest controller text accepted (a limit on the work of reading it).
MAX_TEXT = 1 << 20


class AbnfError(ValueError):
    """The ABNF text is not valid ABNF (or uses prose, which cannot be matched)."""


class AbnfLimit(RuntimeError):
    """A match needed more work than :data:`MAX_STEPS` allows."""


# --------------------------------------------------------------------- nodes
#
# ('alt', [node, ...])           alternatives
# ('seq', [node, ...])           concatenation
# ('rep', min, max, node)        repetition (max None: unbounded)
# ('rule', name)                 rule reference, name in lower case
# ('str', text, sensitive)       char-val
# ('range', low, high)           num-val range
# ('chars', [code, ...])         num-val concatenation (%x61.62.63)

_TOKEN = re.compile(r'''
    (?P<ws>[ \t]+)
  | (?P<comment>;[^\n]*)
  | (?P<nl>\r?\n)
  | (?P<defined>=/|=)
  | (?P<str>(?:%[si])?"[^"\n]*")
  | (?P<prose><[^>\n]*>)
  | (?P<num>%[xXdDbB][0-9A-Fa-f]+(?:-[0-9A-Fa-f]+|(?:\.[0-9A-Fa-f]+)*))
  | (?P<repeat>[0-9]*\*[0-9]*|[0-9]+)
  | (?P<name>[A-Za-z][A-Za-z0-9-]*)
  | (?P<punct>[/()\[\]])
''', re.VERBOSE)

_BASES = {'x': 16, 'd': 10, 'b': 2}
_DIGITS = {16: '0123456789abcdefABCDEF', 10: '0123456789', 2: '01'}


def _tokens(text: str) -> List[Tuple[str, str]]:
    """Split ABNF text into tokens (names, numbers, strings, operators, line breaks)."""
    result: List[Tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            line = text.count('\n', 0, pos) + 1
            raise AbnfError(f"unexpected {text[pos]!r} in line {line}")
        kind = match.lastgroup
        if kind not in ('ws', 'comment'):
            result.append((kind, match.group()))
        pos = match.end()
    return result


class _Parser:
    """Recursive-descent reader of ABNF text (RFC 5234 section 4, with RFC 7405 strings)."""
    def __init__(self, text: str):
        """Set up the object."""
        self.tokens = _tokens(text)
        self.pos = 0

    def peek(self, offset: int = 0) -> Tuple[str, str]:
        """The next token, without consuming it."""
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else ('end', '')

    def skip_newlines(self):
        """Skip line breaks and comments between rules."""
        while self.peek()[0] == 'nl':
            self.pos += 1

    def at_rule_start(self) -> bool:
        """``name =`` or ``name =/`` is where a rule begins, wherever it is indented."""
        offset = 0
        while self.peek(offset)[0] == 'nl':
            offset += 1
        return self.peek(offset)[0] == 'name' and self.peek(offset + 1)[0] == 'defined'

    def alternation(self):
        """Read ``a / b / c``."""
        branches = [self.concatenation()]
        while self.peek_after_newlines() == ('punct', '/'):
            self.skip_newlines()
            self.pos += 1
            branches.append(self.concatenation())
        return branches[0] if len(branches) == 1 else ('alt', branches)

    def peek_after_newlines(self) -> Tuple[str, str]:
        """The next token after any line breaks, without consuming anything."""
        offset = 0
        while self.peek(offset)[0] == 'nl':
            offset += 1
        return self.peek(offset)

    def concatenation(self):
        """Read a sequence of repetitions."""
        items = []
        while True:
            if self.at_rule_start():
                break
            self.skip_newlines()
            kind, value = self.peek()
            if kind in ('end',) or (kind == 'punct' and value in ')]/'):
                break
            items.append(self.repetition())
        if not items:
            raise AbnfError("an element is missing")
        return items[0] if len(items) == 1 else ('seq', items)

    def repetition(self):
        """Read an element with its repeat prefix (``*``, ``1*``, ``2*3``, ``3``)."""
        kind, value = self.peek()
        low, high = 1, 1
        if kind == 'repeat':
            self.pos += 1
            if '*' in value:
                left, right = value.split('*')
                low = int(left) if left else 0
                high = int(right) if right else None
            else:
                low = high = int(value)
            if high is not None and low > high:
                raise AbnfError(f"repetition {value!r} allows no repeats")
        node = self.element()
        return node if (low, high) == (1, 1) else ('rep', low, high, node)

    def element(self):
        """Read a rule name, group, option, string, or numeric value."""
        kind, value = self.peek()
        self.pos += 1
        if kind == 'name':
            return ('rule', value.lower())
        if kind == 'str':
            sensitive = False
            if value[0] == '%':
                sensitive = value[1] == 's'
                value = value[2:]
            text = value[1:-1]
            if any(ord(c) > 0x7E or (ord(c) < 0x20 and c != '\t') for c in text):
                raise AbnfError(f"the string {value} has characters ABNF does not allow in a string")
            return ('str', text, sensitive)
        if kind == 'num':
            return self.number(value)
        if kind == 'prose':
            raise AbnfError(f"prose {value} cannot be matched")
        if kind == 'punct' and value in '([':
            inner = self.alternation()
            self.skip_newlines()
            closing = ')' if value == '(' else ']'
            if self.peek() != ('punct', closing):
                raise AbnfError(f"'{value}' is not closed by '{closing}'")
            self.pos += 1
            return inner if value == '(' else ('rep', 0, 1, inner)
        raise AbnfError(f"unexpected {value!r}" if value else "the text ends inside an element")

    @staticmethod
    def number(value: str):
        """Read an integer in the base the ``%b``/``%d``/``%x`` prefix names."""
        base = _BASES[value[1].lower()]
        body = value[2:]
        for char in body:
            if char not in _DIGITS[base] and char not in '-.':
                raise AbnfError(f"{value!r} has a digit that is not base {base}")
        if '-' in body:
            low, high = body.split('-')
            low, high = int(low, base), int(high, base)
            if low > high:
                raise AbnfError(f"the range {value!r} is empty")
            return ('range', low, high)
        codes = [int(part, base) for part in body.split('.')]
        return ('chars', codes)


class Grammar:
    """The rules of an ABNF text and its start element."""

    def __init__(self, start, rules: Dict[str, object]):
        """Set up the object."""
        self.start = start
        self.rules = rules

    def matches(self, symbols: Sequence[int]) -> bool:
        """Whether the whole of *symbols* (a sequence of integers) is in the language of the start element."""
        try:
            return len(symbols) in _Matcher(self, symbols).ends(self.start, 0)
        except RecursionError:  # a grammar that recurses once per symbol, on a long string
            raise AbnfLimit("the grammar recurses too deeply for this string") from None


def compile_abnf(text: str) -> Grammar:
    """Read the controller text of ``.abnf`` / ``.abnfb``.

    Raises:
        AbnfError: The text is not one element followed by rules, a rule is
            undefined or defined twice with ``=``, or it uses prose.
    """
    if len(text) > MAX_TEXT:
        raise AbnfError("the ABNF text is too long")
    try:
        return _compile(text)
    except RecursionError:
        raise AbnfError("the ABNF text is nested too deeply") from None


@lru_cache(maxsize=256)
def _compile(text: str) -> Grammar:
    """Compile ABNF text into a Grammar; results are cached by text."""
    parser = _Parser(text)
    parser.skip_newlines()
    if parser.at_rule_start():
        raise AbnfError("the text must start with the element to match (a rule name), "
                        "then the rules, as in 'a' newline 'a = \"x\"'")
    start = parser.alternation()
    rules: Dict[str, object] = {}
    while True:
        parser.skip_newlines()
        kind, name = parser.peek()
        if kind == 'end':
            break
        if kind != 'name' or parser.peek(1)[0] != 'defined':
            raise AbnfError(f"expected a rule, found {name!r}")
        defined = parser.peek(1)[1]
        parser.pos += 2
        body = parser.alternation()
        key = name.lower()
        if defined == '=/':
            if key not in rules:
                raise AbnfError(f"'{name} =/' extends a rule that is not defined before it")
            previous = rules[key]
            rules[key] = ('alt', (previous[1] if previous[0] == 'alt' else [previous])
                          + (body[1] if body[0] == 'alt' else [body]))
        else:
            if key in rules:
                raise AbnfError(f"the rule '{name}' is defined twice")
            rules[key] = body
        parser.skip_newlines()
        if parser.peek()[0] not in ('end',) and not parser.at_rule_start():
            raise AbnfError(f"unexpected {parser.peek()[1]!r}")
    _check_references(start, rules)
    for body in rules.values():
        _check_references(body, rules)
    return Grammar(start, rules)


def _check_references(node, rules: Dict[str, object]):
    """Raise AbnfError for a rule that is used but not defined."""
    kind = node[0]
    if kind == 'rule':
        if node[1] not in rules:
            raise AbnfError(f"the rule '{node[1]}' is not defined (every rule it needs must be in the "
                            f"text; there is no implicit core rule set)")
    elif kind in ('alt', 'seq'):
        for child in node[1]:
            _check_references(child, rules)
    elif kind == 'rep':
        _check_references(node[3], rules)


# ------------------------------------------------------------------ matching

class _Matcher:
    """The set of positions each node can reach, for one input."""

    def __init__(self, grammar: Grammar, symbols: Sequence[int]):
        """Set up the object."""
        self.grammar = grammar
        self.symbols = symbols
        self.steps = 0
        self.done: Dict[Tuple[str, int], frozenset] = {}
        self.open: Dict[Tuple[str, int], frozenset] = {}
        self.reentered = set()

    def ends(self, node, start: int) -> frozenset:
        """The set of positions where this node can end, starting at *start*."""
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise AbnfLimit("the ABNF match needed too much work")
        kind = node[0]
        symbols = self.symbols
        if kind == 'str':
            text, sensitive = node[1], node[2]
            if start + len(text) > len(symbols):
                return frozenset()
            for offset, char in enumerate(text):
                got = symbols[start + offset]
                if sensitive or not char.isalpha():
                    if got != ord(char):
                        return frozenset()
                elif got not in (ord(char.lower()), ord(char.upper())):
                    return frozenset()
            return frozenset((start + len(text),))
        if kind == 'range':
            if start < len(symbols) and node[1] <= symbols[start] <= node[2]:
                return frozenset((start + 1,))
            return frozenset()
        if kind == 'chars':
            codes = node[1]
            if start + len(codes) <= len(symbols) and all(
                    symbols[start + i] == code for i, code in enumerate(codes)):
                return frozenset((start + len(codes),))
            return frozenset()
        if kind == 'alt':
            reached = set()
            for branch in node[1]:
                reached |= self.ends(branch, start)
            return frozenset(reached)
        if kind == 'seq':
            reached = {start}
            for part in node[1]:
                following = set()
                for position in reached:
                    following |= self.ends(part, position)
                reached = following
                if not reached:
                    break
            return frozenset(reached)
        if kind == 'rep':
            low, high, inner = node[1], node[2], node[3]
            reached = {start} if low == 0 else set()
            seen = set(reached)
            frontier, count = {start}, 0
            while frontier and (high is None or count < high):
                count += 1
                following = set()
                for position in frontier:
                    following |= self.ends(inner, position)
                if count >= low:
                    following -= seen   # a position reached again adds nothing
                    seen |= following
                    reached |= following
                elif following == frontier:
                    count = low - 1     # the same positions again and again until the minimum
                frontier = following
            return frozenset(reached)
        return self.rule(node[1], start)

    def rule(self, name: str, start: int) -> frozenset:
        """The compiled definition of the rule *name*."""
        key = (name, start)
        if key in self.done:
            return self.done[key]
        if key in self.open:
            # a rule that needs itself from the same position (left recursion): what is
            # known so far, and the outer visit repeats until nothing new turns up
            self.reentered.add(key)
            return self.open[key]
        self.open[key] = frozenset()
        try:
            while True:
                found = self.ends(self.grammar.rules[name], start)
                if key not in self.reentered or found <= self.open[key]:
                    break
                self.open[key] = found
                self.reentered.discard(key)
        finally:
            result = self.open.pop(key)
            self.reentered.discard(key)
        result = frozenset(result | found)
        if not self.open:  # no unfinished visit can have given a partial answer
            self.done[key] = result
        return result


def matches(text: str, symbols: Sequence[int]) -> bool:
    """Whether *symbols* is in the language of the ABNF *text* (see :func:`compile_abnf`)."""
    return compile_abnf(text).matches(symbols)
