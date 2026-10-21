"""Matching a decoded value against ``#M`` and ``#M.N`` (RFC 8610 §3.6, #167, #171).

``#M`` is any data item of major type *M*, and ``#M.N`` one whose additional
information is *N*. The decoder returns Python values, so the major type is
read from the value:

====  ==========================================  ==============================
Major Decoded value                               ``#M.N``
====  ==========================================  ==============================
0     ``int`` from 0 to 2**64-1                   ``N`` is the additional information
1     ``int`` from -2**64 to -1                   (the width of the head: below 24
2     ``bytes``                                   the value or length itself, 24
3     ``str``                                     to 27 the width of its argument,
4     ``list``                                    31 an indefinite length)
5     ``dict``
6     a ``CBORTag``, or a bignum                  ``N`` is the tag number
7     ``bool``, ``None``, ``float``,              20 ``false``, 21 ``true``, 22
      ``UNDEFINED``, ``CBORSimple``               ``null``, 23 ``undefined``, 25 to
                                                  27 a float of that width, else
                                                  the simple value ``N``
====  ==========================================  ==============================

The additional information of an integer, string, array, map or float is what
the decoder kept of the head it was written with (see
:func:`~cddl_verifier._cbor_values.head_info`); data decoded without that
record, or given as Python values, is taken to be written the preferred way.
A float with no record matches a width it can be written in without loss.
For major types 6 and 7 the number is not additional information (RFC 9682):
``#6.N`` is a tag with number ``N`` around any item, as ``#6.N(#)`` is.
"""

import struct
from typing import Any, Optional

from .._cbor_values import UNDEFINED, CBORSimple, CBORTag, head_info

MAJOR_NAMES = {0: 'unsigned integer', 1: 'negative integer', 2: 'byte string',
               3: 'text string', 4: 'array', 5: 'map', 6: 'tag', 7: 'float or simple value'}

_UINT_LIMIT = 2 ** 64
_FLOAT_FORMATS = {25: '>e', 26: '>f'}


def as_tag(value: Any) -> Any:
    """*value* as a :class:`CBORTag` when it is a bignum the decoder returned as an ``int``, else *value*.

    A tag 2 or 3 around a byte string of 2^64 or more is an ``int`` in decoded data (so that
    it round-trips); a schema that names the tag (``#6.2(bstr)``) must see the tag (#234).
    """
    parts = tag_parts(value) if _is_bignum(value) else None
    return value if parts is None else CBORTag(parts[0], parts[1])


def is_tag(value: Any) -> bool:
    """A decoded tag, or an integer too big for major type 0 or 1 (a bignum)."""
    return isinstance(value, CBORTag) or _is_bignum(value)


def tag_parts(value: Any):
    """``(tag number, item)`` of a tag value, else ``None``.

    A bignum the decoder returned as an ``int`` is tag 2 (positive) or 3
    (negative) around its magnitude as a byte string.
    """
    if _is_bignum(value):
        magnitude = value if value >= 0 else -1 - value
        return (2 if value >= 0 else 3), magnitude.to_bytes((magnitude.bit_length() + 7) // 8,
                                                             'big')
    if isinstance(value, CBORTag):
        return value.tag, value.value
    return None


def _is_bignum(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and not -_UINT_LIMIT <= value < _UINT_LIMIT)


def major_error(value: Any, major: int, info: Optional[int] = None) -> Optional[str]:
    """Why *value* is not ``#major`` / ``#major.info``, or ``None`` if it is."""
    if not _has_major(value, major):
        return f"expected a {MAJOR_NAMES[major]} (#{major}), got {_describe(value)}"
    if info is None:
        return None
    if major == 6:  # '#6.N': the tag number is N, whatever width it was encoded in
        number = tag_parts(value)[0]
        return None if number == info else f"expected tag {info} (#6.{info}), got tag {number}"
    if major == 7:
        return None if _is_simple(value, info) else f"expected #7.{info}, got {_describe(value)}"
    found = head_info(value)
    if found == info:
        return None
    return f"expected #{major}.{info}, {_head_text(value, found)}"


def _is_simple(value: Any, info: int) -> bool:
    if info == 20:
        return value is False
    if info == 21:
        return value is True
    if info == 22:
        return value is None
    if info == 23:
        return value is UNDEFINED
    if info in _FLOAT_FORMATS or info == 27:
        return isinstance(value, float) and float_width_fits(value, info)
    return isinstance(value, CBORSimple) and value.number == info


def float_width_fits(value: float, info: int) -> bool:
    """Whether the float *value* is, or can be written as, one of width *info* (25 to 27)."""
    written = getattr(value, 'info', None)
    if written is not None:
        return written == info
    if info == 27 or value != value or value in (float('inf'), float('-inf')):
        return True
    try:
        return struct.unpack(_FLOAT_FORMATS[info], struct.pack(_FLOAT_FORMATS[info], value))[0] \
            == value
    except OverflowError:
        return False


def _head_text(value: Any, found: Optional[int]) -> str:
    if found is None:
        return f"got {_describe(value)}"
    if found == 31:
        return "got an indefinite length"
    return f"its head has additional information {found}"


def _has_major(value: Any, major: int) -> bool:
    if major == 6:
        return is_tag(value)
    if isinstance(value, CBORTag):
        return False  # a tag is not the item inside it
    if major == 0:
        return isinstance(value, int) and not isinstance(value, bool) \
            and 0 <= value < _UINT_LIMIT
    if major == 1:
        return isinstance(value, int) and not isinstance(value, bool) \
            and -_UINT_LIMIT <= value < 0
    if major == 2:
        return isinstance(value, bytes)
    if major == 3:
        return isinstance(value, str)
    if major == 4:
        return isinstance(value, list)
    if major == 5:
        return isinstance(value, dict)
    return isinstance(value, (bool, float, CBORSimple)) or value is None or value is UNDEFINED


def _describe(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) or value is None or value is UNDEFINED \
            or isinstance(value, CBORSimple):
        return repr(value)
    return type(value).__name__
