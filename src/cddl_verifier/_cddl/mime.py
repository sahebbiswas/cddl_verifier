"""Whether a text is a MIME message (RFC 5322, RFC 2045, RFC 2046); the content of tag 36 (#187).

A message is a header section, then optionally an empty line and a body. This module
checks what makes it a MIME message rather than any text:

* the header section is one or more header fields, ``name: value``, with folded
  continuation lines (a line that starts with a space or a tab) and no continuation
  before the first field; a physical line is at most 998 bytes (RFC 5322 section
  2.1.1); no NUL and no bare CR (RFC 5322 section 2.3)
* ``MIME-Version``, when present, is ``1.0`` (RFC 2045 section 4)
* ``Content-Type``, when present, is ``type/subtype`` with ``;``-separated
  ``attribute=value`` parameters (RFC 2045 section 5.1), comments allowed
* ``Content-Transfer-Encoding``, when present, is one token (RFC 2045 section 6.1)
* each of those three appears at most once
* ``multipart/*`` has a ``boundary`` parameter of 1 to 70 permitted characters
  (RFC 2046 section 5.1.1), its body has the opening and the closing delimiter, and
  every part is checked the same way (a part may have no header fields), nested up to
  :data:`MAX_DEPTH` levels

Lines may end in CRLF or in a bare LF: text that was copied around rarely keeps its
CRLF, and a validator that refused it would refuse most real messages. Non-ASCII text
in headers is accepted (RFC 6532). The body is not decoded: ``base64`` and
``quoted-printable`` content, ``message/rfc822`` and the line-length limit of 7bit
bodies are not checked.
"""

import re
from typing import List, Optional, Tuple

__all__ = ['message_error', 'MAX_DEPTH']

#: How deep multipart entities may nest
MAX_DEPTH = 10

_MAX_LINE = 998
_FIELD_NAME = re.compile(r'([\x21-\x39\x3b-\x7e]+)[ \t]*:')  # printable ASCII except ':'
_TOKEN = r"[!#$%&'*+\-.0-9A-Z^_`a-z{|}~]+"
_QUOTED = r'"(?:[^"\\\r\n]|\\.)*"'
_CONTENT_TYPE = re.compile(
    rf'\s*({_TOKEN})\s*/\s*({_TOKEN})((?:\s*;\s*{_TOKEN}\s*=\s*(?:{_TOKEN}|{_QUOTED}))*)(?:\s*;)?\s*',
    re.ASCII)
_PARAMETER = re.compile(rf'({_TOKEN})\s*=\s*({_TOKEN}|{_QUOTED})', re.ASCII)
_ONE_TOKEN = re.compile(rf'\s*{_TOKEN}\s*', re.ASCII)
_VERSION = re.compile(r'\s*1\s*\.\s*0\s*')
_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,\-./:=? ]{1,70}", re.ASCII)
_SINGLE = ('mime-version', 'content-type', 'content-transfer-encoding')


def _bad_chars(line: str) -> Optional[str]:
    if '\x00' in line:
        return "a NUL character"
    return None


def _uncommented(value: str) -> Optional[str]:
    """*value* without its comments (RFC 5322 section 3.2.2), or ``None`` if one is not closed.

    A comment is ``(`` ... ``)``, may nest, and ``\\`` quotes the next character;
    parentheses inside a quoted string are text."""
    out: List[str] = []
    depth = 0
    quoted = False
    i = 0
    while i < len(value):
        char = value[i]
        if depth:
            if char == '\\':
                i += 1
            elif char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
        elif quoted:
            out.append(char)
            if char == '\\' and i + 1 < len(value):
                i += 1
                out.append(value[i])
            elif char == '"':
                quoted = False
        elif char == '(':
            depth = 1
        else:
            if char == '"':
                quoted = True
            out.append(char)
        i += 1
    return None if depth or quoted else ''.join(out)


def _split_fields(lines: List[str]) -> Tuple[Optional[List[Tuple[str, str]]], int, Optional[str]]:
    """The header fields ``(name, unfolded value)`` at the start of *lines*, the number of lines
    they take (the empty line that ends them is not counted), and a problem."""
    fields: List[Tuple[str, str]] = []
    count = 0
    for line in lines:
        if line == '':
            break
        count += 1
        if len(line.encode('utf-8')) > _MAX_LINE:
            return None, count, f"line {count} is longer than {_MAX_LINE} bytes (RFC 5322 section 2.1.1)"
        problem = _bad_chars(line)
        if problem:
            return None, count, f"line {count} has {problem}"
        if '\r' in line:
            return None, count, f"line {count} has a bare CR (RFC 5322 section 2.3)"
        if line[0] in ' \t':
            if not fields:
                return None, count, "line 1 continues a header field, but there is none before it"
            name, value = fields[-1]
            fields[-1] = (name, value + '\n' + line)
            continue
        match = _FIELD_NAME.match(line)
        if not match:
            return None, count, (f"line {count} is not a header field (expected 'name: value'"
                                 f"{', then an empty line before the body' if fields else ''})")
        fields.append((match.group(1), line[match.end():]))
    return fields, count, None


def _parameters(rest: str) -> dict:
    found = {}
    for name, value in _PARAMETER.findall(rest):
        if value.startswith('"'):
            value = re.sub(r'\\(.)', r'\1', value[1:-1])
        found.setdefault(name.lower(), value)
    return found


def _entity(text: str, depth: int, *, needs_fields: bool) -> Optional[str]:
    lines = text.split('\n')
    lines = [line[:-1] if line.endswith('\r') else line for line in lines]
    fields, used, problem = _split_fields(lines)
    if problem:
        return problem
    if needs_fields and not fields:
        return "there is no header field (a MIME message starts with a header section)"
    body_lines = lines[used + 1:] if used < len(lines) else []
    seen = {}
    content_type = None
    for name, value in fields:
        key = name.lower()
        if key not in _SINGLE:
            continue
        if key in seen:
            return f"the {name} header field appears twice"
        seen[key] = True
        plain = _uncommented(value)
        if plain is None:
            return f"the {name} header field has a comment or a quoted string that is not closed"
        if key == 'mime-version':
            if not _VERSION.fullmatch(plain):
                return f"MIME-Version is {plain.strip()!r}, not 1.0 (RFC 2045 section 4)"
        elif key == 'content-transfer-encoding':
            if not _ONE_TOKEN.fullmatch(plain):
                return f"Content-Transfer-Encoding {plain.strip()!r} is not one token (RFC 2045 section 6.1)"
        else:
            unfolded = plain.replace('\n', ' ')
            match = _CONTENT_TYPE.fullmatch(unfolded)
            if not match:
                return (f"Content-Type {unfolded.strip()!r} is not 'type/subtype' with "
                        "'; attribute=value' parameters (RFC 2045 section 5.1)")
            content_type = (match.group(1).lower(), match.group(2).lower(), _parameters(match.group(3)))
    if content_type and content_type[0] == 'multipart':
        return _multipart(content_type[2].get('boundary'), body_lines, depth)
    return None


def _multipart(boundary: Optional[str], body_lines: List[str], depth: int) -> Optional[str]:
    if depth >= MAX_DEPTH:
        return f"multipart entities are nested more than {MAX_DEPTH} levels"
    if boundary is None:
        return "a multipart Content-Type needs a boundary parameter (RFC 2046 section 5.1.1)"
    if not _BOUNDARY.fullmatch(boundary) or boundary.endswith(' '):
        return (f"the boundary {boundary!r} is not 1 to 70 characters from the permitted set, "
                "not ending in a space (RFC 2046 section 5.1.1)")
    opening = '--' + boundary
    parts: List[List[str]] = []
    closed = False
    for raw in body_lines:
        line = raw[:-1] if raw.endswith('\r') else raw
        if closed:
            break  # the epilogue
        if line.startswith(opening):
            tail = line[len(opening):]
            if tail.strip(' \t') == '':
                parts.append([])
                continue
            if tail.startswith('--') and tail[2:].strip(' \t') == '':
                closed = True
                continue
        if parts:
            parts[-1].append(line)
    if not parts:
        return f"the body has no part: no line starts with --{boundary}"
    if not closed:
        return f"the body has no closing delimiter --{boundary}-- (RFC 2046 section 5.1.1)"
    for index, part in enumerate(parts, 1):
        problem = _entity('\n'.join(part), depth + 1, needs_fields=False)
        if problem:
            return f"part {index}: {problem}"
    return None


def message_error(text: str) -> Optional[str]:
    """Why *text* is not a MIME message, or ``None``."""
    if text.strip() == '':
        return "the text is empty"
    return _entity(text, 0, needs_fields=True)
