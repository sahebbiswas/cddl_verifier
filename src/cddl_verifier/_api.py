"""Public validation facade: :func:`validate`, :class:`Validator` and results.

This module is a thin layer over the internal parser and analyzer
(``_analyzer``). It is the stable entry point: the implementation underneath
can move to a standards-oriented CDDL AST without changing these signatures.
"""

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from . import _analyzer
from ._cddl.modules import ModuleLoader
from ._cbor import CBOR, CBORDecodeError
from ._diagnostics import (JSON_FORMAT_VERSION, Diagnostic, decode_diagnostic, render_json,
                           render_text)
from ._errors import SchemaError

__all__ = ["Diagnostic", "SchemaError", "ValidationResult", "Validator", "render_json",
           "render_text", "validate"]

#: CDDL schema input: schema text (``str``) or a path to a ``.cddl`` file.
SchemaSource = Union[str, "os.PathLike[str]"]

_BYTES_TYPES = (bytes, bytearray, memoryview)
_EDN_FORMATS = ("keyindex", "keyname", "both")


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

    def to_dict(self) -> Dict[str, Any]:
        """The outcome as JSON-ready data: ``valid``, ``root_type`` and ``diagnostics``.

        Each diagnostic is :meth:`Diagnostic.to_dict`. The decoded ``data`` is
        not included.
        """
        return {
            "format_version": JSON_FORMAT_VERSION,
            "valid": self.valid,
            "root_type": self.root_type,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        """The outcome as a JSON document (see :meth:`to_dict`)."""
        return render_json(self.diagnostics, indent=indent, valid=self.valid,
                           root_type=self.root_type)

    def format(self, *, verbose: bool = False) -> str:
        """The diagnostics as text, one finding per line (see :func:`render_text`)."""
        return render_text(self.diagnostics, verbose=verbose)


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
        modules: Modules the schema imports with the comment syntax of the
            CDDL modules draft (``;# import rfc9393 as coswid``): a mapping of
            module name to its CDDL text (``str``) or to a path. Their rules
            join the schema, as ``coswid.<rule>`` for an import with ``as``
            and under their own names for an include.
        module_path: Directories searched for ``<module name>.cddl`` when
            *modules* has no entry for it. When *schema* is a path, its
            directory is searched last.
        unique_choices: Reject data that matches more than one structured
            (map, array or tag) alternative of a choice. By default a choice
            accepts data that fits any alternative, as CDDL defines it; EDN
            output labels such data with the first alternative that matches.

    Attributes:
        warnings: What the schema relies on without checking: an import whose
            module was not found keeps its names unchecked (they match any
            value). The same messages are issued as ``UserWarning``.

    Raises:
        SchemaError: The schema file cannot be read or the schema cannot be
            parsed. For text that is not valid CDDL the message is
            ``file:line:column: problem`` and the exception has ``line`` and
            ``column`` attributes.
        TypeError: *schema* is neither ``str`` nor ``os.PathLike``.
    """

    def __init__(self, schema: SchemaSource, *,
                 modules: Optional[Mapping[str, SchemaSource]] = None,
                 module_path: Sequence[Union[str, "os.PathLike[str]"]] = (),
                 unique_choices: bool = False):
        text = _read_schema(schema)
        name = os.fspath(schema) if isinstance(schema, os.PathLike) else None
        self._unique_choices = unique_choices
        search = list(module_path)
        if name is not None:
            search.append(Path(name).parent)
        loader = ModuleLoader(modules, search)
        try:
            self._parser = _analyzer.CDDLParser(text, source_name=name, loader=loader)
        except SchemaError:
            raise  # syntax errors already say where: "file:line:col: message"
        except Exception as e:  # noqa: BLE001 - anything else is a parser bug
            raise SchemaError(f"cannot parse CDDL schema: {e}") from e
        self.warnings: Tuple[str, ...] = tuple(self._parser.warnings)
        for message in self.warnings:
            warnings.warn(message, UserWarning, stacklevel=2)

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
                data = CBOR.loads(cbor_bytes, keep_encoding=True)
            except CBORDecodeError as e:
                return ValidationResult(False, root, (decode_diagnostic(e, cbor_bytes),), None)
        analyzer = _analyzer.CBORAnalyzer(self._parser, unique_choices=self._unique_choices)
        ok = analyzer.validate(data, root, cbor_bytes)
        diagnostics = analyzer.get_diagnostics()
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
            data = CBOR.loads(bytes(data), keep_encoding=True)
        root = root_type if root_type is not None else self.default_root_type
        generator = _analyzer.EDNGenerator(self._parser, edn_format=edn_format)
        return generator.generate(data, root, annotate)


def validate(schema: SchemaSource, data: Any,
             root_type: Optional[str] = None, *,
             modules: Optional[Mapping[str, SchemaSource]] = None,
             module_path: Sequence[Union[str, "os.PathLike[str]"]] = (),
             unique_choices: bool = False) -> ValidationResult:
    """Validate *data* against a CDDL *schema*.

    Shorthand for ``Validator(schema).validate(data, root_type)``; build a
    :class:`Validator` once to check many items against the same schema.
    """
    return Validator(schema, modules=modules, module_path=module_path,
                     unique_choices=unique_choices).validate(data, root_type)
