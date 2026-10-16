# Benchmarks and profiling

`scripts/benchmark.py` times schema parsing and resolution, validation, regular
expression controls, CBOR encoding and decoding, and EDN generation (#85). It uses
only the standard library.

```
python scripts/benchmark.py                       # every case
python scripts/benchmark.py --list                # the cases, their sizes, what each measures
python scripts/benchmark.py regexp cbor.encode    # cases whose name contains a word
python scripts/benchmark.py --quick               # smallest size, one call: a smoke test
python scripts/benchmark.py --json base.json      # save the results
python scripts/benchmark.py --compare base.json   # ratio against a saved run
python scripts/benchmark.py --compare base.json --fail-above 1.5   # exit 1 if a case is 1.5x slower
python scripts/benchmark.py --profile validate.array-of-maps       # cProfile: top calls
```

Timings depend on the machine and its load, so compare runs made on the same machine,
one after the other. `--fail-above` is for a person or a job that owns a quiet machine;
the unit tests only check that the script runs (`tests/test_benchmark.py`).

## What it measures

Each case builds its schema and data for a size `n`, calls the operation once and
checks the result (a benchmark of a call that fails measures the wrong thing), then
times it for at least 0.3 s. A case that scales with `n` runs at two sizes and reports
the growth exponent between them: about 1 is linear, 2 is quadratic. Anything above 1.5
is flagged `superlinear`.

| Area | Cases |
|---|---|
| `parse` | `parse.rules` (n small rules that refer to one another), `parse.corim-schema` (`cddl-schemas/unified.cddl`, 900 lines) |
| `resolve` | `resolve.alias-chain` (load and validate through n aliases), `resolve.validate-through-aliases` (n values, each through 50 aliases) |
| `validate` | `array-of-maps` (Python values and CBOR bytes), `invalid-array` (error reporting), `wide-map`, `nested`, `choice-last` (the last of n alternatives matches), `corim-document` |
| `regexp` | `match` (n strings), `long-string` (one long string), `nested-star` (`(a*)*b`, exponential in a backtracking engine) |
| `cbor` | `encode`, `encode-canonical` (key sorting), `encode-canonical-records`, `decode`, `decode-keep-encoding`, `decode-deterministic` |
| `edn` | `array-of-maps`, `corim-document` |

## What profiling found (0.34.1)

Every case scales linearly (growth between 0.9 and 1.15), so nothing is quadratic or
worse, and the pattern `(a*)*b` that blows up a backtracking matcher takes under a
millisecond. The absolute cost was in validation: about 300 µs for a map of four
fields, against 14 µs to decode it. The profile showed the cost was not in checking
the data but in working out, for every field of every item, facts about the schema
node that never change: how it is checked, its text, its type definition. The changes:

| Change | Where | Effect |
|---|---|---|
| The text of a node is written once and kept on the node | `_cddl/printer.py` | `format_node` was 15% of validation and 37% of EDN generation |
| How a node is checked (`_checked_as_whole`) is remembered per analyzer, by node identity, in a bounded table | `_validator/primitives.py` | 41% of validation |
| `get_type` remembers its answer for a node once the schema is complete (only when no data chooses the type) | `_schema.py` | 33% of what was left |
| The steps of a regular-expression match are remembered per pattern; the cost is stored with the step, so the budget counts as before and a limit still fails closed | `_cddl/xsd_regex.py` | matching 2.5x to 6x faster |
| `ast.walk` runs on a stack and reads the field names of a node class once | `_cddl/ast.py` | schema loading about 10% faster |

Measured on one machine, minimum of the runs (`--compare` against the 0.34.0 baseline):

| Case | Before | After |
|---|---:|---:|
| `validate.array-of-maps[2000]` | 615 ms | 272 ms |
| `validate.invalid-array[1200]` | 400 ms | 190 ms |
| `validate.corim-document` | 1.62 ms | 1.45 ms |
| `regexp.match[2000]` | 190 ms | 75 ms |
| `regexp.long-string[8000]` | 12.1 ms | 1.8 ms |
| `edn.array-of-maps[800]` | 109 ms | 85 ms |
| `parse.rules[1200]` | 220 ms | 197 ms |
| `parse.corim-schema` | 29 ms | 26 ms |

Considered and left alone, because the profile did not support them:

- **Avoiding repeated CBOR encoding while sorting.** The canonical encoder encodes
  each map key once and sorts the encoded bytes; `encode-canonical-records` costs the
  same as `encode`. Nothing is encoded twice.
- **Caching `resolve_alias`.** After the changes above it is under 10% of validation,
  and the cases that depend on alias chains (`resolve.*`) are linear.
- **Compiled-pattern reuse.** `compile_pattern` was already cached (256 patterns).

What remains in validation is a flat profile: many small calls (`isinstance`, node
queries) at about 35 µs per field. Making it faster again means changing how the
checks are structured, for instance compiling each rule into a checker once, which is
a larger change than #85 asks for and has no case behind it yet.

## Adding a case

Write a function that builds the schema and data for `n`, checks the result once with
`_ok`, and returns the callable to time; register it with `@case(name, area, what, sizes)`.
Use two sizes (a factor of four apart) when the cost depends on `n`, so the growth shows.
