"""Tokenizer for CDDL (RFC 8610 Appendix B, as updated by RFC 9682).

Whitespace and line breaks only separate tokens. Comments (``;`` to end of
line) are not tokens; they are collected in :attr:`Lexer.comments` so the
parser can attach a trailing comment to a group entry.

Token kinds:

* ``ID``: identifier (``value`` is the name). Matching is greedy as the ABNF
  says, so ``tstr.size`` is one identifier.
* ``INT``, ``FLOAT``, ``TEXT``, ``BYTES``: literals (``value`` is decoded).
* ``CTLOP``: ``.name`` (``value`` is ``name``).
* ``OCCUR``: ``?``, ``+``, ``*``, ``n*m`` (``value`` is ``(min, max)``).
* ``HASH``: ``#``, ``#M``, ``#M.N``, ``#M.`` before ``<`` (``value`` is
  ``(major, number, angle)``).
* punctuation, whose kind is its text: ``= /= //= / // , : => ^ ( ) { } [ ]
  < > ~ & .. ...``.
* ``EOF``.
"""

import base64
import binascii
from typing import List, NamedTuple, Optional, Tuple

from .ast import Source, Span
from .errors import CDDLSyntaxError

_ALPHA = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
_EALPHA = _ALPHA | frozenset('@_$')
_DIGIT = frozenset('0123456789')
_HEXDIG = _DIGIT | frozenset('abcdefABCDEF')
_ID_CHARS = _EALPHA | _DIGIT | frozenset('-.')
_WS = frozenset(' \t\r\n')

# Longest first, so '//=' wins over '//' and '/'.
_PUNCT = ('//=', '...', '/=', '//', '=>', '..', '=', '/', ',', ':', '^',
          '(', ')', '{', '}', '[', ']', '<', '>', '~', '&')

_SIMPLE_ESCAPES = {'"': '"', "'": "'", '\\': '\\', '/': '/', 'b': '\b',
                   'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


class Token(NamedTuple):
    kind: str
    text: str
    start: int
    end: int
    value: object = None
    ws_before: bool = False


class Comment(NamedTuple):
    start: int
    end: int
    text: str  # without the ';', stripped


class Lexer:
    """Turn schema text into a list of tokens ending with ``EOF``."""

    def __init__(self, source: Source):
        self.source = source
        self.text = source.text
        self.comments: List[Comment] = []

    def error(self, message: str, start: int, end: Optional[int] = None) -> CDDLSyntaxError:
        return CDDLSyntaxError(message, Span(start, start + 1 if end is None else end), self.source)

    def tokenize(self) -> List[Token]:
        text, n = self.text, len(self.text)
        tokens: List[Token] = []
        i = 0
        while True:
            ws_start = i
            i = self._skip_trivia(i)
            ws = i > ws_start or not tokens
            if i >= n:
                tokens.append(Token('EOF', '', n, n, None, ws))
                return tokens
            kind, end, value = self._token_at(i)
            tokens.append(Token(kind, text[i:end], i, end, value, ws))
            i = end

    # ------------------------------------------------------------ trivia

    def _skip_trivia(self, i: int) -> int:
        text, n = self.text, len(self.text)
        while i < n:
            c = text[i]
            if c in _WS:
                i += 1
            elif c == ';':
                end = i
                while end < n and text[end] not in '\r\n':
                    end += 1
                self.comments.append(Comment(i, end, text[i + 1:end].strip()))
                i = end
            else:
                break
        return i

    # ------------------------------------------------------------ tokens

    def _token_at(self, i: int) -> Tuple[str, int, object]:
        text, n = self.text, len(self.text)
        c = text[i]

        if c in _EALPHA:
            end = i + 1
            while end < n and text[end] in _ID_CHARS:
                end += 1
            while text[end - 1] in '-.':
                end -= 1
            name = text[i:end]
            if name in ('h', 'b64') and end < n and text[end] == "'":
                return self._bytes(i, end, name)
            return 'ID', end, name

        if c in _DIGIT or (c == '-' and i + 1 < n and text[i + 1] in _DIGIT):
            occ = self._occurrence(i)
            if occ is not None:
                return occ
            return self._number(i)

        if c == '*':
            return self._occurrence(i)
        if c == '?':
            return 'OCCUR', i + 1, (0, 1)
        if c == '+':
            return 'OCCUR', i + 1, (1, None)
        if c == '"':
            return self._text(i)
        if c == "'":
            return self._bytes(i, i, '')
        if c == '#':
            return self._hash(i)

        if c == '.' and i + 1 < n and text[i + 1] in _EALPHA:
            end = i + 2
            while end < n and text[end] in _ID_CHARS:
                end += 1
            while text[end - 1] in '-.':
                end -= 1
            return 'CTLOP', end, text[i + 1:end]

        for p in _PUNCT:
            if text.startswith(p, i):
                return p, i + len(p), None

        if c == '-':
            raise self.error("expected a digit after '-'", i)
        raise self.error(f"unexpected character {c!r}", i)

    def _uint(self, i: int) -> Tuple[Optional[int], int]:
        """Scan an RFC 8610 ``uint`` at *i*: decimal, ``0x`` or ``0b``."""
        text, n = self.text, len(self.text)
        if text.startswith(('0x', '0X'), i) and i + 2 < n and text[i + 2] in _HEXDIG:
            end = i + 2
            while end < n and text[end] in _HEXDIG:
                end += 1
            return int(text[i + 2:end], 16), end
        if text.startswith(('0b', '0B'), i) and i + 2 < n and text[i + 2] in '01':
            end = i + 2
            while end < n and text[end] in '01':
                end += 1
            return int(text[i + 2:end], 2), end
        end = i
        while end < n and text[end] in _DIGIT:
            end += 1
        if end == i:
            return None, i
        return int(text[i:end]), end

    def _occurrence(self, i: int):
        """``[uint] "*" [uint]``, with no spaces inside. ``None`` if absent."""
        text, n = self.text, len(self.text)
        low, end = (0, i) if text[i] == '*' else self._uint(i)
        if low is None or end >= n or text[end] != '*':
            return None
        high, after = self._uint(end + 1)
        return 'OCCUR', after, (low, high)

    def _number(self, i: int):
        text, n = self.text, len(self.text)
        start = i
        if text[i] == '-':
            i += 1
        if text.startswith(('0x', '0X'), i):
            j = i + 2
            while j < n and text[j] in _HEXDIG:
                j += 1
            if j == i + 2:
                raise self.error("expected hex digits after '0x'", start, j)
            if j < n and (text[j] in 'pP' or (text[j] == '.' and j + 1 < n and text[j + 1] in _HEXDIG)):
                return self._hexfloat(start, j)
            value = int(text[i + 2:j], 16)
            return 'INT', j, -value if text[start] == '-' else value
        if text.startswith(('0b', '0B'), i):
            j = i + 2
            while j < n and text[j] in '01':
                j += 1
            if j == i + 2:
                raise self.error("expected binary digits after '0b'", start, j)
            value = int(text[i + 2:j], 2)
            return 'INT', j, -value if text[start] == '-' else value
        j = i
        while j < n and text[j] in _DIGIT:
            j += 1
        is_float = False
        if j + 1 < n and text[j] == '.' and text[j + 1] in _DIGIT:
            is_float = True
            j += 1
            while j < n and text[j] in _DIGIT:
                j += 1
        if j < n and text[j] in 'eE':
            k = j + 1
            if k < n and text[k] in '+-':
                k += 1
            if k < n and text[k] in _DIGIT:
                is_float = True
                j = k
                while j < n and text[j] in _DIGIT:
                    j += 1
        if is_float:
            return 'FLOAT', j, float(text[start:j])
        return 'INT', j, int(text[start:j])

    def _hexfloat(self, start: int, j: int):
        text, n = self.text, len(self.text)
        if text[j] == '.':
            j += 1
            k = j
            while j < n and text[j] in _HEXDIG:
                j += 1
            if j == k:
                raise self.error("expected hex digits after '.' in a hex float", start, j)
        if j >= n or text[j] not in 'pP':
            raise self.error("a hex float needs a 'p' exponent", start, j)
        j += 1
        if j < n and text[j] in '+-':
            j += 1
        k = j
        while j < n and text[j] in _DIGIT:
            j += 1
        if j == k:
            raise self.error("expected digits in the hex float exponent", start, j)
        return 'FLOAT', j, float.fromhex(text[start:j])

    def _escape(self, i: int) -> Tuple[str, int]:
        """Decode the escape starting at the backslash at *i*."""
        text, n = self.text, len(self.text)
        if i + 1 >= n:
            raise self.error("unterminated escape", i)
        c = text[i + 1]
        if c in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[c], i + 2
        if c != 'u':
            raise self.error(f"unknown escape '\\{c}'", i, i + 2)
        if i + 2 < n and text[i + 2] == '{':
            close = text.find('}', i + 3)
            digits = text[i + 3:close] if close != -1 else ''
            if close == -1 or not digits or any(d not in _HEXDIG for d in digits):
                raise self.error("expected hex digits and '}' in '\\u{...}'", i)
            cp = int(digits, 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                raise self.error(f"'\\u{{{digits}}}' is not a Unicode scalar value", i, close + 1)
            return chr(cp), close + 1
        hex4 = text[i + 2:i + 6]
        if len(hex4) != 4 or any(d not in _HEXDIG for d in hex4):
            raise self.error("expected four hex digits after '\\u'", i)
        cp = int(hex4, 16)
        end = i + 6
        if 0xD800 <= cp <= 0xDBFF:
            low = text[end + 2:end + 6] if text.startswith('\\u', end) else ''
            if len(low) == 4 and all(d in _HEXDIG for d in low) and 0xDC00 <= int(low, 16) <= 0xDFFF:
                cp = 0x10000 + ((cp - 0xD800) << 10) + (int(low, 16) - 0xDC00)
                return chr(cp), end + 6
            raise self.error("unpaired surrogate in '\\u' escape", i, end)
        if 0xDC00 <= cp <= 0xDFFF:
            raise self.error("unpaired surrogate in '\\u' escape", i, end)
        return chr(cp), end

    def _text(self, i: int):
        text, n = self.text, len(self.text)
        out = []
        j = i + 1
        while True:
            if j >= n or text[j] in '\r\n':
                raise self.error("unterminated text string", i, j)
            c = text[j]
            if c == '"':
                return 'TEXT', j + 1, ''.join(out)
            if c == '\\':
                s, j = self._escape(j)
                out.append(s)
                continue
            if ord(c) < 0x20 or c == '\x7f':
                raise self.error("control character in text string", j)
            out.append(c)
            j += 1

    def _bytes(self, start: int, quote: int, qualifier: str):
        """``'...'``, ``h'...'`` or ``b64'...'``; *quote* is the opening quote."""
        text, n = self.text, len(self.text)
        j = quote + 1
        out = []
        while True:
            if j >= n:
                raise self.error("unterminated byte string", start, j)
            c = text[j]
            if c == "'":
                break
            if qualifier and c == ';':  # comments are allowed in h'' and b64''
                while j < n and text[j] not in '\r\n':
                    j += 1
                continue
            if c == '\\':
                s, j = self._escape(j)
                out.append(s)
                continue
            out.append(c)
            j += 1
        end = j + 1
        content = ''.join(out)
        if not qualifier:
            return 'BYTES', end, content.encode('utf-8')
        compact = ''.join(content.split())
        try:
            if qualifier == 'h':
                if len(compact) % 2 or any(d not in _HEXDIG for d in compact):
                    raise ValueError
                return 'BYTES', end, bytes.fromhex(compact)
            # b64: base64url or classic base64, padding optional
            std = compact.replace('-', '+').replace('_', '/').rstrip('=')
            if len(std) % 4 == 1:
                raise ValueError
            return 'BYTES', end, base64.b64decode(std + '=' * (-len(std) % 4), validate=True)
        except (ValueError, binascii.Error):
            raise self.error(f"invalid {qualifier}'' byte string", start, end) from None

    def _hash(self, i: int):
        """``#``, ``#M``, ``#M.N`` or ``#M.<`` (the ``<`` is left for the parser)."""
        text, n = self.text, len(self.text)
        j = i + 1
        if j >= n or text[j] not in _DIGIT:
            return 'HASH', j, (None, None, False)
        major = int(text[j])
        j += 1
        if j < n and text[j] == '.':
            if j + 1 < n and text[j + 1] == '<':
                return 'HASH', j + 1, (major, None, True)
            number, end = self._uint(j + 1)
            if number is None:
                raise self.error(f"expected a number or '<' after '#{major}.'", i, j + 1)
            return 'HASH', end, (major, number, False)
        return 'HASH', j, (major, None, False)
