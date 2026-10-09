"""Exception types shared by the public API and the internal CDDL parser."""


class SchemaError(ValueError):
    """Raised when a CDDL schema cannot be read or parsed."""
