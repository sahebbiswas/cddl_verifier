"""cddl-verifier: validate CBOR data against CDDL schemas and render EDN.

Public API::

    from cddl_verifier import validate, Validator

    result = validate(schema_text, cbor_bytes, root_type="corim-map")
    if not result.valid:
        for diagnostic in result.diagnostics:
            print(diagnostic)

CBOR helpers live in :mod:`cddl_verifier.cbor` and JSON conversion in
:mod:`cddl_verifier.json_codec`. Modules whose names start with an underscore
are internal and may change in any release.

The supported CDDL (RFC 8610) and CBOR (RFC 8949) features are a practical
subset; see the project README for what is and is not covered.
"""

from ._api import Diagnostic, SchemaError, ValidationResult, Validator, validate
from ._cbor import CBORDecodeError
from ._version import __version__

__all__ = [
    "CBORDecodeError",
    "Diagnostic",
    "SchemaError",
    "ValidationResult",
    "Validator",
    "__version__",
    "validate",
]
