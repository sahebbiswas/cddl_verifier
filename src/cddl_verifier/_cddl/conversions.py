"""Text conversions for the controls of RFC 9741 (``.b64u``, ``.hex``, ``.base10``, ``.printf``, ``.json``).

Each ``decode_*`` function reads the text of a text string the way its control
defines and returns the value it stands for, or ``None`` when the text is not in
the form the control accepts. They are strict, as RFC 9741 says: only the
alphabet of the control, no blank space, the padding the control prescribes
(none for ``.b64u``, ``.b32`` and ``.h32``; ``=`` for ``.b64c``), and, except for
the ``-sloppy`` variants, no stray bits after the last character.

``.printf`` is here too: :func:`parse_format` reads a C ``printf`` format, and
the pieces of a text that a format could have written are listed by
:func:`printf_matches` (:func:`match_printf` gives the first) and
:func:`read_conversion` reads each piece back into a data item, so that a text is
checked by taking it apart (the data items are checked against the controller by
the caller, which tries the next way of taking the text apart when they do not fit).
"""

import base64
import json
import math
import sys
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

# ------------------------------------------------------------------ bytes

_B64U = re.compile(r'[A-Za-z0-9_-]*\Z')
_B64C = re.compile(r'(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?\Z')
_B32 = re.compile(r'[A-Z2-7]*\Z')
_H32 = re.compile(r'[0-9A-V]*\Z')
_H32_TO_B32 = str.maketrans('0123456789ABCDEFGHIJKLMNOPQRSTUV', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')
_HEX = {'hex': re.compile(r'(?:[0-9A-Fa-f]{2})*\Z'),
        'hexlc': re.compile(r'(?:[0-9a-f]{2})*\Z'),
        'hexuc': re.compile(r'(?:[0-9A-F]{2})*\Z')}
_B45_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'
_B45_VALUE = {char: index for index, char in enumerate(_B45_ALPHABET)}


def decode_b64u(text: str, sloppy: bool = False) -> Optional[bytes]:
    """Base64url without padding (RFC 4648 section 5); *sloppy* allows non-zero bits at the end."""
    if not _B64U.match(text) or len(text) % 4 == 1:
        return None
    data = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    if not sloppy and base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii') != text:
        return None
    return data


def decode_b64c(text: str, sloppy: bool = False) -> Optional[bytes]:
    """Classic base64 with padding (RFC 4648 section 4); *sloppy* allows non-zero bits at the end."""
    if not _B64C.match(text):
        return None
    data = base64.b64decode(text)
    if not sloppy and base64.b64encode(data).decode('ascii') != text:
        return None
    return data


def decode_b32(text: str) -> Optional[bytes]:
    """Base32 without padding (RFC 4648 section 6)."""
    if not _B32.match(text) or len(text) % 8 not in (0, 2, 4, 5, 7):
        return None
    data = base64.b32decode(text + '=' * (-len(text) % 8))
    return data if base64.b32encode(data).rstrip(b'=').decode('ascii') == text else None


def decode_h32(text: str) -> Optional[bytes]:
    """Base32 with the extended hex alphabet and no padding (RFC 4648 section 7)."""
    if not _H32.match(text):
        return None
    return decode_b32(text.translate(_H32_TO_B32))


def decode_hex(text: str, form: str = 'hex') -> Optional[bytes]:
    """Base16 (RFC 4648 section 8): either case (``hex``), lower (``hexlc``) or upper (``hexuc``)."""
    return bytes.fromhex(text) if _HEX[form].match(text) else None


def decode_b45(text: str) -> Optional[bytes]:
    """Base45 (RFC 9285)."""
    if len(text) % 3 == 1 or any(char not in _B45_VALUE for char in text):
        return None
    out = bytearray()
    for index in range(0, len(text), 3):
        chunk = [_B45_VALUE[char] for char in text[index:index + 3]]
        if len(chunk) == 3:
            number = chunk[0] + chunk[1] * 45 + chunk[2] * 2025
            if number > 0xFFFF:
                return None
            out += number.to_bytes(2, 'big')
        else:
            number = chunk[0] + chunk[1] * 45
            if number > 0xFF:
                return None
            out.append(number)
    return bytes(out)


#: ``op -> decoder`` for the control operators that give a byte string.
BYTE_DECODERS: Dict[str, Callable[[str], Optional[bytes]]] = {
    'b64u': decode_b64u,
    'b64u-sloppy': lambda text: decode_b64u(text, sloppy=True),
    'b64c': decode_b64c,
    'b64c-sloppy': lambda text: decode_b64c(text, sloppy=True),
    'b32': decode_b32,
    'h32': decode_h32,
    'hex': decode_hex,
    'hexlc': lambda text: decode_hex(text, 'hexlc'),
    'hexuc': lambda text: decode_hex(text, 'hexuc'),
    'b45': decode_b45,
}

# ---------------------------------------------------------------- integers

_BASE10 = re.compile(r'(?:0|-?[1-9][0-9]*)\Z')


def decode_base10(text: str) -> Optional[int]:
    """An integer in decimal without leading zeros: ``0|-?[1-9][0-9]*``."""
    return int(text) if _BASE10.match(text) and len(text) <= 4300 else None


# -------------------------------------------------------------------- JSON

def _no_constant(name: str):
    """Reject ``NaN`` and ``Infinity``, which JSON does not have."""
    raise ValueError(f"{name} is not JSON")


def _object(pairs):
    """Build a JSON object, rejecting duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate member name {key!r}")
        result[key] = value
    return result


def decode_json(text: str):
    """The data item for a JSON text, by the conversion of RFC 8949 section 6.2.

    Integers stay integers, numbers with a fraction or exponent are floats, objects
    become maps with text keys. Raises ``ValueError`` for text that is not
    one JSON value (``NaN``, ``Infinity`` and repeated member names included).
    """
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_no_constant)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None


# ------------------------------------------------------------------ printf

class PrintfError(ValueError):
    """A format that RFC 9741 does not allow or that is not a valid ``printf`` format."""


class Spec:
    """One ``%`` conversion of a format."""

    __slots__ = ('flags', 'width', 'precision', 'conv', 'star_width', 'star_precision')

    def __init__(self, flags: str, width: int, precision: Optional[int], conv: str,
                 star_width: bool = False, star_precision: bool = False):
        """Set up the object; a ``*`` width or precision takes its value from a data item (RFC 9741)."""
        self.flags, self.width, self.precision, self.conv = flags, width, precision, conv
        self.star_width, self.star_precision = star_width, star_precision

    @property
    def arity(self) -> int:
        """How many data items the conversion takes: the value, and one for each ``*``."""
        return 1 + self.star_width + self.star_precision

    def with_(self, width: int = 0, precision: Optional[int] = None) -> 'Spec':
        """This conversion with the ``*`` values filled in (a negative width means the ``-`` flag)."""
        flags = self.flags + '-' if width < 0 and '-' not in self.flags else self.flags
        return Spec(flags, abs(width) if self.star_width else self.width,
                    (None if precision is None or precision < 0 else precision) if self.star_precision
                    else self.precision, self.conv)


def item_count(parts: List[object]) -> int:
    """How many data items a parsed format takes (a ``*`` takes one more than its conversion)."""
    return sum(part.arity for part in parts if not isinstance(part, str))


_SPEC = re.compile(r'%([-+ #0]*)([0-9]*|\*)(?:\.([0-9]*|\*))?([a-zA-Z%])')
_CONVERSIONS = 'diouxXeEfFgGaAcs'


def parse_format(fmt: str) -> List[object]:
    """The parts of a ``printf`` format: literal strings and :class:`Spec` objects.

    RFC 9741 leaves out the length modifiers and the ``p`` and ``n`` conversions:
    they are rejected. A ``*`` width or precision is kept (``Spec.star_width``): C takes
    it from an extra argument before the value.
    """
    parts: List[object] = []
    pos, literal = 0, []
    while pos < len(fmt):
        char = fmt[pos]
        if char != '%':
            literal.append(char)
            pos += 1
            continue
        match = _SPEC.match(fmt, pos)
        if match is None:
            raise PrintfError(f"the format has a '%' that starts no conversion (at {pos}); write '%%' for '%'")
        flags, width, precision, conv = match.groups()
        if conv == '%':
            if match.group() != '%%':
                raise PrintfError("'%%' takes no flags, width or precision")
            literal.append('%')
        else:
            if conv in 'hlLjztqpn' or conv not in _CONVERSIONS:
                reason = ('length modifiers are not allowed' if conv in 'hlLjztq' else
                          f"the conversion '%{conv}' is not allowed")
                raise PrintfError(f"{reason} in a .printf format (RFC 9741)")
            if literal:
                parts.append(''.join(literal))
                literal = []
            parts.append(Spec(flags, int(width) if width and width != '*' else 0,
                              None if precision in (None, '*') else int(precision or 0), conv,
                              star_width=width == '*', star_precision=precision == '*'))
        pos = match.end()
    if literal:
        parts.append(''.join(literal))
    return parts


def _int_digits(value: int, conv: str) -> str:
    """The digits of *value* in the base of *conv*."""
    if conv in 'di':
        return str(abs(value))
    if conv == 'u':
        return str(value)
    return {'o': '{:o}', 'x': '{:x}', 'X': '{:X}'}[conv].format(value)


def format_int(spec: Spec, value: int) -> Optional[str]:
    """What C's ``printf`` writes for an integer conversion; ``None`` if it has no output (a negative unsigned)."""
    conv = spec.conv
    if conv not in 'di' and value < 0:
        return None
    digits = _int_digits(value, conv)
    if spec.precision is not None:
        digits = '' if spec.precision == 0 and value == 0 else digits.rjust(spec.precision, '0')
    prefix = ''
    if conv in 'di':
        prefix = '-' if value < 0 else '+' if '+' in spec.flags else ' ' if ' ' in spec.flags else ''
    elif '#' in spec.flags:
        if conv == 'o' and not digits.startswith('0'):
            digits = '0' + digits
        elif conv in 'xX' and value != 0:
            prefix = '0' + conv
    return _pad(spec, prefix, digits, zero=spec.precision is None)


def _pad(spec: Spec, prefix: str, body: str, zero: bool) -> str:
    """Pad *body* (with *prefix*) to the width of *spec*."""
    gap = spec.width - len(prefix) - len(body)
    if gap <= 0:
        return prefix + body
    if '-' in spec.flags:
        return prefix + body + ' ' * gap
    if '0' in spec.flags and zero:
        return prefix + '0' * gap + body
    return ' ' * gap + prefix + body


def format_float(spec: Spec, value: float) -> str:
    """What ``printf`` writes for a floating point conversion (Python's ``%`` agrees with C here).

    ``%a`` and ``%A``, which Python has no conversion for, are written by :func:`format_hex_float`.
    """
    if spec.conv in 'aA':
        return format_hex_float(spec, value)
    precision = '' if spec.precision is None else f'.{spec.precision}'
    return ('%' + spec.flags + (str(spec.width) if spec.width else '') + precision + spec.conv) % value


_HEX_FLOAT = re.compile(r'0x([01])\.([0-9a-f]+)p([+-][0-9]+)\Z')


def format_hex_float(spec: Spec, value: float) -> str:
    """What C99 ``printf`` writes for ``%a`` and ``%A``: a hexadecimal floating-point number.

    This is the form glibc writes: ``0x1.8p+0`` for 1.5 (a normal number starts with
    ``1``, a subnormal or zero with ``0``), the fraction without trailing zeros unless
    there is a precision, which rounds half to even, and the exponent in decimal. ``#``
    keeps the point of a number with no fraction (``0x1.p+0``), and the ``0`` flag pads
    with zeros after ``0x``. Infinity and NaN are ``inf`` and ``nan`` (``INF`` and
    ``NAN`` for ``A``), as for the other float conversions; C leaves other details of the
    text open (the leading digit of a normal number may differ on other platforms).
    A NaN is written without a sign, as Python writes it for the other float conversions
    (glibc writes ``-nan`` for one whose sign bit is set).
    """
    upper = spec.conv == 'A'
    nan = math.isnan(value)
    sign = '-' if not nan and math.copysign(1.0, value) < 0 else \
        '+' if '+' in spec.flags else ' ' if ' ' in spec.flags else ''
    if nan or math.isinf(value):
        word = 'nan' if nan else 'inf'
        return _pad(spec, sign, word.upper() if upper else word, zero=False)
    lead, fraction, exponent = _HEX_FLOAT.match(abs(value).hex()).groups()
    fraction = fraction.ljust(13, '0')
    if spec.precision is None:
        fraction = fraction.rstrip('0')
    elif spec.precision >= 13:
        fraction = fraction.ljust(spec.precision, '0')
    else:  # round the 53 bits to the digits asked for, half to even
        digits = spec.precision
        shift = (13 - digits) * 4
        rounded, rest = divmod(int(lead + fraction, 16), 1 << shift)
        if rest > 1 << (shift - 1) or (rest == 1 << (shift - 1) and rounded & 1):
            rounded += 1
        lead = format(rounded >> (4 * digits), 'x')
        fraction = format(rounded & ((1 << (4 * digits)) - 1), 'x').rjust(digits, '0') if digits else ''
    point = '.' + fraction if fraction or '#' in spec.flags else ''
    body = f"{lead}{point}p{'-' if exponent[0] == '-' else '+'}{int(exponent[1:])}"
    prefix = sign + '0x'
    if upper:
        prefix, body = sign + '0X', body.upper()
    return _pad(spec, prefix, body, zero=True)


def format_text(spec: Spec, text: str) -> str:
    """``%s`` and ``%c``: *text*, cut to the precision (``%s``) and padded to the width."""
    if spec.conv == 's' and spec.precision is not None:
        text = text[:spec.precision]
    return _pad(spec, '', text, zero=False)


_INT_CLASS = {'d': r'[-+ 0-9]+', 'i': r'[-+ 0-9]+', 'u': r'[ 0-9]+', 'o': r'[ 0-7]+',
              'x': r'[ 0-9a-fx]+', 'X': r'[ 0-9A-FX]+'}
_FLOAT_CLASS = r'(?:[-+ ]?(?:inf|nan|INF|NAN)|[-+ 0-9.eE]+)'

#: How many steps :func:`match_printf` may take before it gives up.
MATCH_BUDGET = 200_000


class PrintfLimit(Exception):
    """Taking a text apart by a format needed more steps than :data:`MATCH_BUDGET` allows."""


_FLOAT_RUN = re.compile(r'[-+ 0-9.eE]+')
_HEX_FLOAT_RUN = re.compile(r'[-+ 0-9a-fA-FxXpP.]+')
_FLOAT_WORD = re.compile(r'[-+ ]?(?:inf|nan|INF|NAN)')
_RUNS = {conv: re.compile(pattern) for conv, pattern in _INT_CLASS.items()}


def _ends(part: 'Spec', text: str, start: int) -> List[int]:
    """Where a piece that *part* may have written, starting at *start*, can end (shortest first)."""
    conv, size = part.conv, len(text)
    if conv in _RUNS:  # a run of characters of a class
        run = _RUNS[conv].match(text, start)
        return list(range(start + 1, run.end() + 1)) if run else []
    if conv in 'eEfFgGaA':
        ends = set()
        run = (_HEX_FLOAT_RUN if conv in 'aA' else _FLOAT_RUN).match(text, start)
        if run:
            ends.update(range(start + 1, run.end() + 1))
        word = _FLOAT_WORD.match(text, start)
        if word:
            ends.add(word.end())
        return sorted(ends)
    if conv == 'c':
        wide = part.width > 1 or part.star_width
        return list(range(start + 1, size + 1)) if wide else ([start + 1] if start < size else [])
    return list(range(start, size + 1))  # 's'


def printf_matches(parts: List[object], text: str, budget: int = MATCH_BUDGET) -> Iterator[List[str]]:
    """Each way the conversions of *parts* could have written *text*, as the list of their pieces.

    Literal parts must appear as they are. A conversion takes the shortest piece
    first, so the first way is the one with the shortest early pieces, and the next
    ways follow as the earlier pieces are made longer (``"%s%s"`` takes ``"abcd"`` as
    ``"" + "abcd"``, ``"a" + "bcd"``, ... ``"abcd" + ""``). A position that cannot be
    completed is remembered, so the work is bounded by the number of parts and the
    length of the text, and by *budget*, which counts the steps over all the ways
    taken, not each one.

    Raises:
        PrintfLimit: (while iterating) the work needed is more than *budget* characters.
    """
    failed = set()
    spent = [0]
    pieces: List[str] = []

    def charge(amount: int) -> None:
        """Count a step and stop when the budget is spent."""
        spent[0] += amount
        if spent[0] > budget:
            raise PrintfLimit("matching the text against the .printf format needed too much work")

    def walk(index: int, start: int) -> Iterator[None]:
        """Yield once for each way parts[index:] can take the text from *start*; the pieces are kept in order."""
        if index == len(parts):
            if start == len(text):
                yield
            return
        if (index, start) in failed:
            return
        found = False
        part = parts[index]
        if isinstance(part, str):
            charge(len(part) + 1)
            if text.startswith(part, start):
                for _ in walk(index + 1, start + len(part)):
                    found = True
                    yield
        else:
            for end in _ends(part, text, start):
                charge(1)
                pieces.append(text[start:end])
                for _ in walk(index + 1, end):
                    found = True
                    yield
                pieces.pop()
        if not found:
            failed.add((index, start))

    try:
        for _ in walk(0, 0):
            yield list(pieces)
    except RecursionError:  # a format with more parts than the stack has room for
        raise PrintfLimit("the .printf format has too many parts to match") from None


def match_printf(parts: List[object], text: str, budget: int = MATCH_BUDGET) -> Optional[List[str]]:
    """The first way :func:`printf_matches` takes *text* apart by *parts*, or ``None`` if it does not fit.

    Raises:
        PrintfLimit: the work needed is more than *budget* characters.
    """
    return next(printf_matches(parts, text, budget), None)


def read_conversion(spec: Spec, text: str):
    """The data item *text* stands for as the output of *spec*, or ``None`` if *spec* cannot write it.

    The text is parsed and written again, and must come out the same, so
    ``"0013"`` is the output of ``%04x`` for 19 but ``"13"`` is not.
    """
    conv = spec.conv
    try:
        if conv in _INT_CLASS:
            number = text.strip(' ')
            sign = -1 if number.startswith('-') else 1
            number = number.lstrip('+-').rstrip(' ')
            if conv in 'xX' and number[:2] in ('0x', '0X'):
                number = number[2:]
            if not number or not re.fullmatch(r'[0-9a-fA-F]+', number):
                return None
            value = sign * int(number, {'d': 10, 'i': 10, 'u': 10, 'o': 8, 'x': 16, 'X': 16}[conv])
            if conv == 'o' and '#' in spec.flags and number.startswith('0'):
                value = sign * int(number, 8)
            return value if format_int(spec, value) == text else None
        if conv in 'aA':
            stripped = text.strip(' ')
            sign = -1.0 if stripped.startswith('-') else 1.0
            try:
                value = float.fromhex(stripped)
            except OverflowError:
                # rounding up the largest values reaches 2**1024 (``0x2p+1023``)
                value = sign * sys.float_info.max
                return value if format_float(spec, value) == text else None
            if format_float(spec, value) == text:
                return value
            # a precision can round a neighbouring value to this text: up into a lead
            # digit of 2 (``%.0a`` of 1.5 is ``0x2p+0``), or a tiny subnormal to
            # ``0x0p-1022``. The text denotes *value*, and is what the neighbour writes.
            neighbour = math.nextafter(value, 0.0) if value else sign * 5e-324
            return value if format_float(spec, neighbour) == text else None
        if conv in 'eEfFgG':
            value = float(text.strip(' '))
            return value if format_float(spec, value) == text else None
        if conv == 'c':
            stripped = text.strip(' ') if len(text) > 1 else text
            if len(stripped) != 1:
                return None
            code = ord(stripped)
            return code if format_text(spec, chr(code)) == text else None
        return text if format_text(spec, text) == text else None  # 's'
    except (ValueError, OverflowError):
        return None


def read_all(spec: Spec, text: str) -> Iterator[Any]:
    """Every data item *text* can stand for as the output of *spec* (:func:`read_conversion` gives one).

    That is one item for every conversion but ``%s`` with a width, which pads the string:
    ``"  ab"`` is ``"  ab"`` and, at width 4, ``"ab"`` and ``" ab"`` as well.
    """
    if spec.conv == 's' and spec.width > 0:
        trailing = '-' in spec.flags
        for cut in range(len(text) + 1):
            value = text[:len(text) - cut] if trailing else text[cut:]
            if not (text[len(text) - cut:] if trailing else text[:cut]).strip(' '):
                if format_text(spec, value) == text:
                    yield value
        return
    value = read_conversion(spec, text)
    if value is not None:
        yield value


_INT_MAX = 2 ** 31 - 1


def star_readings(spec: Spec, text: str) -> Iterator[Tuple[Optional[int], Any, List[Optional[int]]]]:
    """The ways *text* can be the output of a conversion with a ``*`` width or precision (RFC 9741 section 2.3).

    C takes a ``*`` from an extra ``int`` argument that comes before the value, so the
    text does not say what they were: ``"42"`` is ``%*d`` with a width of 0, 1 or 2 (or
    -1, -2) and ``"   42"`` one of exactly 5 (or -5, which pads on the right). Each
    result is ``(precision, value, widths)``: the precision (``None`` when the
    conversion has no ``*`` precision), the value it was read as, and the widths that
    write *text* (``[None]`` when the conversion has no ``*`` width). One text can give
    several results (``%*s`` of ``"  ab"`` is ``"  ab"`` at any width up to 4, and ``"ab"``
    at 4). A precision that writes a text shorter than itself (``%.*s`` of ``"ab"``) is
    tried up to the length of the text and as the largest ``int``, not at the values in
    between; ``-1`` stands for every negative precision, which C treats as no precision.
    """
    size = len(text)
    if spec.star_precision:
        precisions: List[Optional[int]] = [-1, *range(size + 1), _INT_MAX]
    else:
        precisions = [None]
    for precision in precisions:
        if not spec.star_width:
            for value in read_all(spec.with_(0, precision), text):
                yield precision, value, [None]
            continue
        for value in read_all(spec.with_(0, precision), text):  # no padding: any width not wider than the text
            yield precision, value, [0, *range(1, size + 1), *range(-1, -size - 1, -1)]
        for width in (size, -size):  # padded to the width of the text
            for padded in read_all(spec.with_(width, precision), text):
                yield precision, padded, [width]
