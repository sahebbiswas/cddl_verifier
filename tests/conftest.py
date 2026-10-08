"""pytest configuration: make ``cddl_verifier`` importable.

Tests import the installed package when there is one (CI installs the built
wheel or sdist and runs the suite against it). Without an installation, the
source tree under ``src/`` is used instead, so ``pytest`` works straight from a
checkout. ``pip install -e ".[test]"`` is the recommended development setup.

Set ``CDDL_VERIFIER_REQUIRE_INSTALLED=1`` to fail fast if the package would be
imported from the source tree instead of an installation.
"""
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

try:
    import cddl_verifier
except ImportError:
    if os.environ.get("CDDL_VERIFIER_REQUIRE_INSTALLED"):
        raise
    sys.path.insert(0, str(SRC))
    import cddl_verifier

if os.environ.get("CDDL_VERIFIER_REQUIRE_INSTALLED"):
    _location = Path(cddl_verifier.__file__).resolve()
    if SRC in _location.parents:
        raise RuntimeError(
            f"cddl_verifier was imported from the source tree ({_location}), "
            "not from an installed distribution")
