"""Loading and running the conformance corpus (#80).

Shared by ``tests/test_corpus.py``, ``scripts/build_corpus.py`` and
``scripts/render_corpus.py``. The corpus is described in docs/CORPUS.md.
"""

import json
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"
SUITES = CORPUS / "suites"

try:
    import cddl_verifier  # noqa: F401
except ImportError:  # running from a checkout
    import sys
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import CBORDecodeError, SchemaError, Validator  # noqa: E402
from cddl_verifier import cbor as cbor_api  # noqa: E402

#: ``expect`` of a case: what the source says. ``schema-error`` cases have no data.
EXPECTATIONS = ("valid", "invalid", "schema-error")
#: ``status`` of a case: ``pass`` (this library agrees with the source), or why not.
STATUSES = ("pass", "gap", "divergence", "source-defect")
PROVENANCE = ("verbatim", "converted", "derived", "adapted", "constructed")


def load_suites() -> List[dict]:
    suites = []
    for path in sorted(SUITES.glob("*.json")):
        suite = json.loads(path.read_text(encoding="utf-8"))
        suite["_path"] = path
        suites.append(suite)
    return suites


def iter_cases(suites: Optional[List[dict]] = None) -> Iterator[Tuple[dict, dict]]:
    for suite in suites if suites is not None else load_suites():
        for entry in suite["cases"]:
            yield suite, entry


def _read(relative: str) -> str:
    return (CORPUS / relative).read_text(encoding="utf-8")


def schema_paths(suite: dict, entry: dict) -> Tuple[str, ...]:
    schema = entry.get("schema", suite.get("schema"))
    if schema is None:
        raise KeyError(f"case {entry['id']} names no schema and suite {suite['id']} has none")
    return (schema,) if isinstance(schema, str) else tuple(schema)


def module_paths(suite: dict, entry: dict) -> Tuple[Tuple[str, str], ...]:
    modules = entry.get("modules", suite.get("modules", {}))
    return tuple(sorted(modules.items()))


@lru_cache(maxsize=None)
def _validator(paths: Tuple[str, ...], modules: Tuple[Tuple[str, str], ...],
               features: Optional[Tuple[str, ...]] = None):
    """``(Validator, None)``, or ``(None, SchemaError)`` for a schema the library refuses."""
    text = "\n".join(_read(path) for path in paths)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return Validator(text, modules={name: _read(path) for name, path in modules},
                             features=features), None
    except SchemaError as error:
        return None, error


def run(suite: dict, entry: dict) -> Tuple[str, str]:
    """What this library says about a case: ``(verdict, detail)``.

    The verdict is ``valid`` or ``invalid`` for data, and ``schema-error`` when the
    schema is refused (also the answer for data checked against such a schema),
    or ``schema-ok`` for a case that expects a refusal and was not given one.
    """
    enabled = entry.get("features", suite.get("features"))  # the RFC 9165 features that are on
    validator, error = _validator(schema_paths(suite, entry), module_paths(suite, entry),
                                  None if enabled is None else tuple(enabled))
    if entry["expect"] == "schema-error":
        return ("schema-error", str(error)) if error else ("schema-ok", "")
    if error:
        return "schema-error", str(error)
    data = bytes.fromhex(entry["hex"])
    root = entry.get("root", suite.get("root"))
    deterministic = bool(entry.get("deterministic", suite.get("deterministic", False)))
    try:
        if entry.get("encoding", suite.get("encoding", "item")) == "sequence":
            data = cbor_api.decode_sequence(data, require_deterministic=deterministic)
            deterministic = False  # the items are decoded: the flag has done its work
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = validator.validate(data, root, require_deterministic=deterministic)
    except CBORDecodeError as exc:  # a sequence that does not decode, as for a single item
        return "invalid", str(exc)
    except Exception as exc:  # a crash is a finding, not a verdict
        return f"crash:{type(exc).__name__}", str(exc)
    if result.valid:
        return "valid", ""
    return "invalid", str(result.errors[0]) if result.errors else ""


def status_of(entry: dict) -> str:
    return entry.get("status", "pass")


def agrees(entry: dict, verdict: str) -> bool:
    return verdict == entry["expect"]


def matrix_rows() -> Dict[str, dict]:
    matrix = json.loads((ROOT / "docs" / "conformance.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in matrix["rows"]}
