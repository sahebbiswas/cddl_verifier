"""Structured validation diagnostics (#75): the data model and its renderers.

A :class:`Diagnostic` is one finding. Besides the readable ``message`` it
carries where the problem is (``path`` in the data, ``offset`` in the CBOR
bytes, ``schema_path`` and ``schema_location`` in the CDDL), what was expected
and what was found, a bounded hex excerpt of the offending bytes, and a
``severity`` and ``category`` to filter on.

:func:`render_text` and :func:`render_json` turn a sequence of diagnostics
into text or JSON. The JSON layout is versioned by :data:`JSON_FORMAT_VERSION`.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

__all__ = [
    "CATEGORIES",
    "Diagnostic",
    "JSON_FORMAT_VERSION",
    "MAX_HEX_BYTES",
    "SEVERITIES",
    "render_json",
    "render_text",
]

#: Version of the layout :func:`render_json` and :meth:`Diagnostic.to_dict` produce.
JSON_FORMAT_VERSION = 1

#: Severities a diagnostic can have, most serious first.
SEVERITIES = ("error", "warning", "info")

#: Categories of diagnostic. ``decode``: the CBOR cannot be decoded. ``schema``:
#: the schema or root type cannot be used. ``tag``: a CBOR tag is missing or
#: wrong. ``type``: the value has the wrong CBOR type. ``value``: the type is
#: right but a control (``.size``, ``.regexp``, a range, a literal) fails.
#: ``missing``: a required member is absent. ``unknown``: a member the schema
#: does not allow. ``occurrence``: too few or too many array elements or
#: entries. ``nested``: embedded CBOR (``.cbor``) cannot be checked. ``other``
#: is anything else.
CATEGORIES = ("decode", "schema", "tag", "type", "value", "missing", "unknown",
              "occurrence", "nested", "other")

#: Longest hex excerpt, in bytes, a diagnostic carries. Longer items are cut
#: and flagged with ``hex_truncated``, so a diagnostic never echoes a large or
#: secret payload.
MAX_HEX_BYTES = 16


def _step_text(step: Any) -> str:
    """One path step as it appears in a path string (``/1/name/0``)."""
    if isinstance(step, bytes):
        return f"h'{step.hex()}'"
    if step is None:
        return "null"
    value = getattr(step, "value", step)  # CBORKey wraps the real key
    if isinstance(value, bytes):
        return f"h'{value.hex()}'"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _step_json(step: Any) -> Any:
    """One path step as a JSON value: ints and strings stay, the rest are text."""
    if isinstance(step, bool):
        return "true" if step else "false"
    if isinstance(step, (int, str)):
        return step
    return _step_text(step)


@dataclass(frozen=True)
class Diagnostic:
    """One finding produced while checking data against a schema.

    Attributes:
        message:  Human-readable description.
        code:     ``"decode"`` when the CBOR input could not be decoded,
                  ``"validation"`` when decoded data does not match the schema.
        severity: ``"error"``, ``"warning"`` or ``"info"``. Every diagnostic
                  is an error in this release.
        offset:   Byte offset into the CBOR input of the offending item, when
                  known. For a missing member it is the map that lacks it.
        category: The kind of problem; one of :data:`CATEGORIES`.
        path:     Where the item is in the data: map keys and array indexes
                  from the root, as the decoder returns them.
        schema_path: The CDDL rules and members the check went through, from
                  the root type down.
        rule:     The CDDL rule being checked when the problem was found.
        schema_location: ``file:line:column`` of that rule's definition.
        expected: What the schema asks for, as short text.
        actual:   What the data has, as short text.
        end_offset: Byte offset just past the offending item.
        hex:      Hex excerpt of the offending bytes, cut at
                  :data:`MAX_HEX_BYTES` bytes (see ``hex_truncated``).
        hex_truncated: ``True`` when ``hex`` is a prefix of a longer item.
    """

    message: str
    code: str = "validation"
    severity: str = "error"
    offset: Optional[int] = None
    category: str = "other"
    path: Tuple[Any, ...] = ()
    schema_path: Tuple[str, ...] = ()
    rule: Optional[str] = None
    schema_location: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    end_offset: Optional[int] = None
    hex: Optional[str] = None
    hex_truncated: bool = False

    def __str__(self) -> str:
        return self.message

    @property
    def path_text(self) -> str:
        """The data path as text: ``/`` for the root, else ``/1/0/name``."""
        return "/" + "/".join(_step_text(step) for step in self.path)

    @property
    def schema_path_text(self) -> str:
        """The schema path as text: ``/corim-map/signers/[0]``."""
        return "/" + "/".join(self.schema_path)

    def to_dict(self) -> Dict[str, Any]:
        """The diagnostic as plain JSON-ready data (see :data:`JSON_FORMAT_VERSION`).

        Every key is always present; unknown values are ``None``.
        """
        return {
            "severity": self.severity,
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "path": [_step_json(step) for step in self.path],
            "offset": self.offset,
            "end_offset": self.end_offset,
            "hex": self.hex,
            "hex_truncated": self.hex_truncated,
            "rule": self.rule,
            "schema_path": list(self.schema_path),
            "schema_location": self.schema_location,
            "expected": self.expected,
            "actual": self.actual,
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        """The diagnostic as a JSON object."""
        return json.dumps(self.to_dict(), indent=indent)

    def format(self) -> str:
        """The message plus where the problem is, over several lines."""
        lines = [f"{self.severity}[{self.category}]: {self.message}"]
        where = []
        if self.category != "decode" and (self.path or self.offset is not None):
            where.append(f"data {self.path_text}")
        if self.offset is not None:
            span = f"offset {self.offset}"
            if self.end_offset is not None and self.end_offset > self.offset + 1:
                span += f"..{self.end_offset}"
            where.append(span)
        if where:
            lines.append("  at " + ", ".join(where))
        if self.rule is not None or self.schema_location is not None:
            rule = f"rule {self.rule}" if self.rule is not None else "rule"
            if self.schema_location is not None:
                rule += f" ({self.schema_location})"
            lines.append("  in " + rule)
        if self.expected is not None or self.actual is not None:
            parts = []
            if self.expected is not None:
                parts.append(f"expected {self.expected}")
            if self.actual is not None:
                parts.append(f"found {self.actual}")
            lines.append("  " + ", ".join(parts))
        if self.hex is not None:
            lines.append(f"  bytes {self.hex}{' ...' if self.hex_truncated else ''}")
        return "\n".join(lines)


def render_text(diagnostics: Iterable[Diagnostic], *, verbose: bool = False) -> str:
    """Render *diagnostics* as text, one finding per paragraph.

    By default each diagnostic is its message on one line, as the
    ``cddl-verify`` command prints it. With ``verbose=True`` each one also
    shows its severity, category, data path, byte offset, rule, expected and
    found values, and bytes (see :meth:`Diagnostic.format`).
    """
    if verbose:
        return "\n".join(diagnostic.format() for diagnostic in diagnostics)
    return "\n".join(diagnostic.message for diagnostic in diagnostics)


def render_json(diagnostics: Iterable[Diagnostic], *, indent: Optional[int] = 2,
                **extra: Any) -> str:
    """Render *diagnostics* as a JSON document.

    The document is ``{"format_version": 1, "diagnostics": [...]}``; each
    element is :meth:`Diagnostic.to_dict`. Keyword arguments other than
    ``indent`` are added as top-level members before ``diagnostics``
    (``valid=False``, ``root_type="corim-map"``).

    Raises:
        TypeError: ``format_version`` or ``diagnostics`` is passed as a
            keyword argument; the renderer owns those two members.
    """
    reserved = sorted({"format_version", "diagnostics"} & set(extra))
    if reserved:
        raise TypeError(f"render_json sets {', '.join(reserved)} itself; it cannot be passed")
    document: Dict[str, Any] = {"format_version": JSON_FORMAT_VERSION}
    document.update(extra)
    document["diagnostics"] = [diagnostic.to_dict() for diagnostic in diagnostics]
    return json.dumps(document, indent=indent)


_CLASSIFY_RULES = (
    ('schema', re.compile(r"not found in CDDL|resolves to|Invalid \.regexp pattern|cannot check")),
    ('tag', re.compile(r"\bCBOR tag\b")),
    ('missing', re.compile(r"^Missing required field")),
    ('unknown', re.compile(r"^Unknown fields")),
    ('nested', re.compile(r"nested CBOR|Embedded CBOR|embedded CBOR|containing CBOR")),
    ('occurrence', re.compile(
        r"at least one|must not be empty|missing element|unexpected element|"
        r"matches no entry|entries? with key|\+ occurrence")),
    ('value', re.compile(r"violates|does not satisfy|\.regexp|\.size|\.(?:ge|gt|le|lt)\b|bignum")),
    ('type', re.compile(r"[Ee]xpected|wrong type|[Tt]ype mismatch|matches none of")),
)
_EXPECTED_GOT = re.compile(r"expected (?P<expected>[^,]+?), got (?P<actual>.+?)$")


def classify_message(message: str) -> str:
    """The diagnostic category of an analyzer message."""
    for category, pattern in _CLASSIFY_RULES:
        if pattern.search(message):
            return category
    return 'other'


#: CBOR names for the Python type names analyzer messages report.
_CBOR_NAMES = {'str': 'tstr', 'bytes': 'bstr', 'list': 'array', 'dict': 'map',
               'NoneType': 'null', 'CBORTag': 'tag'}


def expected_and_found(message: str) -> Tuple[Optional[str], Optional[str]]:
    """Read ``expected X, got Y`` out of *message*; ``(None, None)`` if it has no such part.

    A Python type name in the ``got`` part (``str``, ``NoneType``) is given by
    its CBOR name (``tstr``, ``null``).
    """
    found = _EXPECTED_GOT.search(message)
    if found:
        actual = found.group('actual')
        return found.group('expected'), _CBOR_NAMES.get(actual, actual)
    return None, None


def decode_diagnostic(error: Any, cbor_bytes: bytes) -> Diagnostic:
    """The diagnostic for CBOR that cannot be decoded: its offset and the bytes there.

    *error* is a ``CBORDecodeError``.
    """
    offset = error.offset
    excerpt = cbor_bytes[offset:offset + MAX_HEX_BYTES] if 0 <= offset < len(cbor_bytes) else b""
    return Diagnostic(
        str(error), code="decode", offset=offset, category="decode",
        hex=excerpt.hex(" ") if excerpt else None,
        hex_truncated=bool(excerpt) and len(cbor_bytes) - offset > MAX_HEX_BYTES)


class _Finding(str):
    """An error message that carries its structured :class:`Diagnostic`.

    The analyzer keeps its errors as a list of ``str``; each element is a
    ``_Finding`` so the list can still be copied, sliced and compared as
    strings while ``.diagnostic`` holds everything else about the finding.
    """

    diagnostic: Diagnostic

    def __new__(cls, message: str, diagnostic: Diagnostic):
        obj = super().__new__(cls, message)
        obj.diagnostic = diagnostic
        return obj

    def __reduce__(self):
        return (_Finding, (str(self), self.diagnostic))


def diagnostic_of(error: str) -> Diagnostic:
    """The :class:`Diagnostic` of an analyzer error (a plain message gets a bare one)."""
    found = getattr(error, "diagnostic", None)
    if found is not None:
        return found
    return Diagnostic(str(error))
