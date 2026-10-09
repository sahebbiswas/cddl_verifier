"""Public validation facade: :func:`validate`, :class:`Validator` and results.

This module is a thin layer over the internal parser and analyzer
(``_analyzer``). It is the stable entry point: the implementation underneath
can move to a standards-oriented CDDL AST without changing these signatures.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from . import _analyzer
from ._cbor import CBOR, CBORDecodeError
from ._errors import SchemaError

__all__ = ["Diagnostic", "SchemaError", "ValidationResult", "Validator", "validate"]

#: CDDL schema input: schema text (``str``) or a path to a ``.cddl`` file.
SchemaSource = Union[str, "os.PathLike[str]"]

_BYTES_TYPES = (bytes, bytearray, memoryview)
_EDN_FORMATS = ("keyindex", "keyname", "both")


@dataclass(frozen=True)
class Diagnostic:
    """One finding produced while checking data against a schema.

    Attributes:
        message:  Human-readable description.
        code:     ``"decode"`` when the CBOR input could not be decoded,
                  ``"validation"`` when decoded data does not match the schema.
        severity: ``"error"`` for every diagnostic in this release.
        offset:   Byte offset into the CBOR input, when known (decode errors).
    """

    message: str
    code: str = "validation"
    severity: str = "error"
    offset: Optional[int] = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a validation.

    ``bool(result)`` is ``result.valid``. ``data`` holds the decoded value
    (``None`` if decoding failed).
    """

    valid: bool
    root_type: Optional[str]
    diagnostics: Tuple[Diagnostic, ...] = ()
    data: Any = field(default=None, compare=False, repr=False)

    def __bool__(self) -> bool:
        return self.valid

    @property
    def errors(self) -> Tuple[str, ...]:
        """Messages of the error diagnostics."""
        return tuple(d.message for d in self.diagnostics if d.severity == "error")


def _read_schema(schema: SchemaSource) -> str:
    if isinstance(schema, os.PathLike):
        try:
            return Path(schema).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise SchemaError(f"cannot read CDDL schema {os.fspath(schema)!r}: {e}") from e
    if isinstance(schema, str):
        return schema
    raise TypeError(
        "schema must be CDDL text (str) or a path (os.PathLike), "
        f"not {type(schema).__name__}")


class Validator:
    """A parsed CDDL schema that can validate many data items.

    Args:
        schema: CDDL schema text (``str``) or a path to a schema file
            (``pathlib.Path`` or other ``os.PathLike``). A plain ``str`` is
            always treated as schema text, never as a file name.

    Raises:
        SchemaError: The schema file cannot be read or the schema cannot be
            parsed. For text that is not valid CDDL the message is
            ``file:line:column: problem`` and the exception has ``line`` and
            ``column`` attributes.
        TypeError: *schema* is neither ``str`` nor ``os.PathLike``.
    """

    def __init__(self, schema: SchemaSource):
        text = _read_schema(schema)
        name = os.fspath(schema) if isinstance(schema, os.PathLike) else None
        try:
            self._parser = _analyzer.CDDLParser(text, source_name=name)
        except SchemaError:
            raise  # syntax errors already say where: "file:line:col: message"
        except Exception as e:  # noqa: BLE001 - anything else is a parser bug
            raise SchemaError(f"cannot parse CDDL schema: {e}") from e

    @property
    def default_root_type(self) -> Optional[str]:
        """The first rule defined in the schema, used when no root type is given."""
        return self._parser.first_definition

    def _resolve_root(self, root_type: Optional[str]) -> str:
        root = root_type if root_type is not None else self.default_root_type
        if root is None:
            raise SchemaError("the CDDL schema defines no rules; pass root_type")
        return root

    def validate(self, data: Any, root_type: Optional[str] = None) -> ValidationResult:
        """Validate *data* against the schema.

        Args:
            data: CBOR-encoded bytes (``bytes``, ``bytearray`` or
                ``memoryview``), or an already-decoded Python value. Bytes are
                always decoded as CBOR; to validate a decoded byte string
                against a ``bstr`` rule, pass its CBOR encoding.
            root_type: Name of the CDDL rule to validate against. Defaults to
                the first rule in the schema.

        Returns:
            A :class:`ValidationResult`. CBOR that cannot be decoded is
            reported as an invalid result with a ``"decode"`` diagnostic, not
            raised.
        """
        root = self._resolve_root(root_type)
        cbor_bytes = None
        if isinstance(data, _BYTES_TYPES):
            cbor_bytes = bytes(data)
            try:
                data = CBOR.loads(cbor_bytes)
            except CBORDecodeError as e:
                diag = Diagnostic(str(e), code="decode", offset=e.offset)
                return ValidationResult(False, root, (diag,), None)
        analyzer = _analyzer.CBORAnalyzer(self._parser)
        ok = analyzer.validate(data, root, cbor_bytes)
        diagnostics = tuple(Diagnostic(msg) for msg in analyzer.get_errors())
        return ValidationResult(bool(ok) and not diagnostics, root, diagnostics, data)

    def to_edn(self, data: Any, root_type: Optional[str] = None, *,
               annotate: bool = True, edn_format: str = "keyindex") -> str:
        """Render *data* as EDN (RFC 8949 diagnostic notation).

        Map keys are annotated with field names from the schema when
        *annotate* is true and *root_type* (or the default root) applies.

        Args:
            data: CBOR bytes or an already-decoded Python value.
            root_type: CDDL rule used for annotations (defaults to the first rule).
            annotate: Add field-name comments.
            edn_format: ``"keyindex"``, ``"keyname"`` or ``"both"``.

        Raises:
            CBORDecodeError: *data* is bytes that are not one valid CBOR item.
            ValueError: *edn_format* is not one of the supported formats.
        """
        if edn_format not in _EDN_FORMATS:
            raise ValueError(f"edn_format must be one of {_EDN_FORMATS}, not {edn_format!r}")
        if isinstance(data, _BYTES_TYPES):
            data = CBOR.loads(bytes(data))
        root = root_type if root_type is not None else self.default_root_type
        generator = _analyzer.EDNGenerator(self._parser, edn_format=edn_format)
        return generator.generate(data, root, annotate)


def validate(schema: SchemaSource, data: Any,
             root_type: Optional[str] = None) -> ValidationResult:
    """Validate *data* against a CDDL *schema*.

    Shorthand for ``Validator(schema).validate(data, root_type)``; build a
    :class:`Validator` once to check many items against the same schema.
    """
    return Validator(schema).validate(data, root_type)
