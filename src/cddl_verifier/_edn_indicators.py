"""Encoding indicators for EDN (RFC 8949 section 8.1, #237).

An item decoded with ``keep_encoding=True`` remembers how it was written (:mod:`._cbor_values`):
the additional information of its head, which is 24 to 27 for a head longer than needed
(``1801`` is the integer 1 in a 1-byte argument) and 31 for an indefinite length, and a
float always carries its width. RFC 8949 section 8.1 writes that after the item: ``_n`` for
additional information 24+n (``1_0``, ``1.5_3``, ``"a"_1``), and ``_`` after the opening
bracket of an array or map that has an indefinite length (``[_ 1, 2]``). The indicators are
optional in the notation, and a reader that honours them gets the same bytes back, which is
what lets ``<< >>`` show an item that is not in preferred serialization.

:func:`rebuild` is that reader: the bytes an item stands for with the indicators it carries.
The generator uses it to decide whether embedded CBOR can be shown as items. What it cannot
express is left to the byte string: the chunks of an indefinite string beyond one, and the
width of a tag number, are not recorded by the decoder.
"""

from typing import Any, Optional

from ._cbor import CBORKey
from ._cbor_values import (INDEFINITE, UNDEFINED, CBORSimple, CBORTag, pack_float, shortest_info)
from .cbor import encode as cbor_encode

__all__ = ['indicator', 'rebuild', 'array_marker']


def _shortest_float_info(value: float) -> int:
    """The additional information of the shortest float that holds *value* (25, 26 or 27)."""
    for info in (25, 26, 27):
        if pack_float(value, info) is not None:
            return info
    return 27  # pragma: no cover - a double always holds a float


def _suffix(info: int) -> str:
    return '_' if info == INDEFINITE else f'_{info - 24}'


def indicator(value: Any) -> str:
    """The encoding indicator written after a number or string: ``_0`` to ``_3``, or ``''``.

    Nothing for an item in preferred serialization, for an indefinite-length string
    (its chunks are not known, see :func:`~._edn.EDNGenerator`) and for a value that
    carries no record.
    """
    info = getattr(value, 'info', None)
    if info is None or isinstance(value, bool):
        return ''
    if isinstance(value, float):
        return _suffix(info) if info in (25, 26, 27) and info != _shortest_float_info(value) else ''
    if isinstance(value, (int, str, bytes)) and 24 <= info <= 27:
        return _suffix(info)
    return ''


def array_marker(value: Any) -> str:
    """What follows the opening bracket or brace of an array or map: ``_`` (indefinite), ``_n``, or ``''``."""
    info = getattr(value, 'info', None)
    return _suffix(info) if isinstance(value, (list, dict)) and info is not None and 24 <= info <= 31 else ''


def _head(major: int, argument: int, info: Optional[int]) -> bytes:
    """The head of an item: the shortest one, or the one of additional information *info*."""
    if info is None or not 24 <= info <= 27:  # below 24 the argument is in the head: that is the shortest
        info = shortest_info(argument)
    if info < 24:
        return bytes([(major << 5) | info])
    size = {24: 1, 25: 2, 26: 4, 27: 8}[info]
    return bytes([(major << 5) | info]) + argument.to_bytes(size, 'big')


def rebuild(item: Any) -> bytes:
    """The bytes *item* stands for, with the head widths and indefinite lengths it carries (#237)."""
    if isinstance(item, CBORKey):
        item = item.value
    if item is UNDEFINED or isinstance(item, (CBORSimple, bool, float)) or item is None:
        return cbor_encode(item)  # a float is written in its own width
    if isinstance(item, CBORTag):
        return _head(6, item.tag, None) + rebuild(item.value)
    info = getattr(item, 'info', None)
    if isinstance(item, int):
        if not -2 ** 64 <= item < 2 ** 64:
            return cbor_encode(item)  # a bignum
        return _head(0 if item >= 0 else 1, item if item >= 0 else -1 - item, info)
    if isinstance(item, (bytes, str)):
        data = item if isinstance(item, bytes) else item.encode('utf-8')
        major = 2 if isinstance(item, bytes) else 3
        if info == INDEFINITE:  # one chunk, the only structure the record can stand for
            chunk = _head(major, len(data), None) + data if data else b''
            return bytes([(major << 5) | INDEFINITE]) + chunk + b'\xff'
        return _head(major, len(data), info) + data
    if isinstance(item, (list, tuple)):
        body = b''.join(rebuild(element) for element in item)
        return (b'\x9f' + body + b'\xff') if info == INDEFINITE else _head(4, len(item), info) + body
    if isinstance(item, dict):
        body = b''.join(rebuild(key) + rebuild(value) for key, value in item.items())
        return (b'\xbf' + body + b'\xff') if info == INDEFINITE else _head(5, len(item), info) + body
    return cbor_encode(item)
