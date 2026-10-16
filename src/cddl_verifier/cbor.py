"""CBOR (RFC 8949) encoding, decoding and diagnostic helpers.

``encode``/``decode``/``diag_dump`` are also available under their longer
names ``cbor_encode``/``cbor_decode``/``cbor_diag_dump``.

``decode`` accepts exactly one complete CBOR data item and raises
:class:`CBORDecodeError` (with a byte ``offset``) for anything else.

Map keys that are not integers, text, byte strings or null (booleans, floats,
arrays, maps and tags) decode to :class:`CBORKey`, which keeps their CBOR type:
``{1: a, true: b}`` keeps both entries and re-encodes to the same bytes.

Indefinite-length strings, arrays and maps decode to the same values as
definite-length ones. ``undefined`` decodes to :data:`UNDEFINED` and the other
simple values to :class:`CBORSimple`. ``decode(data, keep_encoding=True)`` also
records how each item was written (see :func:`head_info`).

A tag decodes to a :class:`CBORTag` and the encoder writes one as a tag. A
``(tag, value)`` tuple is not a tag (it was before 0.24.0): ``encode((1, 2))``
raises ``TypeError``, which asks for a ``CBORTag`` or a list.
"""

from ._cbor_values import UNDEFINED, CBORSimple, CBORTag, CBORUndefined, float_width, head_info
from ._cbor import (
    CBOR,
    CBORDecodeError,
    CBORKey,
    CBORNotDeterministicError,
    CBORTrailingDataError,
    CBORTruncatedError,
    CBORUnsupportedError,
    cbor_decode,
    cbor_decode_sequence,
    cbor_diag_dump,
    cbor_encode,
)

__all__ = [
    "CBOR",
    "CBORDecodeError",
    "CBORKey",
    "CBORNotDeterministicError",
    "CBORSimple",
    "CBORTag",
    "CBORTrailingDataError",
    "CBORTruncatedError",
    "CBORUndefined",
    "CBORUnsupportedError",
    "UNDEFINED",
    "cbor_decode",
    "cbor_decode_sequence",
    "cbor_diag_dump",
    "cbor_encode",
    "decode",
    "decode_sequence",
    "diag_dump",
    "encode",
    "float_width",
    "head_info",
]


def encode(obj, canonical: bool = False) -> bytes:
    """Encode a Python value as CBOR.

    Floats are written in the shortest width that holds them exactly (RFC 8949
    section 4.1): ``1.5`` is ``f9 3e00``. :func:`float_width` fixes the width of
    one value. ``canonical=True`` applies RFC 8949 section 4.2.1 deterministic
    encoding (sorted map keys, and every float at its shortest); see
    ``CBOR.encode`` for details.
    """
    return cbor_encode(obj, canonical=canonical)


def decode(data: bytes, keep_encoding: bool = False, require_deterministic: bool = False):
    """Decode exactly one CBOR data item from *data*.

    With ``keep_encoding=True``, a float, and an integer, string, array or map
    written with a longer head than needed or with an indefinite length, come
    back as a subclass of their Python type whose ``info`` attribute is the
    additional information of the item's initial byte. They compare and print
    as the plain value does. :func:`head_info` reads it for any value.

    With ``require_deterministic=True`` the input must also be deterministically
    encoded (RFC 8949 section 4.2.1): heads as short as they can be, no
    indefinite lengths, shortest floats (NaN as ``f9 7e 00``), map keys in
    bytewise order, and bignums only past 64 bits. The first violation raises
    :class:`CBORNotDeterministicError` (a ``CBORDecodeError``) at its offset.
    What passes is exactly what ``encode(canonical=True)`` writes.
    """
    return cbor_decode(data, keep_encoding, require_deterministic)


def decode_sequence(data: bytes, keep_encoding: bool = False, require_deterministic: bool = False):
    """Decode a CBOR sequence (RFC 8742) from *data* to the list of its items.

    Empty input is the empty sequence. A truncated or malformed item raises
    ``CBORDecodeError`` that names the item and carries the offset. :func:`decode`
    still rejects trailing data. ``require_deterministic=True`` applies to every
    item, as for :func:`decode`.
    """
    return cbor_decode_sequence(data, keep_encoding, require_deterministic)


def diag_dump(data: bytes, indent: str = "  ") -> str:
    """Return an annotated, byte-by-byte dump of the CBOR in *data*."""
    return cbor_diag_dump(data, indent)
