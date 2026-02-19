"""pytest configuration: add the repo root to sys.path.

This makes the source modules (cbor_cddl_analyzer, simple_cbor, cbor_json)
importable when tests are run from any working directory.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
