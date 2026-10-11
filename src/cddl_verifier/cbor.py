"""CBOR (RFC 8949) encoding, decoding and diagnostic helpers.

``encode``/``decode``/``diag_dump`` are also available under their longer
names ``cbor_encode``/``cbor_decode``/``cbor_diag_dump``.

``decode`` accepts exactly one complete CBOR data item and raises
:class:`CBORDecodeError` (with a byte ``offset``) for anything else.

Map keys that are not integers, text, byte strings or null (booleans, floats,
arrays, maps and tags) decode to :class:`CBORKey`, which keeps their CBOR type:
``{1: a, true: b}`` keeps both entries and re-encodes to the same bytes.

Decoded tags are provisional: they currently decode to ``(tag, value)`` tuples,
which is expected to change in a later release.
"""

from ._cbor import (
    CBOR,
    CBORDecodeError,
    CBORKey,
    CBORTrailingDataError,
    CBORTruncatedError,
    CBORUnsupportedError,
    cbor_decode,
    cbor_diag_dump,
    cbor_encode,
)

__all__ = [
    "CBOR",
    "CBORDecodeError",
    "CBORKey",
    "CBORTrailingDataError",
    "CBORTruncatedError",
    "CBORUnsupportedError",
    "cbor_decode",
    "cbor_diag_dump",
    "cbor_encode",
    "decode",
    "diag_dump",
    "encode",
]


def encode(obj, canonical: bool = False) -> bytes:
    """Encode a Python value as CBOR.

    ``canonical=True`` applies RFC 8949 section 4.2.1 deterministic encoding
    (shortest floats, sorted map keys); see ``CBOR.encode`` for details.
    """
    return cbor_encode(obj, canonical=canonical)


def decode(data: bytes):
    """Decode exactly one CBOR data item from *data*."""
    return cbor_decode(data)


def diag_dump(data: bytes, indent: str = "  ") -> str:
    """Return an annotated, byte-by-byte dump of the CBOR in *data*."""
    return cbor_diag_dump(data, indent)
