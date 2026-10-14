"""
What the content of the tags of the RFC 8610 prelude must be (#169).

``tdate = #6.0(tstr)`` only says that the content is text; RFC 8949 section
3.4 says it is an RFC 3339 date-time. ``content_error(tag, content)`` checks
that meaning for the tags whose content has a definition this module can
check, and returns a message, or ``None`` when the content is fine or the tag
has no check:

======  ===============================================================
tag     content
======  ===============================================================
0       an RFC 3339 ``date-time`` (calendar-valid, offset 00:00 to 23:59,
        second 60 only at the end of a UTC month)
24      a byte string holding exactly one well-formed CBOR item
32      an RFC 3986 URI reference (``::x`` and ``a b`` are not)
33      base64url text without padding (RFC 4648 section 5)
34      base64 text with padding (RFC 4648 section 4); for both the unused
        bits of the last character are 0 (RFC 8949 section 3.4.5.3)
35      a regular expression; checked as a Python ``re`` pattern after
        ``(?<name>...)`` and ``\\k<name>`` are written the Python way, which
        approximates PCRE and ECMA-262 but does not accept every construct
        of either
======  ===============================================================

Tag 36 (a MIME message) has no check beyond being text. A content of the
wrong type (a number under tag 0) is left to the type check of the rule.
"""

import ipaddress
import re
from typing import Any, Callable, Dict, Optional

__all__ = ['content_error', 'CHECKED_TAGS']

# ------------------------------------------------------------------ RFC 3339

_DATE_TIME = re.compile(
    r'(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?'
    r'(?:[Zz]|[+-](\d{2}):(\d{2}))', re.ASCII)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _date_time(text: str) -> Optional[str]:
    match = _DATE_TIME.fullmatch(text)
    if not match:
        return "expected an RFC 3339 date-time (YYYY-MM-DDThh:mm:ss[.fraction](Z|+hh:mm))"
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if not 1 <= month <= 12:
        return f"month {month:02d} is not between 01 and 12"
    if not 1 <= day <= _days_in_month(year, month):
        return f"day {day:02d} does not exist in {year:04d}-{month:02d}"
    if hour > 23 or minute > 59 or second > 60:
        return f"the time {hour:02d}:{minute:02d}:{second:02d} does not exist"
    offset = 0
    if match.group(7) is not None:
        if int(match.group(7)) > 23 or int(match.group(8)) > 59:
            return f"the offset {match.group(7)}:{match.group(8)} does not exist"
        sign = -1 if text[match.start(7) - 1] == '-' else 1
        offset = sign * (int(match.group(7)) * 60 + int(match.group(8)))
    if second == 60 and not _is_leap_second_instant(year, month, day, hour * 60 + minute - offset):
        return "second 60 is only allowed at 23:59:60 UTC on the last day of March, June, September or December"
    return None


def _is_leap_second_instant(year: int, month: int, day: int, utc_minute: int) -> bool:
    """Whether the UTC minute (from the local date's midnight) can end in a leap second.

    RFC 3339 section 5.7 allows second 60 only at the end of a UTC month, in
    practice June or December (ITU-R TF.460 also allows March and September).
    Which of those months really had one cannot be known for the future.
    """
    import datetime
    try:
        day_utc = datetime.date(year, month, day) + datetime.timedelta(days=utc_minute // 1440)
    except (ValueError, OverflowError):
        return False
    return (utc_minute % 1440 == 23 * 60 + 59 and day_utc.month in (3, 6, 9, 12)
            and day_utc.day == _days_in_month(day_utc.year, day_utc.month))


# ----------------------------------------------------------------- RFC 3986

_UNRESERVED = r"A-Za-z0-9\-._~"
_SUB_DELIMS = r"!$&'()*+,;="
_PCT = r"%[0-9A-Fa-f]{2}"
_SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+\-.]*:', re.ASCII)
_PCHARS = re.compile(rf"(?:[{_UNRESERVED}{_SUB_DELIMS}:@]|{_PCT})*", re.ASCII)
_QUERY = re.compile(rf"(?:[{_UNRESERVED}{_SUB_DELIMS}:@/?]|{_PCT})*", re.ASCII)
_USERINFO = re.compile(rf"(?:[{_UNRESERVED}{_SUB_DELIMS}:]|{_PCT})*", re.ASCII)
_REG_NAME = re.compile(rf"(?:[{_UNRESERVED}{_SUB_DELIMS}]|{_PCT})*", re.ASCII)
_IPV_FUTURE = re.compile(rf"v[0-9A-Fa-f]+\.[{_UNRESERVED}{_SUB_DELIMS}:]+", re.ASCII)
_PORT = re.compile(r'[0-9]*', re.ASCII)


def _segments(path: str, what: str) -> Optional[str]:
    for segment in path.split('/'):
        if not _PCHARS.fullmatch(segment):
            return f"the {what} has an invalid character in {segment!r}"
    return None


def _authority(auth: str) -> Optional[str]:
    userinfo, at, hostport = auth.rpartition('@')
    if at and not _USERINFO.fullmatch(userinfo):
        return f"the user information {userinfo!r} has an invalid character"
    if hostport.startswith('['):
        end = hostport.find(']')
        if end < 0:
            return "an IP literal in the host has no closing ']'"
        literal, rest = hostport[1:end], hostport[end + 1:]
        if not _IPV_FUTURE.fullmatch(literal):
            try:
                ipaddress.IPv6Address(literal)
            except ValueError:
                return f"[{literal}] is not an IPv6 address"
            if '%' in literal:
                return "an IPv6 literal cannot carry a zone"
        if rest and not (rest[0] == ':' and _PORT.fullmatch(rest[1:])):
            return f"{rest!r} after the IP literal is not a port"
        return None
    host, colon, port = hostport.rpartition(':')
    if not colon:
        host, port = hostport, ''
    if not _REG_NAME.fullmatch(host):
        return f"the host {host!r} has an invalid character"
    if not _PORT.fullmatch(port):
        return f"the port {port!r} is not digits"
    return None


def _uri_reference(text: str) -> Optional[str]:
    """RFC 3986 section 4.1: ``URI-reference = URI / relative-ref``."""
    bad = next((c for c in text if not (c.isascii() and c.isprintable()) or c in ' "<>\\^`{|}'), None)
    if bad is not None:
        return f"{bad!r} is not allowed in a URI (escape it as %HH)"
    rest, hash_, fragment = text.partition('#')
    if hash_ and not _QUERY.fullmatch(fragment):
        return "the fragment has an invalid character"
    rest, question, query = rest.partition('?')
    if question and not _QUERY.fullmatch(query):
        return "the query has an invalid character"
    scheme = _SCHEME.match(rest)
    if scheme:
        rest = rest[scheme.end():]
    elif ':' in rest.split('/', 1)[0]:
        return "the text before the first ':' is not a scheme (a relative reference cannot have one)"
    if rest.startswith('//'):
        auth, slash, path = rest[2:].partition('/')
        return _authority(auth) or (_segments(slash + path, 'path') if slash else None)
    if rest.startswith('/'):
        return _segments(rest[1:], 'path')
    return _segments(rest, 'path') if rest else None


# ------------------------------------------------------------------- base64

_B64URL = re.compile(r'[A-Za-z0-9_-]*', re.ASCII)
_B64 = re.compile(r'(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?', re.ASCII)


_URL_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
_STD_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def _padding_bits(text: str, alphabet: str) -> Optional[str]:
    """RFC 8949 section 3.4.5.3: the unused bits of the last character must be 0."""
    body = text.rstrip('=')
    if not body or len(body) % 4 == 0:
        return None
    unused = 4 if len(body) % 4 == 2 else 2  # a 2-character block holds 1 byte, a 3-character block 2
    if alphabet.index(body[-1]) & ((1 << unused) - 1):
        return (f"the last character {body[-1]!r} has unused bits that are not 0 "
                f"(RFC 8949 section 3.4.5.3)")
    return None


def _base64url(text: str) -> Optional[str]:
    if not _B64URL.fullmatch(text):
        return "expected base64url text (only A-Z a-z 0-9 - _, and no '=' padding)"
    if len(text) % 4 == 1:
        return "a base64url text cannot have a length of 4n+1"
    return _padding_bits(text, _URL_ALPHABET)


def _base64(text: str) -> Optional[str]:
    if not _B64.fullmatch(text):
        return "expected base64 text with padding (A-Z a-z 0-9 + /, padded with = to a multiple of 4)"
    return _padding_bits(text, _STD_ALPHABET)


# ------------------------------------------------------------------- regexp

_NAMED_GROUP = re.compile(r'\(\?<([A-Za-z_]\w*)>')
_NAMED_REF = re.compile(r'\\k<([A-Za-z_]\w*)>')


def _regexp(text: str) -> Optional[str]:
    pattern = _NAMED_REF.sub(r'(?P=\1)', _NAMED_GROUP.sub(r'(?P<\1>', text))
    try:
        re.compile(pattern)
    except (re.error, RecursionError, OverflowError) as exc:
        return f"expected a regular expression: {exc}"
    return None


# ------------------------------------------------------------- embedded CBOR

def _encoded_cbor(data: bytes) -> Optional[str]:
    from .._cbor import CBOR, CBORDecodeError
    try:
        CBOR.loads(data)
    except CBORDecodeError as exc:
        return f"expected one well-formed CBOR item: {exc}"
    return None


_TEXT: Dict[int, Callable[[str], Optional[str]]] = {
    0: _date_time, 32: _uri_reference, 33: _base64url, 34: _base64, 35: _regexp}

#: The tag numbers whose content is checked.
CHECKED_TAGS = frozenset(_TEXT) | {24}


def content_error(tag: int, content: Any) -> Optional[str]:
    """Why *content* is not what tag *tag* means, or ``None``.

    Content of another type than the tag takes (a number under tag 0) gives
    ``None``: the type check reports that.
    """
    if tag == 24:
        problem = _encoded_cbor(content) if isinstance(content, bytes) else None
        return f"tag 24 content is not valid: {problem}" if problem else None
    check = _TEXT.get(tag)
    if check is None or not isinstance(content, str):
        return None
    problem = check(content)
    return f"tag {tag} content {content!r} is not valid: {problem}" if problem else None
