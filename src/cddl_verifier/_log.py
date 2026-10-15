"""Logging for the command line: the colours, the formatter and the stderr handler.

As a library the package emits nothing by default; the CLI (``_cli.main``) attaches a
coloured stderr handler and removes it again. Split from ``_analyzer`` (#83).
"""

import logging
import sys


# ANSI color codes for terminal output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Logging level colors
    DEBUG = '\033[36m'      # Cyan
    INFO = '\033[32m'       # Green
    WARNING = '\033[33m'    # Yellow
    ERROR = '\033[31m'      # Red
    
    # Semantic colors
    CBOR = '\033[35m'       # Magenta for CBOR hex
    CDDL = '\033[34m'       # Blue for CDDL types
    MATCH = '\033[32m'      # Green for matches
    MISMATCH = '\033[31m'   # Red for mismatches

class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support."""
    
    FORMATS = {
        logging.DEBUG: f'{Colors.DEBUG}[DEBUG]{Colors.RESET} %(message)s',
        logging.INFO: f'{Colors.INFO}[INFO]{Colors.RESET} %(message)s',
        logging.WARNING: f'{Colors.WARNING}[WARNING]{Colors.RESET} %(message)s',
        logging.ERROR: f'{Colors.ERROR}[ERROR]{Colors.RESET} %(message)s',
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, '[%(levelname)s] %(message)s')
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# Configure logging. As a library the package emits nothing by default; the
# CLI (main) attaches a coloured stderr handler.
logger = logging.getLogger('cddl_verifier')
logger.addHandler(logging.NullHandler())


class _StderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr``."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def _configure_cli_logging(verbose: bool) -> None:
    """Attach the CLI's coloured stderr handler (once) and set the level."""
    if not any(isinstance(h, _StderrHandler) for h in logger.handlers):
        cli_handler = _StderrHandler()
        cli_handler.setFormatter(ColoredFormatter())
        logger.addHandler(cli_handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def _reset_cli_logging() -> None:
    """Undo :func:`_configure_cli_logging` so library use stays silent."""
    for h in [h for h in logger.handlers if isinstance(h, _StderrHandler)]:
        logger.removeHandler(h)
    logger.setLevel(logging.NOTSET)
