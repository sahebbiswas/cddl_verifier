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

``translate`` turns an XSD pattern into an equivalent Python pattern for
``re.fullmatch``. Every character class is computed as a set of code points,
so negation, subtraction and the multi-character escapes combine as XSD says.
A construct XSD does not have, or that this module does not cover, raises
``XsdRegexError`` naming it.

What is covered: the whole XSD 1.0 grammar, with the Unicode categories of the
running Python (``unicodedata``) and the Unicode blocks in ``_BLOCKS``. Other
blocks are reported as unsupported rather than guessed.
"""

import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

__all__ = ['XsdRegexError', 'translate', 'fullmatch']

_MAX_CODE_POINT = 0x10FFFF
_MAX_DEPTH = 64
_MAX_REPEAT = 1000

Ranges = List[Tuple[int, int]]


class XsdRegexError(ValueError):
    """A pattern that is not valid XSD, or that uses something not supported."""


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


# ---------------------------------------------------------------- emitting

def _emit_char(code: int) -> str:
    return '\\U%08x' % code


def _emit_class(ranges: Ranges) -> str:
    ranges = _normalize(ranges)
    if not ranges:
        return '(?!)'
    parts = []
    for lo, hi in ranges:
        parts.append(_emit_char(lo) if lo == hi else f'{_emit_char(lo)}-{_emit_char(hi)}')
    return '[' + ''.join(parts) + ']'


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

    def parse(self) -> str:
        out = self.regexp(0)
        if self.pos < len(self.text):
            raise self.error(f"unmatched '{self.text[self.pos]}'")
        return out

    def regexp(self, depth: int) -> str:
        if depth > _MAX_DEPTH:
            raise self.error(f"groups are nested more than {_MAX_DEPTH} deep")
        branches = [self.branch(depth)]
        while self.peek() == '|':
            self.pos += 1
            branches.append(self.branch(depth))
        return '|'.join(branches)

    def branch(self, depth: int) -> str:
        pieces = []
        while self.peek() is not None and self.peek() not in '|)':
            pieces.append(self.piece(depth))
        return ''.join(pieces)

    def piece(self, depth: int) -> str:
        atom = self.atom(depth)
        quantifier = self.quantifier()
        nxt = self.peek()
        if quantifier and nxt is not None and nxt in '?*+{':
            raise self.error("XSD has no lazy, possessive or repeated quantifier "
                             f"(a second '{nxt}' after a quantifier)")
        return atom + quantifier

    def quantifier(self) -> str:
        ch = self.peek()
        if ch in ('?', '*', '+'):
            self.pos += 1
            return ch
        if ch != '{':
            return ''
        end = self.text.find('}', self.pos)
        match = re.fullmatch(r'\{(\d+)(?:(,)(\d*))?\}', self.text[self.pos:end + 1]) if end >= 0 else None
        if not match:
            raise self.error("a '{' must start a quantifier {n}, {n,} or {n,m}; write \\{ for the character")
        low = int(match.group(1))
        high = int(match.group(3)) if match.group(3) else None
        if high is not None and high < low:
            raise self.error(f"the quantifier {{{low},{high}}} has a maximum below its minimum")
        if max(low, high or 0) > _MAX_REPEAT:
            raise self.error(f"a repeat count above {_MAX_REPEAT} is not supported")
        self.pos = end + 1
        return match.group(0)

    def atom(self, depth: int) -> str:
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
            return f'(?:{inner})'
        if ch == '[':
            return _emit_class(self.class_expr())
        if ch == '.':
            self.pos += 1
            return _emit_class(_complement([(0x0A, 0x0A), (0x0D, 0x0D)]))
        if ch == '\\':
            return _emit_class(self.escape())
        if ch in ('?', '*', '+', '{'):
            raise self.error(f"a quantifier '{ch}' has nothing to repeat")
        if ch in ('}', ']'):
            raise self.error(f"'{ch}' must be written \\{ch}")
        self.pos += 1
        return _emit_char(ord(ch))

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


@lru_cache(maxsize=256)
def translate(pattern: str) -> str:
    """The Python pattern equal to the XSD *pattern*, for ``re.fullmatch``.

    Raises ``XsdRegexError`` for a pattern that is not valid XSD, or uses a
    construct this module does not support, naming it.
    """
    try:
        return _Parser(pattern).parse()
    except RecursionError:
        raise XsdRegexError(f"/{pattern}/ is nested too deeply") from None


@lru_cache(maxsize=256)
def _compiled(pattern: str):
    try:
        return re.compile(translate(pattern))
    except re.error as exc:  # pragma: no cover - the translation is always valid
        raise XsdRegexError(f"/{pattern}/ cannot be matched: {exc}") from None


def fullmatch(pattern: str, text: str) -> bool:
    """Whether the whole of *text* matches the XSD *pattern*."""
    return _compiled(pattern).fullmatch(text) is not None
