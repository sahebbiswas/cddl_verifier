"""Command-line interface: the ``cddl-verify`` console script.

Usage: ``cddl-verify SCHEMA.cddl DATA.cbor [--type NAME] [--output FILE]``.
Run ``cddl-verify --help`` for all options.
"""

from ._analyzer import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
