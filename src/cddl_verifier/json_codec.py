"""Conversion between CBOR and JSON.

Run ``python -m cddl_verifier.json_codec --help`` for the command-line
converter.
"""

from ._json_codec import (
    CBORJSONEncoder,
    cbor_file_to_json_file,
    cbor_to_json,
    json_file_to_cbor_file,
    json_to_cbor,
    main,
)

__all__ = [
    "CBORJSONEncoder",
    "cbor_file_to_json_file",
    "cbor_to_json",
    "json_file_to_cbor_file",
    "json_to_cbor",
]

if __name__ == "__main__":
    main()
