#!/usr/bin/env python3
"""Differential testing against an independent CDDL implementation (#81).

The conformance corpus (the probes in ``docs/conformance.json``) and a set of
mutants derived from it are validated by this library and by ``pycddl`` (the
Rust ``cddl`` crate). Every case where the two disagree is a *divergence*, and
every divergence must be explained in ``docs/divergences.json``. The test
(``tests/test_differential.py``) fails on a divergence nobody has explained and
on an explanation that no longer matches anything.

Divergences are classified by ``kind``:

``parser``      One side rejects a schema the other accepts.
``validation``  Both accept the schema and give different verdicts for the data.
``crash``       The other implementation crashed or timed out on the schema.

and explained by ``cause``:

``their-unsupported``  The other implementation does not support the feature
                       (it rejects the schema, or ignores the construct).
``their-bug``          The other implementation is wrong according to the RFC.
``our-bug``            This library is wrong (needs an ``issue``).
``spec-ambiguous``     The RFC leaves it open; both behaviours are defensible.

Usage::

    python scripts/differential.py report              # all divergences, grouped
    python scripts/differential.py report --untriaged  # only unexplained ones
    python scripts/differential.py minimize            # shrink each divergence's data
    python scripts/differential.py check               # exit 1 on a failing policy

``pycddl`` is optional (``pip install pycddl``); without it the tools exit
with a clear message and the test is skipped.
"""

import argparse
import json
import logging
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import SchemaError, Validator  # noqa: E402
from cddl_verifier import cbor  # noqa: E402
from cddl_verifier._cbor import map_key  # noqa: E402
from cddl_verifier._cbor_values import UNDEFINED, CBORSimple, CBORTag  # noqa: E402

MATRIX = ROOT / "docs" / "conformance.json"
RECORD = ROOT / "docs" / "divergences.json"

KINDS = {"parser", "validation", "crash"}
CAUSES = {"their-unsupported", "their-bug", "our-bug", "spec-ambiguous"}
VERDICTS = {"valid", "invalid"}
TIMEOUT = 60

# Runs in a subprocess: pycddl can segfault on recursive schemas.
WORKER = r"""
import sys, json
import pycddl
req = json.load(sys.stdin)
try:
    s = pycddl.Schema(req["schema"])
except BaseException as e:
    print(json.dumps({"parse_error": str(e)[:300]})); sys.exit(0)
out = []
for h in req["data"]:
    try:
        s.validate_cbor(bytes.fromhex(h)); out.append({"valid": True})
    except BaseException as e:
        out.append({"valid": False, "error": str(e)[:200]})
print(json.dumps({"results": out}))
"""


def available():
    try:
        import pycddl  # noqa: F401
    except ImportError:
        return False
    return True


# -- corpus ------------------------------------------------------------------

def probe_value(node):
    """A probe value from its JSON form (docs/CONFORMANCE.md, "Updating")."""
    if isinstance(node, list):
        return [probe_value(item) for item in node]
    if isinstance(node, dict):
        if "$bytes" in node:
            return bytes.fromhex(node["$bytes"])
        if "$undefined" in node:
            return UNDEFINED
        if "$simple" in node:
            return CBORSimple(node["$simple"])
        if "$float" in node:
            return float(node["$float"])
        if "$tag" in node:
            return CBORTag(node["$tag"], probe_value(node["$value"]))
        if "$map" in node:
            return {map_key(probe_value(k)): probe_value(v) for k, v in node["$map"]}
        raise ValueError(f"unknown probe value {node!r}")
    return node


_REPLACEMENTS = (0, "a", b"\x00", None, True, 1.5, [], {})


def _kind_of(value):
    return type(value).__name__


def mutants(value):
    """Variants of ``value`` that probe the edges around it.

    Each variant changes one thing: drops, adds or duplicates an array item or
    map entry, or replaces a value (at the top level or one level down) with a
    value of another type.
    """
    seen = []

    def add(candidate):
        if candidate != value and candidate not in seen:
            seen.append(candidate)

    for other in _REPLACEMENTS:
        if _kind_of(other) != _kind_of(value):
            add(other)
    if isinstance(value, list):
        if value:
            add(value[:-1])
            add(value[1:])
            add(value + [value[-1]])
        else:
            add([0])
        for index, item in enumerate(value[:4]):
            for other in _REPLACEMENTS:
                if _kind_of(other) != _kind_of(item):
                    add(value[:index] + [other] + value[index + 1:])
    elif isinstance(value, dict):
        keys = list(value)
        for key in keys[:6]:
            add({k: v for k, v in value.items() if k != key})
            for other in _REPLACEMENTS:
                if _kind_of(other) != _kind_of(value[key]):
                    add({**value, key: other})
        add({**value, 99999: 0})
    elif isinstance(value, CBORTag):
        add(value.value)
        add(CBORTag(value.tag + 1, value.value))
        for inner in mutants(value.value)[:4]:
            add(CBORTag(value.tag, inner))
    return seen


def corpus(with_mutants=True):
    """Cases ``(row, schema, data_hex)`` from the single-module conformance probes."""
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))["rows"]
    cases, seen = [], set()

    def add(row, schema, value):
        try:
            raw = cbor.encode(value)
        except Exception:  # a value this library cannot write; not a case
            return
        key = (schema, raw.hex())
        if key not in seen:
            seen.add(key)
            cases.append({"row": row, "schema": schema, "data": raw.hex()})

    for row in rows:
        for group in row.get("probes", ()):
            if "modules" in group or "root" in group:
                continue  # the other implementation has no module or root selection
            for kind in ("valid", "invalid", "accepted_but_invalid", "rejected_but_valid"):
                for node in group.get(kind, ()):
                    add(row["id"], group["schema"], probe_value(node))
            if with_mutants:
                for node in group.get("valid", ()):
                    for variant in mutants(cbor.decode(cbor.encode(probe_value(node)))):
                        add(row["id"], group["schema"], variant)
    return cases


# -- the two implementations -------------------------------------------------

def run_ours(cases):
    """Per case: ``"valid"``, ``"invalid"`` or ``"parser"`` (schema rejected)."""
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    validators = {}
    results = []
    for case in cases:
        schema = case["schema"]
        if schema not in validators:
            try:
                validators[schema] = Validator(schema)
            except SchemaError:
                validators[schema] = None
        validator = validators[schema]
        if validator is None:
            results.append("parser")
        else:
            ok = validator.validate(bytes.fromhex(case["data"])).valid
            results.append("valid" if ok else "invalid")
    return results


def _worker_outcome(proc, expected):
    """The worker's reply, or ``None`` when it is missing or malformed (a crash).

    The reply is the last line of stdout, so a warning printed before it does
    not hide it; a reply whose results are not a list of the expected length of
    ``{"valid": bool}`` entries is malformed.
    """
    lines = proc.stdout.strip().splitlines() if proc.returncode == 0 else []
    try:
        outcome = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        return None
    if not isinstance(outcome, dict):
        return None
    if "parse_error" in outcome:
        return outcome
    results = outcome.get("results")
    if not isinstance(results, list) or len(results) != expected:
        return None
    if not all(isinstance(r, dict) and isinstance(r.get("valid"), bool) for r in results):
        return None
    return outcome


def run_theirs(cases):
    """Per case: ``"valid"``, ``"invalid"``, ``"parser"`` or ``"crash"``."""
    by_schema = {}
    for index, case in enumerate(cases):
        by_schema.setdefault(case["schema"], []).append(index)
    results = [None] * len(cases)
    for schema, indexes in by_schema.items():
        request = json.dumps({"schema": schema, "data": [cases[i]["data"] for i in indexes]})
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", WORKER], input=request,
                                  capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            outcome = None
        else:
            outcome = _worker_outcome(proc, len(indexes))
        for position, index in enumerate(indexes):
            if outcome is None:
                results[index] = "crash"
            elif "parse_error" in outcome:
                results[index] = "parser"
            else:
                results[index] = "valid" if outcome["results"][position]["valid"] else "invalid"
    return results


def divergences(cases):
    """The cases on which the implementations disagree, with both outcomes.

    A schema one side cannot parse (or the other cannot survive) is reported
    once, without data; the verdicts only matter when both parse the schema.
    """
    found, schema_level = [], set()
    for case, ours, theirs in zip(cases, run_ours(cases), run_theirs(cases)):
        if ours == theirs:
            continue
        if "parser" in (ours, theirs) or theirs == "crash":
            kind = "crash" if theirs == "crash" else "parser"
            outcome = ("parser" if ours == "parser" else "accepts",
                       theirs if theirs in ("parser", "crash") else "accepts")
            key = (case["row"], case["schema"], kind)
            if key not in schema_level:
                schema_level.add(key)
                found.append({"row": case["row"], "schema": case["schema"], "kind": kind,
                              "ours": outcome[0], "theirs": outcome[1]})
            continue
        found.append({**case, "kind": "validation", "ours": ours, "theirs": theirs})
    return found


# -- the record --------------------------------------------------------------

def load_record():
    return json.loads(RECORD.read_text(encoding="utf-8"))


def entry_matches(entry, divergence):
    """An entry names a divergence by any of row, schema, data, kind, ours, theirs.

    A field may hold a list of alternatives.
    """
    def same(field):
        wanted = entry[field]
        wanted = wanted if isinstance(wanted, list) else [wanted]
        return divergence.get(field) in wanted
    return all(same(field) for field in ("row", "schema", "data", "kind", "ours", "theirs")
               if field in entry)


def evaluate(found, record):
    """Policy: what fails CI. Returns ``(unexplained, stale, malformed)``."""
    entries = record["entries"]
    unexplained = [d for d in found if not any(entry_matches(e, d) for e in entries)]
    stale = [e for e in entries if not any(entry_matches(e, d) for d in found)
             and not e.get("fixture")]
    malformed = []
    for entry in entries:
        if entry.get("cause") not in CAUSES:
            malformed.append((entry, f"cause must be one of {sorted(CAUSES)}"))
        if not entry.get("reason"):
            malformed.append((entry, "give a reason"))
        if entry.get("cause") == "our-bug" and not entry.get("issue"):
            malformed.append((entry, "an our-bug entry links its issue"))
        if "kind" in entry and entry["kind"] not in KINDS:
            malformed.append((entry, f"kind must be one of {sorted(KINDS)}"))
        if "expected" in entry and entry["expected"] not in VERDICTS:
            malformed.append((entry, "expected must be valid or invalid"))
        if "expected" in entry and not ("schema" in entry and "data" in entry):
            malformed.append((entry, "expected needs schema and data"))
    return unexplained, stale, malformed


# -- minimizing --------------------------------------------------------------

def shrinks(value):
    """Smaller candidates for ``value``, smallest first."""
    out = []
    if isinstance(value, list):
        out.append([])
        out += [value[:i] + value[i + 1:] for i in range(len(value))]
        for i, item in enumerate(value):
            out += [value[:i] + [s] + value[i + 1:] for s in shrinks(item)]
    elif isinstance(value, dict):
        out.append({})
        out += [{k: v for k, v in value.items() if k != key} for key in value]
        for key, item in value.items():
            out += [{**value, key: s} for s in shrinks(item)]
            if isinstance(key, (int, str, bytes)) and not isinstance(key, bool):
                for smaller in shrinks(key):
                    if isinstance(smaller, (int, str, bytes)) and smaller not in value:
                        out.append({(smaller if k == key else k): v for k, v in value.items()})
    elif isinstance(value, CBORTag):
        out.append(value.value)
        out += [CBORTag(value.tag, s) for s in shrinks(value.value)]
    elif isinstance(value, str) and value:
        out.append("")
    elif isinstance(value, bytes) and value:
        out.append(b"")
    elif isinstance(value, int) and not isinstance(value, bool) and value not in (0, 1):
        out += [0, 1]
    return out


def minimize(divergence):
    """Shrink the data of ``divergence`` while the same disagreement remains."""
    current = cbor.decode(bytes.fromhex(divergence["data"]))
    wanted = (divergence["ours"], divergence["theirs"])
    progress = True
    while progress:
        progress = False
        candidates = []
        for smaller in shrinks(current):
            if smaller == current:
                continue
            try:
                candidates.append((smaller, cbor.encode(smaller).hex()))
            except Exception:
                continue
        if not candidates:
            break
        probes = [{"schema": divergence["schema"], "data": hex_} for _, hex_ in candidates]
        for (smaller, _), ours, theirs in zip(candidates, run_ours(probes), run_theirs(probes)):
            if (ours, theirs) == wanted:
                current, progress = smaller, True
                break
    return {**divergence, "data": cbor.encode(current).hex()}


# -- command line ------------------------------------------------------------

def _describe(d):
    data = f" data={d['data']}" if "data" in d else ""
    return f"[{d['kind']}] {d['row']} ours={d['ours']} theirs={d['theirs']}{data}\n    {d['schema']!r}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("report", "minimize", "check"))
    parser.add_argument("--untriaged", action="store_true", help="report: only unexplained divergences")
    parser.add_argument("--no-mutants", action="store_true", help="use the probes only")
    args = parser.parse_args(argv)
    if not available():
        print("pycddl is not installed (pip install pycddl)", file=sys.stderr)
        return 2
    found = divergences(corpus(with_mutants=not args.no_mutants))
    record = load_record()
    unexplained, stale, malformed = evaluate(found, record)
    if args.command == "report":
        shown = unexplained if args.untriaged else found
        for d in shown:
            print(_describe(d))
        print(f"{len(found)} divergences, {len(unexplained)} unexplained")
        return 0
    if args.command == "minimize":
        done = set()
        for d in unexplained:
            small = minimize(d) if d["kind"] == "validation" else d
            key = (small["schema"], small.get("data"), small["kind"], small["ours"], small["theirs"])
            if key not in done:
                done.add(key)
                print(_describe(small))
        return 0
    for d in unexplained:
        print("unexplained:", _describe(d))
    for e in stale:
        print("stale entry:", json.dumps(e)[:200])
    for e, why in malformed:
        print("malformed entry:", why, json.dumps(e)[:200])
    return 1 if unexplained or stale or malformed else 0


if __name__ == "__main__":
    sys.exit(main())
