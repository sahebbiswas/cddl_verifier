"""Errors raised by the CDDL lexer, parser and resolver."""

from typing import Optional, Tuple

from .._errors import SchemaError
from .ast import Source, Span


class CDDLSyntaxError(SchemaError):
    """A schema that does not match the CDDL grammar.

    Attributes:
        message:  What is wrong, without the location.
        span:     Where in the source the problem was found.
        source:   The schema being parsed.
        expected: Descriptions of the tokens that would have been accepted.
        line, column: 1-based position of ``span.start``.
    """

    def __init__(self, message: str, span: Span, source: Source,
                 expected: Tuple[str, ...] = ()):
        self.message = message
        self.span = span
        self.source = source
        self.expected = tuple(expected)
        self.line, self.column = source.line_col(span.start)
        super().__init__(str(self))

    def __str__(self) -> str:
        name = self.source.name or '<schema>'
        return f"{name}:{self.line}:{self.column}: {self.message}"

    def __reduce__(self):
        return (self.__class__, (self.message, self.span, self.source, self.expected))


def describe(kind: str, text: Optional[str] = None) -> str:
    """Describe a token for an error message."""
    if kind == 'EOF':
        return 'end of input'
    if kind in ('ID', 'INT', 'FLOAT', 'TEXT', 'BYTES', 'CTLOP', 'OCCUR', 'HASH'):
        return repr(text)
    return repr(kind)


class CDDLSemanticError(SchemaError):
    """A schema that parses but does not make sense (#70).

    Raised by the resolver for undefined or conflicting names, generic
    arguments that do not match the parameters, unknown or misapplied control
    operators, empty occurrences and ranges, and sockets used the wrong way.
    It has the same attributes as :class:`CDDLSyntaxError` except
    ``expected``.
    """

    def __init__(self, message: str, span: Span, source: Optional[Source]):
        self.message = message
        self.span = span
        self.source = source if source is not None else Source('')
        self.line, self.column = self.source.line_col(span.start)
        super().__init__(str(self))

    def __str__(self) -> str:
        name = self.source.name or '<schema>'
        return f"{name}:{self.line}:{self.column}: {self.message}"

    def __reduce__(self):
        return (self.__class__, (self.message, self.span, self.source))
