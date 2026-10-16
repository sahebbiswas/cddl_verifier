"""Constants, small helpers and the error types shared by the analyzer's mixins.

Moved verbatim out of ``_analyzer`` (#83).
"""

from typing import Any, NamedTuple, Optional, Tuple

from .._cbor import CBORKey
from .._cbor_values import CBORSimple, CBORTag, tag_tuple_error, UNDEFINED
from .._cddl.ast import Name, Type


_FLOAT_WIDTHS = {'float16': 25, 'float32': 26, 'float64': 27}


def _tuple_hint(value: Any) -> str:
    """What to add to "the data is not tagged" when *value* is a tuple shaped like the old tag."""
    return (" (a (tag, value) tuple is not a tag: use CBORTag)"
            if tag_tuple_error(value) is not None else "")


def _entries_wanted(low, high) -> str:
    """``at least 2 entries``, ``exactly 1 entry``... for a member's occurrence."""
    if high is None:
        text, count = f"at least {low}", low
    elif low == high:
        text, count = f"exactly {low}", low
    elif low == 0:
        text, count = f"at most {high}", high
    else:
        text, count = f"{low} to {high}", high
    return f"{text} {'entry' if count == 1 else 'entries'}"


# CBOR Major Type Constants (RFC 8949)
MAJOR_TYPE_UINT = 0      # Unsigned integer
MAJOR_TYPE_NINT = 1      # Negative integer
MAJOR_TYPE_BSTR = 2      # Byte string
MAJOR_TYPE_TSTR = 3      # Text string
MAJOR_TYPE_ARRAY = 4     # Array
MAJOR_TYPE_MAP = 5       # Map
MAJOR_TYPE_TAG = 6       # Tagged item
MAJOR_TYPE_SIMPLE = 7    # Simple values (bool, null, float)

# Simple Value Constants
SIMPLE_FALSE = 20
SIMPLE_TRUE = 21
SIMPLE_NULL = 22


_CONTROL_CHARS = {c: f"\\u{c:04x}" for c in (*range(0x20), *range(0x7f, 0xa0), 0x2028, 0x2029)}
_UNKNOWN_TYPE = Type((Name('unknown'),))

#: Marks a breadcrumb component (a rule name) that is not a step in the data.
_NO_STEP = object()


#: What each byte conversion control reads, for the message about a text it does not accept.
_BYTE_TEXT_FORMS = {
    'b64u': 'base64url text without padding', 'b64u-sloppy': 'base64url text without padding',
    'b64c': 'classic base64 text with padding', 'b64c-sloppy': 'classic base64 text with padding',
    'b32': 'base32 text without padding', 'h32': 'base32hex text without padding',
    'hex': 'hexadecimal text', 'hexlc': 'lower case hexadecimal text',
    'hexuc': 'upper case hexadecimal text', 'b45': 'base45 text',
}


class _CheckError(str):
    """An error found by :meth:`CBORAnalyzer._check_value`, with what is known about it (#153).

    It is a ``str``, so code that only formats or compares the message keeps
    working. The other attributes let a caller report it as a finding of its
    own, at the right item and with its own expected and actual, instead of
    wrapping the text in a message of its own.

    Attributes:
        expected, actual: What the schema asks for and what the data has.
        category: A category from ``_diagnostics.CATEGORIES``; guessed from the
            message when ``None``.
        path: Steps from the value that was checked to the item at fault
            (the key or index of each step); empty for the value itself.
        schema_path, rule: The rules entered below the one that was checked,
            and the innermost one; set for errors found by checking a named type.
        span: Byte span of the item in the input, when it is known
            (``located``); otherwise it is found from the path when reported.
        alternatives: For a value that matches none of a choice's
            alternatives: ``(alternative, error)`` for each one that was tried,
            or, for an error found by a nested run, their finished diagnostics.
    """

    expected: Optional[str]
    actual: Optional[str]
    category: Optional[str]
    path: Tuple[Any, ...]
    schema_path: Tuple[str, ...]
    rule: Optional[str]
    span: Optional[Tuple[int, int]]
    located: bool
    alternatives: Tuple[Any, ...]

    def __new__(cls, message: str, *, expected: Optional[str] = None, actual: Optional[str] = None,
                category: Optional[str] = None, path: Tuple[Any, ...] = (),
                schema_path: Tuple[str, ...] = (), rule: Optional[str] = None,
                span: Optional[Tuple[int, int]] = None, located: bool = False,
                alternatives: Tuple[Any, ...] = ()):
        obj = super().__new__(cls, message)
        obj.expected, obj.actual, obj.category = expected, actual, category
        obj.path, obj.schema_path, obj.rule = tuple(path), tuple(schema_path), rule
        obj.span, obj.located, obj.alternatives = span, located, tuple(alternatives)
        return obj

    def reworded(self, message: str) -> '_CheckError':
        """The same finding under a new message (a caller's wording of it)."""
        return _CheckError(message, expected=self.expected, actual=self.actual,
                           category=self.category, path=self.path, schema_path=self.schema_path,
                           rule=self.rule, span=self.span, located=self.located,
                           alternatives=self.alternatives)


class _EmbeddedFrame(NamedTuple):
    """A byte string whose embedded CBOR is being checked (#154)."""
    depth: int  # steps in the data path up to the byte string
    base: int  # offset in the input of the embedded bytes
    locations: Any  # CBORLocations of the embedded bytes; None if unknown
    span: Optional[Tuple[int, int]]  # the byte string itself, in the input

def _cbor_type_name(value: Any) -> str:
    """Name *value* by its CBOR type (``uint``, ``tstr``, ``map``, ``tag 501``...)."""
    if isinstance(value, CBORKey):
        value = value.value
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if value is UNDEFINED:
        return 'undefined'
    if isinstance(value, CBORSimple):
        return repr(value)
    if isinstance(value, int):
        return 'uint' if value >= 0 else 'nint'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, str):
        return 'tstr'
    if isinstance(value, bytes):
        return 'bstr'
    if isinstance(value, dict):
        return 'map'
    if isinstance(value, CBORTag):
        return f'tag {value.tag}'
    if isinstance(value, (list, tuple)):
        return 'array'
    return type(value).__name__


def _mismatch_category(base_type: str, value: Any) -> str:
    """``value`` when *value* has the CBOR type *base_type* asks for but fails a
    control on it (a range, ``.regexp``), else ``type``."""
    if base_type in ('uint', 'int'):
        fits = (isinstance(value, int) and not isinstance(value, bool)
                and -2**64 <= value < 2**64 and not (base_type == 'uint' and value < 0))
        return 'value' if fits else 'type'
    if base_type == 'tstr':
        return 'value' if isinstance(value, str) else 'type'
    return 'type'


def _describe_value(value: Any) -> str:
    """The CBOR type of *value*, plus the value itself for numbers and booleans."""
    name = _cbor_type_name(value)
    if isinstance(value, (bool, int, float)) and not isinstance(value, CBORKey):
        return f"{name} {value!r}"
    return name
