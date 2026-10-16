#!/usr/bin/env python3
"""Benchmarks and profiling for schema parsing, validation, CBOR and EDN (#85).

Standard library only. Every case builds its schema and data for a size ``n``,
checks once that the call gives the expected result (a benchmark of a call that
fails measures the wrong thing), then times it. Cases that scale with ``n`` run
at two sizes and report the growth exponent between them: about 1 is linear,
2 is quadratic, and anything above 1.5 is worth a look.

Usage::

    python scripts/benchmark.py                       # every case
    python scripts/benchmark.py --list                # the cases and their sizes
    python scripts/benchmark.py regexp cbor.encode    # cases whose name contains a word
    python scripts/benchmark.py --quick               # smallest size, one run (a smoke test)
    python scripts/benchmark.py --json base.json      # write the results
    python scripts/benchmark.py --compare base.json   # ratio against an earlier run
    python scripts/benchmark.py --profile validate.array-of-maps   # cProfile, top calls

Timings depend on the machine and its load. Compare runs made on the same
machine, one after the other. See ``docs/BENCHMARKS.md``.
"""

import argparse
import cProfile
import io
import json
import logging
import math
import platform
import pstats
import random
import statistics
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator  # noqa: E402
from cddl_verifier import _version  # noqa: E402
from cddl_verifier.cbor import decode, encode  # noqa: E402

logging.getLogger("cddl_verifier").setLevel(logging.CRITICAL)
warnings.simplefilter("ignore")

#: How long a case is timed for, and the fewest and most calls it gets
MIN_SECONDS = 0.3
MIN_RUNS = 3
MAX_RUNS = 200


@dataclass
class Case:
    name: str
    area: str
    what: str
    #: ``make(n)`` returns the callable to time; it checks the result once itself
    make: Callable[[int], Callable[[], object]]
    sizes: Tuple[int, ...]


CASES: List[Case] = []


def case(name: str, area: str, what: str, sizes: Sequence[int] = (1,)):
    def register(make):
        CASES.append(Case(name, area, what, make, tuple(sizes)))
        return make
    return register


def _ok(result, message: str) -> None:
    if not result:
        raise AssertionError(message)


# ---------------------------------------------------------------- data

def _records(n: int) -> List[dict]:
    rng = random.Random(85)
    return [{"id": i, "name": f"item-{i:05d}", "tags": ["alpha", "beta", "gamma"],
             "score": round(rng.random() * 100, 3)} for i in range(n)]


_RECORDS_SCHEMA = ("r = [* item]\n"
                   "item = { id: uint, name: tstr .size (1..20), tags: [* tstr], score: float }\n")


# ---------------------------------------------------------------- parse and resolve

@case("parse.rules", "parse", "load a schema of n small rules that refer to one another", (300, 1200))
def _parse_rules(n):
    text = "\n".join(f"r{i} = {{ id: uint, name: tstr, ? next: r{i + 1} }}" for i in range(n - 1))
    text += f"\nr{n - 1} = {{ id: uint, name: tstr }}\n"
    _ok(Validator(text).default_root_type == "r0", "root")
    return lambda: Validator(text)


@case("parse.corim-schema", "parse", "load cddl-schemas/unified.cddl (CoRIM, 900 lines)")
def _parse_corim(n):
    path = ROOT / "cddl-schemas" / "unified.cddl"
    _ok(Validator(path).default_root_type == "corim", "root")
    return lambda: Validator(path)


def _aliases(n: int) -> str:
    return "a0 = uint\n" + "\n".join(f"a{i} = a{i - 1}" for i in range(1, n)) + "\n"


@case("resolve.alias-chain", "resolve", "load a schema with a chain of n aliases and validate once", (100, 400))
def _alias_chain(n):
    text = f"r = a{n - 1}\n" + _aliases(n)
    run = lambda: Validator(text).validate(7)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("resolve.validate-through-aliases", "resolve", "validate n values, each through a chain of 50 aliases",
      (200, 800))
def _validate_alias_chain(n):
    validator = Validator("r = [* a49]\n" + _aliases(50))
    data = [7] * n
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


# ---------------------------------------------------------------- validate

@case("validate.array-of-maps", "validate", "n maps of four fields, as Python values", (500, 2000))
def _array_of_maps(n):
    validator, data = Validator(_RECORDS_SCHEMA), _records(n)
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("validate.array-of-maps-bytes", "validate",
      "the same n maps from CBOR bytes (decode, offsets and validation)", (500, 2000))
def _array_of_maps_bytes(n):
    validator, raw = Validator(_RECORDS_SCHEMA), encode(_records(n))
    run = lambda: validator.validate(raw)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("validate.invalid-array", "validate", "n maps, every one with a wrong field (error reporting)", (300, 1200))
def _invalid_array(n):
    validator = Validator(_RECORDS_SCHEMA)
    data = [dict(record, id="x") for record in _records(n)]
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(not run().valid, "invalid")
    return run


@case("validate.wide-map", "validate", "one map with n fields", (100, 400))
def _wide_map(n):
    text = "r = {\n" + "\n".join(f"  f{i}: {'uint' if i % 2 else 'tstr'}," for i in range(n)).rstrip(",") + "\n}\n"
    validator = Validator(text)
    data = {f"f{i}": (i if i % 2 else "x") for i in range(n)}
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("validate.nested", "validate", "n maps nested inside one another", (20, 60))
def _nested(n):
    text = "n0 = { v: uint }\n" + "\n".join(f"n{i} = {{ next: n{i - 1} }}" for i in range(1, n + 1))
    validator = Validator(f"r = n{n}\n" + text)
    data = {"v": 1}
    for _ in range(n):
        data = {"next": data}
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("validate.choice-last", "validate", "data that matches the last of n map alternatives", (20, 80))
def _choice_last(n):
    text = "r = " + " / ".join(f"c{i}" for i in range(n)) + "\n"
    text += "\n".join(f'c{i} = {{ kind: {i}, value: tstr }}' for i in range(n)) + "\n"
    validator = Validator(text)
    data = {"kind": n - 1, "value": "x"}
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("validate.corim-document", "validate", "a small CoRIM document against unified.cddl")
def _corim_document(n):
    validator = Validator(ROOT / "cddl-schemas" / "unified.cddl")
    raw = (ROOT / "test-data" / "minimal-corim.cbor").read_bytes()
    run = lambda: validator.validate(raw)  # noqa: E731
    _ok(run().valid, "valid")
    return run


# ---------------------------------------------------------------- regular expressions

@case("regexp.match", "regexp", "n strings against a typical .regexp", (500, 2000))
def _regexp_match(n):
    validator = Validator('r = [* tstr .regexp "[a-z]+[0-9]*@[a-z]+\\\\.(com|org|net)"]\n')
    data = [f"user{i}@example.org" for i in range(n)]
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("regexp.long-string", "regexp", "one string of n characters against ([a-z]|[0-9])*", (2000, 8000))
def _regexp_long(n):
    validator = Validator('r = tstr .regexp "([a-z]|[0-9])*"\n')
    data = ("ab12" * n)[:n]
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(run().valid, "valid")
    return run


@case("regexp.nested-star", "regexp", "(a*)*b against n letters a (exponential in a backtracking engine)",
      (500, 2000))
def _regexp_nested_star(n):
    validator = Validator('r = tstr .regexp "(a*)*b"\n')
    data = "a" * n
    run = lambda: validator.validate(data)  # noqa: E731
    _ok(not run().valid, "invalid")
    return run


# ---------------------------------------------------------------- CBOR

@case("cbor.encode", "cbor", "encode n maps", (500, 2000))
def _cbor_encode(n):
    data = _records(n)
    _ok(decode(encode(data)) == data, "round trip")
    return lambda: encode(data)


@case("cbor.encode-canonical", "cbor", "encode a map of n entries deterministically (key sorting)", (500, 2000))
def _cbor_encode_canonical(n):
    rng = random.Random(86)
    keys = list(range(n)); rng.shuffle(keys)
    data = {k: {"v": k, "s": str(k)} for k in keys}
    _ok(decode(encode(data, canonical=True)) == data, "round trip")
    return lambda: encode(data, canonical=True)


@case("cbor.encode-canonical-records", "cbor", "encode n maps deterministically", (500, 2000))
def _cbor_encode_canonical_records(n):
    data = _records(n)
    _ok(decode(encode(data, canonical=True)) == data, "round trip")
    return lambda: encode(data, canonical=True)


@case("cbor.decode", "cbor", "decode n maps", (500, 2000))
def _cbor_decode(n):
    raw = encode(_records(n))
    _ok(len(decode(raw)) == n, "length")
    return lambda: decode(raw)


@case("cbor.decode-keep-encoding", "cbor", "decode n maps and keep how each item was written", (500, 2000))
def _cbor_decode_keep(n):
    raw = encode(_records(n))
    _ok(len(decode(raw, keep_encoding=True)) == n, "length")
    return lambda: decode(raw, keep_encoding=True)


@case("cbor.decode-deterministic", "cbor", "decode n maps and require deterministic encoding", (500, 2000))
def _cbor_decode_det(n):
    raw = encode(_records(n), canonical=True)
    _ok(len(decode(raw, require_deterministic=True)) == n, "length")
    return lambda: decode(raw, require_deterministic=True)


# ---------------------------------------------------------------- EDN

@case("edn.array-of-maps", "edn", "annotated EDN of n maps", (200, 800))
def _edn(n):
    validator, raw = Validator(_RECORDS_SCHEMA), encode(_records(n))
    run = lambda: validator.to_edn(raw)  # noqa: E731
    _ok("item-00000" in run(), "text")
    return run


@case("edn.corim-document", "edn", "annotated EDN of a small CoRIM document")
def _edn_corim(n):
    validator = Validator(ROOT / "cddl-schemas" / "unified.cddl")
    raw = (ROOT / "test-data" / "minimal-corim.cbor").read_bytes()
    run = lambda: validator.to_edn(raw)  # noqa: E731
    _ok(run(), "text")
    return run


# ---------------------------------------------------------------- running

def time_call(fn: Callable[[], object], *, quick: bool = False) -> Dict[str, float]:
    """Time *fn*: its fastest and median call in milliseconds, and how many calls were made."""
    fn()  # warm the caches
    samples: List[float] = []
    started = time.perf_counter()
    while len(samples) < (1 if quick else MIN_RUNS) or (
            not quick and len(samples) < MAX_RUNS and time.perf_counter() - started < MIN_SECONDS):
        t = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t)
    return {"min_ms": min(samples) * 1000, "median_ms": statistics.median(samples) * 1000,
            "runs": len(samples)}


def selected(words: Sequence[str]) -> List[Case]:
    return [c for c in CASES if not words or any(w in c.name for w in words)]


def run(words: Sequence[str] = (), *, quick: bool = False) -> List[Dict]:
    results = []
    for c in selected(words):
        sizes = c.sizes[:1] if quick else c.sizes
        previous = None
        for n in sizes:
            fn = c.make(n)
            timing = time_call(fn, quick=quick)
            row = {"name": c.name, "n": n if len(c.sizes) > 1 else None, "area": c.area, **timing}
            if previous is not None and previous["min_ms"] > 0 and timing["min_ms"] > 0:
                row["growth"] = (math.log(timing["min_ms"] / previous["min_ms"])
                                 / math.log(n / previous["n"]))
            previous = {"n": n, "min_ms": timing["min_ms"]}
            results.append(row)
    return results


def _label(row: Dict) -> str:
    return row["name"] if row["n"] is None else f"{row['name']}[{row['n']}]"


def render(results: List[Dict], baseline: Optional[Dict[str, Dict]] = None) -> str:
    width = max((len(_label(r)) for r in results), default=10)
    head = f"{'case':<{width}}  {'min ms':>10}  {'median ms':>10}  {'runs':>5}  {'growth':>6}"
    if baseline is not None:
        head += f"  {'vs base':>8}"
    lines = [head, "-" * len(head)]
    for r in results:
        growth = f"{r['growth']:.2f}" if "growth" in r else ""
        flag = "  <- superlinear" if r.get("growth", 0) > 1.5 else ""
        line = (f"{_label(r):<{width}}  {r['min_ms']:>10.3f}  {r['median_ms']:>10.3f}  "
                f"{r['runs']:>5}  {growth:>6}")
        if baseline is not None:
            old = baseline.get(_label(r))
            line += f"  {r['min_ms'] / old['min_ms']:>7.2f}x" if old and old["min_ms"] else f"  {'new':>8}"
        lines.append(line + flag)
    return "\n".join(lines)


def profile(name: str, top: int) -> str:
    matches = [c for c in CASES if c.name == name]
    if not matches:
        raise SystemExit(f"no case named {name!r}; --list shows them")
    c = matches[0]
    fn = c.make(c.sizes[-1])
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(3):
        fn()
    profiler.disable()
    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out).strip_dirs()
    out.write(f"{c.name} at n={c.sizes[-1]}, 3 calls: {c.what}\n\n-- by cumulative time\n")
    stats.sort_stats("cumulative").print_stats(top)
    out.write("-- by time inside the function itself\n")
    stats.sort_stats("tottime").print_stats(top)
    return out.getvalue()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("words", nargs="*", help="run the cases whose name contains one of these")
    ap.add_argument("--list", action="store_true", help="list the cases and exit")
    ap.add_argument("--quick", action="store_true", help="smallest size, one call each")
    ap.add_argument("--json", metavar="FILE", help="write the results to FILE")
    ap.add_argument("--compare", metavar="FILE", help="show the ratio against results written with --json")
    ap.add_argument("--fail-above", type=float, metavar="RATIO",
                    help="with --compare, exit 1 if any case is slower than RATIO times the baseline")
    ap.add_argument("--profile", metavar="CASE", help="profile one case with cProfile and exit")
    ap.add_argument("--top", type=int, default=15, help="rows per profile table (default 15)")
    args = ap.parse_args(argv)

    if args.list:
        for c in CASES:
            print(f"{c.name:<36} {c.area:<8} n={','.join(map(str, c.sizes)):<10} {c.what}")
        return 0
    if args.profile:
        print(profile(args.profile, args.top))
        return 0
    baseline = None
    if args.compare:
        data = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        baseline = {_label(r): r for r in data["results"]}
    results = run(args.words, quick=args.quick)
    if not results:
        print("no case matches", file=sys.stderr)
        return 2
    print(f"cddl_verifier {_version.__version__}, Python {platform.python_version()}, "
          f"{platform.system()} {platform.machine()}\n")
    print(render(results, baseline))
    if args.json:
        Path(args.json).write_text(json.dumps({
            "version": _version.__version__, "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}", "results": results,
        }, indent=1) + "\n", encoding="utf-8")
    if args.fail_above and baseline:
        slow = [r for r in results if _label(r) in baseline and baseline[_label(r)]["min_ms"]
                and r["min_ms"] > args.fail_above * baseline[_label(r)]["min_ms"]]
        if slow:
            print("\nslower than %.2fx the baseline: %s" % (args.fail_above, ", ".join(map(_label, slow))),
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
