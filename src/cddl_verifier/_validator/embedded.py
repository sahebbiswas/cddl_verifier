"""Byte strings that hold CBOR (``.cbor``, ``.cborseq``): checked as embedded items.

Moved verbatim out of ``_analyzer`` (#83); ``CBORAnalyzer`` composes it.
"""

from typing import Any, List, Optional, Tuple

from .._cbor import CBOR
from .._cddl.printer import format_node
from .._decode import SimpleCBORDecoder
from .._diagnostics import Diagnostic
from .support import _CheckError, _NO_STEP


class EmbeddedMixin:
    """Byte strings that hold CBOR (``.cbor``, ``.cborseq``): checked as embedded items."""

    def _embedded_errors(self, value: Any, expr, controls, _depth: int, strict: bool) -> List[str]:
        """Check a ``bstr`` with ``.cbor`` / ``.cborseq`` controls: every one must hold.

        ``.cbor`` decodes one item and checks it against its argument;
        ``.cborseq`` decodes a CBOR sequence and checks its items, as an array,
        against its argument. The bytes' own controls (``.size``) apply too.
        """
        def label(op, inner):
            shown = format_node(inner)
            return 'items' if op == 'cborseq' and '@' in shown else shown  # a lifted structure

        decoded = []
        for op, _base, inner in controls:
            if not isinstance(value, bytes):
                return [f"expected bstr (.{op} {label(op, inner)}), got {type(value).__name__}"]
            try:
                if op == 'cbor':
                    decoded.append(SimpleCBORDecoder(value).decode("cbor"))
                else:
                    decoded.append(CBOR.loads_sequence(value, keep_encoding=True))
            except Exception as exc:
                what = 'embedded CBOR' if op == 'cbor' else 'embedded CBOR sequence'
                return [f"{what} (.{op} {label(op, inner)}) does not decode: {exc}"]
        # 'bstr .cbor uint .size 1': the bytes' own controls apply too
        errors = self._primitive_errors(value, 'bstr', expr)
        for (op, _base, inner), nested in zip(controls, decoded):
            errors = errors or self._check_embedded(value, nested, inner, op == 'cborseq',
                                                    _depth + 1, strict)
        return errors

    def _check_embedded(self, payload: bytes, nested: Any, inner, sequence: bool = False,
                        _depth: int = 0, strict: bool = False) -> List[_CheckError]:
        """Check *nested*, decoded from the byte string *payload*, against *inner*.

        The errors know where in the input they are: inside the byte string, at
        the item that is at fault (#154). An error about the embedded item as a
        whole is at that item, which is all of *payload* for ``.cbor``.
        """
        with self._inside_embedded_cbor(payload, sequence):
            errors = self._check_value(nested, inner, _depth, strict)
            whole = self._embedded_item_span(self._current_path(), _NO_STEP) if errors else None
        return [self._located_at(error, whole) for error in errors]

    @classmethod
    def _located_at(cls, error: _CheckError, span: Optional[Tuple[int, int]]) -> _CheckError:
        """*error* with *span* for itself and each alternative that has no position yet.

        What is already located (found by a nested validation run) keeps its
        span. This is for errors about the embedded item as a whole, found
        while the embedded frame is open: they are reported after it closes.
        """
        if not error.located:
            error = error.reworded(str(error))
            error.span, error.located = span, True
        error.alternatives = tuple(
            item if isinstance(item, Diagnostic) else (item[0], cls._located_at(item[1], span))
            for item in error.alternatives)
        return error
