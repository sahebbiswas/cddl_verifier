"""Text conversions for the controls of RFC 9741 (``.b64u``, ``.hex``, ``.base10``, ``.printf``, ``.json``).

Each ``decode_*`` function reads the text of a text string the way its control
defines and returns the value it stands for, or ``None`` when the text is not in
the form the control accepts. They are strict, as RFC 9741 says: only the
alphabet of the control, no blank space, the padding the control prescribes
(none for ``.b64u``, ``.b32`` and ``.h32``; ``=`` for ``.b64c``), and, except for
the ``-sloppy`` variants, no stray bits after the last character.

``.printf`` is here too: :func:`parse_format` reads a C ``printf`` format, and
:func:`printf_pattern` gives the regular expression its output has and how to read
each conversion back into a data item, so that a text is checked by taking it
apart (the data items are checked against the controller by the caller).
"""

import base64
import json
import re
from typing import Callable, Dict, List, Optional, Tuple

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
    raise ValueError(f"{name} is not JSON")


def _object(pairs):
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

    __slots__ = ('flags', 'width', 'precision', 'conv')

    def __init__(self, flags: str, width: int, precision: Optional[int], conv: str):
        self.flags, self.width, self.precision, self.conv = flags, width, precision, conv


_SPEC = re.compile(r'%([-+ #0]*)([0-9]*)(?:\.([0-9]*))?([a-zA-Z%])')
_CONVERSIONS = 'diouxXeEfFgGcs'


def parse_format(fmt: str) -> List[object]:
    """The parts of a ``printf`` format: literal strings and :class:`Spec` objects.

    RFC 9741 leaves out the length modifiers and the ``p`` and ``n`` conversions
    (and ``a``, ``*`` widths are not supported either): they are rejected.
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
            if conv in 'hlLjztqaApn' or conv not in _CONVERSIONS:
                reason = ('length modifiers are not allowed' if conv in 'hlLjztq' else
                          f"the conversion '%{conv}' is not allowed")
                raise PrintfError(f"{reason} in a .printf format (RFC 9741)")
            if literal:
                parts.append(''.join(literal))
                literal = []
            parts.append(Spec(flags, int(width) if width else 0,
                              None if precision is None else int(precision or 0), conv))
        pos = match.end()
    if literal:
        parts.append(''.join(literal))
    return parts


def _int_digits(value: int, conv: str) -> str:
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
    gap = spec.width - len(prefix) - len(body)
    if gap <= 0:
        return prefix + body
    if '-' in spec.flags:
        return prefix + body + ' ' * gap
    if '0' in spec.flags and zero:
        return prefix + '0' * gap + body
    return ' ' * gap + prefix + body


def format_float(spec: Spec, value: float) -> str:
    """What ``printf`` writes for a floating point conversion (Python's ``%`` agrees with C here)."""
    precision = '' if spec.precision is None else f'.{spec.precision}'
    return ('%' + spec.flags + (str(spec.width) if spec.width else '') + precision + spec.conv) % value


def format_text(spec: Spec, text: str) -> str:
    """``%s`` and ``%c``: *text*, cut to the precision (``%s``) and padded to the width."""
    if spec.conv == 's' and spec.precision is not None:
        text = text[:spec.precision]
    return _pad(spec, '', text, zero=False)


_INT_CLASS = {'d': r'[-+ 0-9]+?', 'i': r'[-+ 0-9]+?', 'u': r'[ 0-9]+?', 'o': r'[ 0-7]+?',
              'x': r'[ 0-9a-fx]+?', 'X': r'[ 0-9A-FX]+?'}
_FLOAT_CLASS = r'(?:[-+ ]?(?:inf|nan|INF|NAN)|[-+ 0-9.eE]+?)'


def printf_pattern(parts: List[object]) -> Tuple['re.Pattern', List[Spec]]:
    """``(regex, specs)``: the regex has one group for each conversion, in order."""
    pieces, specs = [], []
    for part in parts:
        if isinstance(part, str):
            pieces.append(re.escape(part))
            continue
        specs.append(part)
        conv = part.conv
        if conv in _INT_CLASS:
            pieces.append('(' + _INT_CLASS[conv] + ')')
        elif conv in 'eEfFgG':
            pieces.append('(' + _FLOAT_CLASS + ')')
        elif conv == 'c':
            pieces.append('(.+?)' if part.width > 1 else '(.)')
        else:
            pieces.append('(.*?)')
    return re.compile(''.join(pieces) + r'\Z', re.DOTALL), specs


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
