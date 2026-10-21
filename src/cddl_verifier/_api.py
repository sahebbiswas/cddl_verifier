"""Public validation facade: :func:`validate`, :class:`Validator` and results.

This module is a thin layer over the internal parser and analyzer
(``_analyzer``). It is the stable entry point: the implementation underneath
can move to a standards-oriented CDDL AST without changing these signatures.
"""

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from . import _analyzer, _edn, _schema
from ._cddl import parse_cddl
from ._cddl.modules import ModuleLoader, link
from ._cddl.resolve import resolve_all
from ._cbor import CBOR, CBORDecodeError
from ._diagnostics import (JSON_FORMAT_VERSION, Diagnostic, decode_diagnostic, render_json,
                           render_text, schema_diagnostic)
from ._errors import SchemaError

__all__ = ["Diagnostic", "SchemaError", "ValidationResult", "Validator", "render_json",
           "render_text", "schema_diagnostics", "validate"]

#: CDDL schema input: schema text (``str``) or a path to a ``.cddl`` file.
SchemaSource = Union[str, "os.PathLike[str]"]

_BYTES_TYPES = (bytes, bytearray, memoryview)
_EDN_FORMATS = ("keyindex", "keyname", "both")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a validation.

    ``bool(result)`` is ``result.valid``. ``data`` holds the decoded value
    (``None`` if decoding failed). ``features_used`` names, in the order they
    were met, the features (RFC 9165 ``.feature``) of the parts of the data
    that matched the schema; it is empty for an invalid result, and for a
    schema that has no ``.feature``.
    """

    valid: bool
    root_type: Optional[str]
    diagnostics: Tuple[Diagnostic, ...] = ()
    data: Any = field(default=None, compare=False, repr=False)
    features_used: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.valid

    @property
    def errors(self) -> Tuple[str, ...]:
        """Messages of the error diagnostics."""
        return tuple(d.message for d in self.diagnostics if d.severity == "error")

    def to_dict(self) -> Dict[str, Any]:
        """The outcome as JSON-ready data: ``valid``, ``root_type`` and ``diagnostics``, and
        ``features_used`` when a feature was used.

        Each diagnostic is :meth:`Diagnostic.to_dict`. The decoded ``data`` is
        not included.
        """
        document = {
            "format_version": JSON_FORMAT_VERSION,
            "valid": self.valid,
            "root_type": self.root_type,
        }
        if self.features_used:
            document["features_used"] = list(self.features_used)
        document["diagnostics"] = [d.to_dict() for d in self.diagnostics]
        return document

    def to_json(self, indent: Optional[int] = 2) -> str:
        """The outcome as a JSON document (see :meth:`to_dict`)."""
        extra = {"features_used": list(self.features_used)} if self.features_used else {}
        return render_json(self.diagnostics, indent=indent, valid=self.valid,
                           root_type=self.root_type, **extra)

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


def schema_diagnostics(schema: SchemaSource, *,
                       modules: Optional[Mapping[str, SchemaSource]] = None,
                       module_path: Sequence[Union[str, "os.PathLike[str]"]] = (),
                       strict_modules: bool = False) -> Tuple[Diagnostic, ...]:
    """The problems in a CDDL schema, as diagnostics; none for a schema that is fine.

    This is what :class:`Validator` raises :class:`SchemaError` for, returned
    instead so that it renders (:func:`render_text`, :func:`render_json`) like
    any other finding, and with every problem the check could find rather than
    the first. Each has ``code == "schema"`` and ``category == "schema"``, the
    ``rule`` it is in, the position as ``offset`` / ``end_offset`` into the
    schema text and as ``line``, ``column`` and ``schema_location``.

    A text that is not valid CDDL gives that one syntax error. A schema that
    parses is checked in full: a problem in a rule's contents (an undefined
    name, a control that does not apply) does not stop the check of the rest,
    so each is reported, in the order the rules and their members are written.
    A problem in the names (a rule defined twice, a cycle) is reported alone.

    With ``strict_modules=True`` every import the check cannot resolve is a
    diagnostic at its ``;# import`` or ``;# include`` line (#229).

    Args:
        schema, modules, module_path, strict_modules: As for :class:`Validator`.

    Raises:
        TypeError: *schema* is neither ``str`` nor ``os.PathLike``.
    """
    try:
        text = _read_schema(schema)
    except SchemaError as e:
        return (schema_diagnostic(e),)
    name = os.fspath(schema) if isinstance(schema, os.PathLike) else None
    search = list(module_path)
    if name is not None:
        search.append(Path(name).parent)
    try:
        linked = link(parse_cddl(text, source_name=name),
                      ModuleLoader(modules, search, strict=strict_modules))
    except SchemaError as e:
        return tuple(schema_diagnostic(error) for error in getattr(e, "errors", (e,)))
    return tuple(schema_diagnostic(e) for e in resolve_all(linked.schema, linked.imports)[1])


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
        comment_names: Name a member after its trailing comment, so ``0: tstr ; name``
            is "name" in messages and EDN annotations. Off by default: comments carry no
            meaning in CDDL (RFC 8610), so a member is named by its key, a bareword key
            or the label of ``&(name: 0)``. Turn it on for a schema that documents its
            members in comments (#108).
        strict_modules: Fail closed when an import cannot be resolved. By default
            the names of a module that is not found match any value, and an
            unprefixed ``;# import`` or ``;# include`` that is not found turns off
            the check for undefined names in the whole schema. With
            ``strict_modules=True`` any ``;# import`` or ``;# include`` that is not
            found, used or not, raises :class:`SchemaError` at that line (#229).
        features: The ``.feature`` names (RFC 9165 section 4) that are enabled. By
            default every feature is enabled and ``T .feature "x"`` is just ``T``.
            With a collection of names, even an empty one, only those features are
            enabled: ``T .feature "x"`` matches only while ``"x"`` is in it, and the
            diagnostic names the feature that is not. A choice falls through to
            its other alternatives (``T .feature "x" / U``). RFC 9165 leaves open what
            a tool does with a feature that is not enabled, so this is a choice of
            this tool (#232). A single string is refused (``{"x"}``, not ``"x"``).

    Attributes:
        warnings: What the schema relies on without checking: an import whose
            module was not found keeps its names unchecked (they match any
            value). The same messages are issued as ``UserWarning``.

    Raises:
        SchemaError: The schema file cannot be read or the schema cannot be
            parsed. For text that is not valid CDDL the message is
            ``file:line:column: problem`` and the exception has ``line`` and
            ``column`` attributes. So is an unresolved import with
            ``strict_modules=True``.
        TypeError: *schema* is neither ``str`` nor ``os.PathLike``.
    """

    def __init__(self, schema: SchemaSource, *,
                 modules: Optional[Mapping[str, SchemaSource]] = None,
                 module_path: Sequence[Union[str, "os.PathLike[str]"]] = (),
                 unique_choices: bool = False,
                 comment_names: bool = False,
                 strict_modules: bool = False,
                 features: Optional[Iterable[str]] = None):
        text = _read_schema(schema)
        name = os.fspath(schema) if isinstance(schema, os.PathLike) else None
        self._unique_choices = unique_choices
        self._features = _analyzer._feature_set(features)
        search = list(module_path)
        if name is not None:
            search.append(Path(name).parent)
        loader = ModuleLoader(modules, search, strict=strict_modules)
        try:
            self._parser = _schema.CDDLParser(text, source_name=name, loader=loader,
                                              comment_names=comment_names)
        except SchemaError:
            raise  # syntax errors already say where: "file:line:col: message"
        except Exception as e:  # noqa: BLE001 - anything else is a parser bug
            raise SchemaError(f"cannot parse CDDL schema: {e}") from e
        self._parser.features = self._features  # EDN output picks choices by the same rule
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

    def validate(self, data: Any, root_type: Optional[str] = None, *,
                 require_deterministic: bool = False) -> ValidationResult:
        """Validate *data* against the schema.

        Args:
            data: CBOR-encoded bytes (``bytes``, ``bytearray`` or
                ``memoryview``), or an already-decoded Python value. Bytes are
                always decoded as CBOR; to validate a decoded byte string
                against a ``bstr`` rule, pass its CBOR encoding.
            root_type: Name of the CDDL rule to validate against. Defaults to
                the first rule in the schema.
            require_deterministic: Also require the CBOR bytes to be
                deterministically encoded (RFC 8949 §4.2.1; see
                :func:`cddl_verifier.cbor.decode`). Bytes that are not give an
                invalid result with a ``"decode"`` diagnostic at the offset of
                the first violation, before the schema is checked.

        Returns:
            A :class:`ValidationResult`. CBOR that cannot be decoded is
            reported as an invalid result with a ``"decode"`` diagnostic, not
            raised.

        Raises:
            TypeError: ``require_deterministic`` is true and *data* is an
                already-decoded value, which has no encoding to check.
        """
        root = self._resolve_root(root_type)
        cbor_bytes = None
        if require_deterministic and not isinstance(data, _BYTES_TYPES):
            raise TypeError("require_deterministic checks how CBOR bytes are encoded; "
                            "pass the bytes, not a decoded value")
        if isinstance(data, _BYTES_TYPES):
            cbor_bytes = bytes(data)
            try:
                data = CBOR.loads(cbor_bytes, keep_encoding=True,
                                  require_deterministic=require_deterministic)
            except CBORDecodeError as e:
                return ValidationResult(False, root, (decode_diagnostic(e, cbor_bytes),), None)
        analyzer = _analyzer.CBORAnalyzer(self._parser, unique_choices=self._unique_choices,
                                          features=self._features)
        ok = analyzer.validate(data, root, cbor_bytes)
        diagnostics = analyzer.get_diagnostics()
        valid = bool(ok) and not diagnostics
        used = tuple(dict.fromkeys(analyzer._feature_trail)) if valid else ()
        return ValidationResult(valid, root, diagnostics, data, used)

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
        generator = _edn.EDNGenerator(self._parser, edn_format=edn_format)
        return generator.generate(data, root, annotate)


def validate(schema: SchemaSource, data: Any,
             root_type: Optional[str] = None, *,
             modules: Optional[Mapping[str, SchemaSource]] = None,
             module_path: Sequence[Union[str, "os.PathLike[str]"]] = (),
             unique_choices: bool = False,
             comment_names: bool = False,
             strict_modules: bool = False,
             features: Optional[Iterable[str]] = None,
             require_deterministic: bool = False) -> ValidationResult:
    """Validate *data* against a CDDL *schema*.

    Shorthand for ``Validator(schema).validate(data, root_type)``; build a
    :class:`Validator` once to check many items against the same schema.
    """
    return Validator(schema, modules=modules, module_path=module_path,
                     unique_choices=unique_choices, comment_names=comment_names,
                     strict_modules=strict_modules, features=features).validate(
        data, root_type, require_deterministic=require_deterministic)
