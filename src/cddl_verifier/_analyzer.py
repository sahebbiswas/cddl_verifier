#!/usr/bin/env python3
"""
CBOR-CDDL analyzer: ``CBORAnalyzer`` checks decoded CBOR against a parsed CDDL schema.

Internal implementation module of the ``cddl_verifier`` package. Use the
public ``cddl_verifier`` API (``validate``, ``Validator``) instead.

``CBORAnalyzer`` is composed from one mixin per concern in ``_validator`` (#83):

* ``reporting``: breadcrumbs, locations, byte offsets, error and diagnostic reports
* ``validation``: ``validate`` and the root and type-expression checks
* ``structures``: tags, constraints, and the members of maps and arrays
* ``containers``: arrays, maps, groups and repeated choices
* ``values``: one value against one type, set controls, parentheses, choices
* ``primitives``: primitive types, ranges and literals
* ``textcontrols``: ``.abnf``, ``.printf``, ``.join`` and the conversions
* ``embedded``: byte strings that hold CBOR

This module keeps the state they share (``__init__``). ``CDDLParser`` lives in ``_schema``,
``EDNGenerator`` in ``_edn`` and the command line in ``_cli``; the old import paths still work.
"""


from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from ._decode import SimpleCBORDecoder  # noqa: F401 (re-exported)
from ._log import Colors, logger  # noqa: F401 (re-exported)
from ._schema import CDDLParser
from ._validator.containers import ContainersMixin
from ._validator.embedded import EmbeddedMixin
from ._validator.primitives import PrimitivesMixin
from ._validator.reporting import ReportingMixin
from ._validator.structures import StructuresMixin
from ._validator.support import _CheckError, _EmbeddedFrame, _NO_STEP  # noqa: F401 (_CheckError, _NO_STEP: re-exported)
from ._validator.textcontrols import TextControlsMixin
from ._validator.validation import ValidationMixin
from ._validator.values import ValuesMixin


def _feature_set(features: Optional[Iterable[str]]) -> Optional[FrozenSet[str]]:
    """The enabled ``.feature`` names as a frozen set; ``None`` stays ``None`` (all enabled)."""
    if features is None:
        return None
    if isinstance(features, (str, bytes)):
        raise TypeError("features is a collection of feature names, not one string; "
                        "pass {'name'} for a single feature")
    names = frozenset(features)
    for name in names:
        if not isinstance(name, str):
            raise TypeError(f"feature names are text strings, not {type(name).__name__}")
    return names


class CBORAnalyzer(ReportingMixin, ValidationMixin, StructuresMixin, ContainersMixin, ValuesMixin, PrimitivesMixin,
                   TextControlsMixin, EmbeddedMixin):
    """Validate decoded CBOR data against a parsed CDDL schema.

    Uses a :class:`CDDLParser` instance for all schema lookups.  Call
    :meth:`validate` with decoded Python data and the root CDDL type name;
    inspect :meth:`get_errors` for a list of human-readable error messages
    when validation fails.

    Type checking enforced
    ----------------------
    * ``tstr`` / ``bstr`` — Python ``str`` / ``bytes`` (with optional
      ``.size`` constraints enforced)
    * ``uint`` — non-negative Python ``int`` (``bool`` rejected)
    * ``int``  — any Python ``int`` (``bool`` rejected)
    * ``bool`` — Python ``bool`` only (``int`` rejected)
    * ``null`` / ``nil`` — Python ``None`` only
    * ``float`` — Python ``float`` (``int`` rejected)
    * Required field presence and optional field absence
    * Unknown map keys (not in schema) → validation error
    * Array ``+`` occurrence → at-least-one element required
    * Array element primitive types (for ``[ + type ]`` / ``[ * type ]``)
    """

    # Shared by ValidationMixin (root rules) and ValuesMixin (choices): the primitives a rule
    # can name directly.
    _ROOT_PRIMITIVES = frozenset(
        {'uint', 'int', 'nint', 'biguint', 'bignint', 'bool', 'nil', 'null', 'float',
         'tstr', 'bstr', 'any', 'float16', 'float32', 'float64'})

    def __init__(self, cddl_parser: CDDLParser, unique_choices: bool = False,
                 features: Optional[Iterable[str]] = None):
        """Initialise the analyzer with a parsed CDDL schema.

        Args:
            cddl_parser: A :class:`CDDLParser` instance holding the schema.
            unique_choices: Report data that matches more than one structured
                alternative (a map, array or tag) of a choice, instead of
                accepting it (#74).
            features: The ``.feature`` names (RFC 9165 section 4) that are enabled.
                ``None`` (the default) enables every feature; a set, even an empty
                one, enables only the names in it, and ``T .feature "x"`` then matches
                nothing while ``"x"`` is not in it (#232).
        """
        self.cddl = cddl_parser
        self.unique_choices = unique_choices
        self.features: Optional[FrozenSet[str]] = _feature_set(features)
        #: The features the parts of the data that matched have used so far; a part that
        #: is tried and does not match takes its entries back (``_check_value``).
        self._feature_trail: List[str] = []

    @property
    def features_used(self) -> Tuple[str, ...]:
        """The ``.feature`` names the parts of the data that matched used in the last
        :meth:`validate`, in the order met and once each (RFC 9165 section 4)."""
        return tuple(dict.fromkeys(self._feature_trail))
        self.validation_errors: List[str] = []
        self.breadcrumb: List[str] = []  # Track CDDL path for logging
        self.cbor_bytes: Optional[bytes] = None  # Raw CBOR bytes for offset tracking
        self.offset_map: Dict[int, int] = {}  # Map data id() to byte offset
        # Structured diagnostics (#75): where in the data each breadcrumb
        # component sits, the rules being checked, and the item offsets
        self._data_path: List[Any] = []
        self._rules: List[str] = []
        self._locations = None  # CBORLocations, decoded from cbor_bytes on first use
        self._locations_tried = False
        # One frame per byte string whose CBOR is being checked, innermost last (#154)
        self._embedded_frames: List[_EmbeddedFrame] = []
        # What was worked out about schema nodes, by kind (``_remembered``, #85)
        self._memo: Dict[str, Dict[int, Any]] = {}


def __getattr__(name):
    """The command-line layer (``_cli``) and ``EDNGenerator`` (``_edn``) moved out (#83): keep
    the old import path working.

    Lazy, because both import this module."""
    if name in _CLI_NAMES:
        from . import _cli
        return getattr(_cli, name)
    if name in _EDN_NAMES:
        from . import _edn
        return getattr(_edn, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CLI_NAMES = frozenset({'load_cddl', 'load_cbor', 'main', '_exit_json', '_schema_problems', '_run_cli'})
_EDN_NAMES = frozenset({'EDNGenerator'})


if __name__ == '__main__':
    from ._cli import main
    main()
