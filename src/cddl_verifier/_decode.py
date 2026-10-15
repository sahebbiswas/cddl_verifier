"""``SimpleCBORDecoder``: a thin wrapper over :class:`CBOR`, kept for existing callers.

Split from ``_analyzer`` (#83) so the EDN generator and the analyzer share it.
"""

from typing import Any

from ._cbor import CBOR


class SimpleCBORDecoder:
    """Compatibility wrapper around unified CBOR class."""
    def __init__(self, data: bytes):
        self.data = data

    def decode(self, breadcrumb: str = "") -> Any:
        """Decode CBOR data using unified CBOR class."""
        return CBOR.loads(self.data, keep_encoding=True)
