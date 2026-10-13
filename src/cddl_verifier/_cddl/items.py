"""Matching a decoded value against ``#M`` and ``#M.N`` (RFC 8610 §3.6, #167).

``#M`` is any data item of major type *M*, and ``#M.N`` one whose additional
information is *N*. The decoder returns Python values, so the major type is
read from the value:

====  =========================================  ===============================
Major Decoded value                              ``#M.N``
====  =========================================  ===============================
0     ``int`` from 0 to 2**64-1                  ``N`` below 24 is the value
1     ``int`` from -2**64 to -1                  ``N`` below 24 is ``-1-N``
2     ``bytes``                                  ``N`` below 24 is the length
3     ``str``                                    ``N`` below 24 is the length
4     ``list``                                   ``N`` below 24 is the length
5     ``dict``                                   ``N`` below 24 is the length
6     a ``(tag, item)`` tuple, or a bignum       (not checked)
7     ``bool``, ``None``, ``float``              20 ``false``, 21 ``true``,
                                                 22 ``null``, 25-27 a float
====  =========================================  ===============================

What the decoder does not keep cannot be checked: the width an item was
encoded in (``#0.26`` is checked as ``#0``, and ``#7.26`` as any float, as
``float32`` is), see :func:`width_unchecked`. ``undefined`` and unassigned
simple values are not decoded at all (#168), so nothing matches ``#7.23`` or
``#7.<other>``.
"""

from typing import Any, Optional

MAJOR_NAMES = {0: 'unsigned integer', 1: 'negative integer', 2: 'byte string',
               3: 'text string', 4: 'array', 5: 'map', 6: 'tag', 7: 'float or simple value'}

_UINT_LIMIT = 2 ** 64


def is_tag(value: Any) -> bool:
    """A decoded tag, or an integer too big for major type 0 or 1 (a bignum)."""
    return (isinstance(value, tuple) and len(value) == 2
            and isinstance(value[0], int) and not isinstance(value[0], bool)) \
        or _is_bignum(value)


def tag_parts(value: Any):
    """``(tag number, item)`` of a tag value, else ``None``.

    A bignum the decoder returned as an ``int`` is tag 2 (positive) or 3
    (negative) around its magnitude as a byte string.
    """
    if _is_bignum(value):
        magnitude = value if value >= 0 else -1 - value
        return (2 if value >= 0 else 3), magnitude.to_bytes((magnitude.bit_length() + 7) // 8,
                                                             'big')
    if is_tag(value):
        return value
    return None


def _is_bignum(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and not -_UINT_LIMIT <= value < _UINT_LIMIT)


def width_unchecked(major: int, info: Optional[int]) -> bool:
    """Whether ``#M.N`` names an encoding detail the decoder does not keep.

    That is the width of an integer or length (``N`` from 24 to 27), and an
    indefinite length (31). They are checked as the major type alone.
    """
    return isinstance(info, int) and major < 6 and info >= 24


def major_error(value: Any, major: int, info: Optional[int] = None) -> Optional[str]:
    """Why *value* is not ``#major`` / ``#major.info``, or ``None`` if it is."""
    if not _has_major(value, major):
        return f"expected a {MAJOR_NAMES[major]} (#{major}), got {_describe(value)}"
    if info is None or major == 6 or width_unchecked(major, info):
        return None
    if major == 7:
        if info == 20 and value is False or info == 21 and value is True \
                or info == 22 and value is None \
                or info in (25, 26, 27) and isinstance(value, float):
            return None
        return f"expected #7.{info}, got {_describe(value)}"
    if major in (0, 1):
        expected = info if major == 0 else -1 - info
        return None if value == expected else f"expected {expected} (#{major}.{info}), got {value}"
    length = len(value)
    return None if length == info else (
        f"expected {MAJOR_NAMES[major]} of length {info} (#{major}.{info}), got length {length}")


def _has_major(value: Any, major: int) -> bool:
    if major == 6:
        return is_tag(value)
    if isinstance(value, tuple) and is_tag(value):
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
    return isinstance(value, (bool, float)) or value is None


def _describe(value: Any) -> str:
    if isinstance(value, (bool, int, float)) or value is None:
        return repr(value) if not isinstance(value, bool) else str(value).lower()
    return type(value).__name__
