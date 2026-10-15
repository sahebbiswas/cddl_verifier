"""
The regular expression dialect of ``.regexp`` (RFC 8610 section 3.8, #182).

RFC 8610 says a ``.regexp`` pattern is an XML Schema (XSD) regular expression
(XSD 1.0 Part 2, appendix F), which is not the dialect of Python's ``re``:

* a pattern always matches the whole text, and ``^`` and ``$`` are ordinary
  characters;
* ``\\d`` is any decimal digit of Unicode (``\\p{Nd}``), ``\\w`` is every
  character except punctuation, separators and "other" (``[^\\p{P}\\p{Z}\\p{C}]``),
  ``\\s`` is only space, tab, line feed and carriage return;
* ``\\p{L}``, ``\\P{Nd}`` and ``\\p{IsGreek}`` name Unicode categories and
  blocks; ``\\i`` and ``\\c`` are XML name characters;
* a class can subtract another: ``[a-z-[aeiou]]``;
* ``.`` is any character except line feed and carriage return;
* there are no lazy quantifiers, back references, look-arounds, flags or
  non-capturing groups.

``compile_pattern`` parses an XSD pattern and builds a Thompson NFA; ``fullmatch``
runs it. Every character class is computed as a set of code points, so negation,
subtraction and the multi-character escapes combine as XSD says. A construct XSD
does not have, or that this module does not cover, raises ``XsdRegexError`` naming
it.

Matching is linear in the length of the text (#185). Python's ``re`` backtracks, so
``(a+)+b`` or ``(a|a)*b`` against a long text of ``a`` takes exponential time. The
NFA is simulated one character at a time over the set of live states instead, so the
work is at most (text length) x (states), and both are bounded: a pattern that needs
more than ``_MAX_STATES`` states is rejected as a pattern, and a match that would take
more than ``MATCH_BUDGET`` steps raises ``XsdRegexLimit``.

What is covered: the whole XSD 1.0 grammar, with the Unicode categories of the
running Python (``unicodedata``) and the Unicode blocks in ``_BLOCKS``. Other
blocks are reported as unsupported rather than guessed.
"""

import re
import unicodedata
from bisect import bisect_right
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

__all__ = ['XsdRegexError', 'XsdRegexLimit', 'MATCH_BUDGET', 'compile_pattern', 'fullmatch']

_MAX_CODE_POINT = 0x10FFFF
_MAX_DEPTH = 64
_MAX_REPEAT = 1000
_MAX_STATES = 50_000

#: Most (live states x characters) steps one match may take. A realistic pattern keeps a
#: handful of states live, so this allows texts of millions of characters; a pattern built
#: to keep thousands of states live on a long text is stopped here.
MATCH_BUDGET = 10_000_000

Ranges = List[Tuple[int, int]]


class XsdRegexError(ValueError):
    """A pattern that is not valid XSD, or that uses something not supported."""


class XsdRegexLimit(XsdRegexError):
    """The pattern is valid, but matching this text would exceed ``MATCH_BUDGET``."""


# ----------------------------------------------------------- code point sets

def _normalize(ranges) -> Ranges:
    merged: Ranges = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            if hi > merged[-1][1]:
                merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


def _complement(ranges: Ranges) -> Ranges:
    result: Ranges = []
    nxt = 0
    for lo, hi in _normalize(ranges):
        if lo > nxt:
            result.append((nxt, lo - 1))
        nxt = hi + 1
    if nxt <= _MAX_CODE_POINT:
        result.append((nxt, _MAX_CODE_POINT))
    return result


def _subtract(ranges: Ranges, taken: Ranges) -> Ranges:
    """The code points in *ranges* that are not in *taken*."""
    keep = _complement(taken)
    result: Ranges = []
    for lo, hi in _normalize(ranges):
        for klo, khi in keep:
            if khi < lo:
                continue
            if klo > hi:
                break
            result.append((max(lo, klo), min(hi, khi)))
    return _normalize(result)


@lru_cache(maxsize=1)
def _categories() -> Dict[str, Ranges]:
    """``category -> code points`` from the running Python's Unicode database."""
    table: Dict[str, Ranges] = {}
    start = 0
    current = unicodedata.category(chr(0))
    for code in range(1, _MAX_CODE_POINT + 2):
        cat = unicodedata.category(chr(code)) if code <= _MAX_CODE_POINT else None
        if cat != current:
            table.setdefault(current, []).append((start, code - 1))
            start, current = code, cat
    return table


def _category(name: str) -> Ranges:
    table = _categories()
    if len(name) == 1:
        return _normalize([r for cat, rs in table.items() if cat[0] == name for r in rs])
    if name in table:
        return list(table[name])
    # a valid XSD category that this Unicode database never assigns (Cs on some builds)
    if name in _CATEGORY_NAMES:
        return []
    raise XsdRegexError(f"unknown Unicode category \\p{{{name}}}")


_CATEGORY_NAMES = frozenset(
    'L Lu Ll Lt Lm Lo M Mn Mc Me N Nd Nl No P Pc Pd Ps Pe Pi Pf Po Z Zs Zl Zp '
    'S Sm Sc Sk So C Cc Cf Co Cn'.split())

#: Unicode blocks by their XSD name (``\p{IsGreek}``): the common ones.
_BLOCKS: Dict[str, Tuple[int, int]] = {
    'BasicLatin': (0x0000, 0x007F), 'Latin-1Supplement': (0x0080, 0x00FF),
    'LatinExtended-A': (0x0100, 0x017F), 'LatinExtended-B': (0x0180, 0x024F),
    'IPAExtensions': (0x0250, 0x02AF), 'SpacingModifierLetters': (0x02B0, 0x02FF),
    'CombiningDiacriticalMarks': (0x0300, 0x036F), 'Greek': (0x0370, 0x03FF),
    'Cyrillic': (0x0400, 0x04FF), 'Armenian': (0x0530, 0x058F), 'Hebrew': (0x0590, 0x05FF),
    'Arabic': (0x0600, 0x06FF), 'Devanagari': (0x0900, 0x097F), 'Thai': (0x0E00, 0x0E7F),
    'Georgian': (0x10A0, 0x10FF), 'HangulJamo': (0x1100, 0x11FF),
    'LatinExtendedAdditional': (0x1E00, 0x1EFF), 'GreekExtended': (0x1F00, 0x1FFF),
    'GeneralPunctuation': (0x2000, 0x206F), 'SuperscriptsandSubscripts': (0x2070, 0x209F),
    'CurrencySymbols': (0x20A0, 0x20CF), 'LetterlikeSymbols': (0x2100, 0x214F),
    'NumberForms': (0x2150, 0x218F), 'Arrows': (0x2190, 0x21FF),
    'MathematicalOperators': (0x2200, 0x22FF), 'BoxDrawing': (0x2500, 0x257F),
    'GeometricShapes': (0x25A0, 0x25FF), 'MiscellaneousSymbols': (0x2600, 0x26FF),
    'Dingbats': (0x2700, 0x27BF), 'CJKSymbolsandPunctuation': (0x3000, 0x303F),
    'Hiragana': (0x3040, 0x309F), 'Katakana': (0x30A0, 0x30FF), 'Bopomofo': (0x3100, 0x312F),
    'CJKUnifiedIdeographs': (0x4E00, 0x9FFF), 'HangulSyllables': (0xAC00, 0xD7AF),
    'PrivateUse': (0xE000, 0xF8FF), 'CJKCompatibilityIdeographs': (0xF900, 0xFAFF),
    'AlphabeticPresentationForms': (0xFB00, 0xFB4F),
    'HalfwidthandFullwidthForms': (0xFF00, 0xFFEF), 'Specials': (0xFFF0, 0xFFFD),
}

_SPACE: Ranges = [(0x09, 0x0A), (0x0D, 0x0D), (0x20, 0x20)]

# XML 1.0 (fifth edition) NameStartChar and NameChar, which XSD 1.1 uses for \i and \c
_NAME_START: Ranges = _normalize([
    (0x3A, 0x3A), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A), (0xC0, 0xD6), (0xD8, 0xF6),
    (0xF8, 0x2FF), (0x370, 0x37D), (0x37F, 0x1FFF), (0x200C, 0x200D), (0x2070, 0x218F),
    (0x2C00, 0x2FEF), (0x3001, 0xD7FF), (0xF900, 0xFDCF), (0xFDF0, 0xFFFD),
    (0x10000, 0xEFFFF)])
_NAME_CHAR: Ranges = _normalize(_NAME_START + [
    (0x2D, 0x2E), (0x30, 0x39), (0xB7, 0xB7), (0x300, 0x36F), (0x203F, 0x2040)])


def _word() -> Ranges:
    """``\\w``: everything except punctuation, separators and "other"."""
    return _complement(_category('P') + _category('Z') + _category('C'))


def _multi(letter: str) -> Ranges:
    """The code points of the escape ``\\<letter>`` (``s``, ``d``, ``w``, ``i``, ``c`` and their upper case)."""
    base = {'s': lambda: _SPACE, 'd': lambda: _category('Nd'), 'w': _word,
            'i': lambda: _NAME_START, 'c': lambda: _NAME_CHAR}[letter.lower()]()
    return _complement(base) if letter.isupper() else list(base)


# ------------------------------------------------------------------ syntax tree
# ('set', ranges)            one character from the code points
# ('cat', [node, ...])       a sequence (empty: matches the empty text)
# ('alt', [node, ...])       a choice
# ('rep', node, low, high)   low..high repeats, high None for no limit

def _set(ranges: Ranges):
    """A character class node. The bound lists are made once here, so every copy of a
    repeated body in the NFA shares them (``\\W{1000}`` has 770 ranges per state)."""
    ranges = _normalize(ranges)
    return ('set', ranges, [lo for lo, _ in ranges], [hi for _, hi in ranges])


# ----------------------------------------------------------------- parsing

_SINGLE_ESCAPES = {'n': 0x0A, 'r': 0x0D, 't': 0x09}
_METACHARS = '\\|.-^?*+{}()[]'


class _Parser:
    def __init__(self, pattern: str):
        self.text = pattern
        self.pos = 0

    def error(self, message: str, pos: Optional[int] = None) -> XsdRegexError:
        at = self.pos if pos is None else pos
        return XsdRegexError(f"{message} (at position {at} of /{self.text}/)")

    def peek(self) -> Optional[str]:
        return self.text[self.pos] if self.pos < len(self.text) else None

    def parse(self):
        out = self.regexp(0)
        if self.pos < len(self.text):
            raise self.error(f"unmatched '{self.text[self.pos]}'")
        return out

    def regexp(self, depth: int):
        if depth > _MAX_DEPTH:
            raise self.error(f"groups are nested more than {_MAX_DEPTH} deep")
        branches = [self.branch(depth)]
        while self.peek() == '|':
            self.pos += 1
            branches.append(self.branch(depth))
        return branches[0] if len(branches) == 1 else ('alt', branches)

    def branch(self, depth: int):
        pieces = []
        while self.peek() is not None and self.peek() not in '|)':
            pieces.append(self.piece(depth))
        return ('cat', pieces)

    def piece(self, depth: int):
        atom = self.atom(depth)
        quantifier = self.quantifier()
        nxt = self.peek()
        if quantifier and nxt is not None and nxt in '?*+{':
            raise self.error("XSD has no lazy, possessive or repeated quantifier "
                             f"(a second '{nxt}' after a quantifier)")
        return ('rep', atom, *quantifier) if quantifier else atom

    def quantifier(self) -> Optional[Tuple[int, Optional[int]]]:
        ch = self.peek()
        if ch in ('?', '*', '+'):
            self.pos += 1
            return {'?': (0, 1), '*': (0, None), '+': (1, None)}[ch]
        if ch != '{':
            return None
        end = self.text.find('}', self.pos)
        match = re.fullmatch(r'\{(\d+)(?:(,)(\d*))?\}', self.text[self.pos:end + 1]) if end >= 0 else None
        if not match:
            raise self.error("a '{' must start a quantifier {n}, {n,} or {n,m}; write \\{ for the character")
        low = int(match.group(1))
        high = int(match.group(3)) if match.group(3) else (None if match.group(2) else low)
        if high is not None and high < low:
            raise self.error(f"the quantifier {{{low},{high}}} has a maximum below its minimum")
        if max(low, high or 0) > _MAX_REPEAT:
            raise self.error(f"a repeat count above {_MAX_REPEAT} is not supported")
        self.pos = end + 1
        return low, high

    def atom(self, depth: int):
        ch = self.peek()
        if ch == '(':
            if self.text.startswith('(?', self.pos):
                raise self.error("XSD has no '(?' construct (no non-capturing group, flag, "
                                 "look-around or named group)")
            self.pos += 1
            inner = self.regexp(depth + 1)
            if self.peek() != ')':
                raise self.error("missing ')'")
            self.pos += 1
            return inner
        if ch == '[':
            return _set(self.class_expr())
        if ch == '.':
            self.pos += 1
            return _set(_complement([(0x0A, 0x0A), (0x0D, 0x0D)]))
        if ch == '\\':
            return _set(self.escape())
        if ch in ('?', '*', '+', '{'):
            raise self.error(f"a quantifier '{ch}' has nothing to repeat")
        if ch in ('}', ']'):
            raise self.error(f"'{ch}' must be written \\{ch}")
        self.pos += 1
        return _set([(ord(ch), ord(ch))])

    # an escape outside or inside a class: the code points it stands for
    def escape(self) -> Ranges:
        start = self.pos
        self.pos += 1  # the backslash
        ch = self.peek()
        if ch is None:
            raise self.error("a pattern cannot end with a backslash", start)
        self.pos += 1
        if ch in _SINGLE_ESCAPES:
            return [(_SINGLE_ESCAPES[ch],) * 2]
        if ch in _METACHARS:
            return [(ord(ch),) * 2]
        if ch in 'sSdDwWiIcC':
            return _multi(ch)
        if ch in 'pP':
            return self.category_escape(ch, start)
        raise self.error(f"XSD has no escape \\{ch}" + (
            " (a back reference)" if ch.isdigit() else
            " (use \\s, \\d, \\w or \\i / \\c; there are no word boundaries or anchors)"
            if ch in 'bBAZzG' else ''), start)

    def category_escape(self, ch: str, start: int) -> Ranges:
        if self.peek() != '{':
            raise self.error(f"\\{ch} must be followed by {{category}}", start)
        end = self.text.find('}', self.pos)
        if end < 0:
            raise self.error(f"\\{ch}{{ has no closing '}}'", start)
        name = self.text[self.pos + 1:end]
        self.pos = end + 1
        if name.startswith('Is'):
            block = _BLOCKS.get(name[2:])
            if block is None:
                raise self.error(f"the Unicode block \\{ch}{{{name}}} is not supported", start)
            ranges: Ranges = [block]
        else:
            try:
                ranges = _category(name)
            except XsdRegexError as exc:
                raise self.error(str(exc), start) from None
        return _complement(ranges) if ch == 'P' else ranges

    # ---- character classes
    def class_expr(self) -> Ranges:
        """``[ ... ]`` with optional negation and ``-[ ... ]`` subtraction."""
        start = self.pos
        self.pos += 1  # '['
        negate = False
        if self.peek() == '^':
            negate = True
            self.pos += 1
        members: Ranges = []
        subtract: Optional[Ranges] = None
        while True:
            ch = self.peek()
            if ch is None:
                raise self.error("missing ']'", start)
            if ch == ']':
                self.pos += 1
                break
            if ch == '-' and self.text.startswith('-[', self.pos):
                self.pos += 1
                subtract = self.class_expr()
                if self.peek() != ']':
                    raise self.error("a class subtraction must be the end of the class")
                continue
            lo = self.class_char()
            if isinstance(lo, list):  # a multi-character escape: no range from it
                members.extend(lo)
                continue
            if self.peek() == '-' and not self.text.startswith('-[', self.pos) \
                    and self.pos + 1 < len(self.text) and self.text[self.pos + 1] != ']':
                self.pos += 1
                hi = self.class_char()
                if isinstance(hi, list):
                    raise self.error("the end of a character range cannot be an escape like \\d")
                if hi < lo:
                    raise self.error(f"the range {chr(lo)}-{chr(hi)} is backwards", start)
                members.append((lo, hi))
            else:
                members.append((lo, lo))
        result = _complement(members) if negate else _normalize(members)
        if subtract is not None:
            result = _subtract(result, subtract)
        return result

    def class_char(self):
        """One class member: a code point, or the ranges of an escape such as ``\\d``."""
        ch = self.peek()
        if ch == '\\':
            ranges = self.escape()
            return ranges[0][0] if len(ranges) == 1 and ranges[0][0] == ranges[0][1] else ranges
        if ch == '[':
            raise self.error("write \\[ for '[' inside a class")
        self.pos += 1
        return ord(ch)


class _Nfa:
    """A Thompson NFA: ``steps[i]`` is ``(starts, ends, next)`` for a character class
    state, ``None`` for the match state; ``splits[i]`` lists the epsilon moves of a
    split state."""

    def __init__(self):
        self.classes: List[Optional[Tuple[List[int], List[int], int]]] = []
        self.splits: List[Optional[List[int]]] = []

    def add(self, cls=None, split=None) -> int:
        if len(self.classes) >= _MAX_STATES:
            raise XsdRegexError(f"the pattern needs more than {_MAX_STATES} states to match "
                                "(a large repeat count inside another repeat)")
        self.classes.append(cls)
        self.splits.append(split)
        return len(self.classes) - 1

    def build(self, node, after: int) -> int:
        """Add the states of *node*, continuing at *after*; return its entry state."""
        kind = node[0]
        if kind == 'set':
            return self.add((node[2], node[3], after))
        if kind == 'cat':
            for part in reversed(node[1]):
                after = self.build(part, after)
            return after
        if kind == 'alt':
            destinations: List[int] = []
            for part in node[1]:  # empty branches all lead to *after*: keep one
                destination = self.build(part, after)
                if destination not in destinations:
                    destinations.append(destination)
            return self.add(split=destinations)
        _, body, low, high = node
        if high is None:
            loop = self.add(split=[])
            self.splits[loop] = [self.build(body, loop), after]
            entry = loop
        else:
            entry = after
            for _ in range(high - low):
                entry = self.add(split=[self.build(body, entry), after])
        for _ in range(low):
            entry = self.build(body, entry)
        return entry


class _Compiled:
    def __init__(self, tree):
        self.nfa = _Nfa()
        self.match = self.nfa.add()
        self.start = self.nfa.build(tree, self.match)

    def closure(self, seeds, live: set) -> int:
        """Add to *live* every state reachable from *seeds* by epsilon moves.

        Returns the work done (states looked at), which the caller charges to the budget."""
        splits = self.nfa.splits
        stack = list(seeds)
        work = len(stack)
        while stack:
            state = stack.pop()
            if state in live:
                continue
            live.add(state)
            if splits[state] is not None:
                stack.extend(splits[state])
                work += len(splits[state])
        return work

    def fullmatch(self, text: str) -> bool:
        classes = self.nfa.classes
        live: set = set()
        steps = self.closure([self.start], live)
        for char in text:
            if not live:
                return False
            steps += len(live)
            if steps > MATCH_BUDGET:
                raise XsdRegexLimit(f"matching a text of {len(text)} characters needs more than "
                                    f"{MATCH_BUDGET} steps")
            code = ord(char)
            moves = []
            for state in live:
                cls = classes[state]
                if cls is not None:
                    starts, ends, nxt = cls
                    i = bisect_right(starts, code) - 1
                    if i >= 0 and code <= ends[i]:
                        moves.append(nxt)
            live = set()
            steps += self.closure(moves, live)
        return self.match in live


@lru_cache(maxsize=256)
def compile_pattern(pattern: str) -> _Compiled:
    """The matcher of the XSD *pattern*.

    Raises ``XsdRegexError`` for a pattern that is not valid XSD, or uses a
    construct this module does not support, naming it.
    """
    try:
        return _Compiled(_Parser(pattern).parse())
    except RecursionError:
        raise XsdRegexError(f"/{pattern}/ is nested too deeply") from None


def fullmatch(pattern: str, text: str) -> bool:
    """Whether the whole of *text* matches the XSD *pattern*, in time linear in *text*."""
    return compile_pattern(pattern).fullmatch(text)
