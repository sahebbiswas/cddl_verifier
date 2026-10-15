"""Property-based tests (#82): arbitrary input fails in a controlled way, supported
values round-trip, and validation agrees with a small reference implementation.

The decoder is given arbitrary and damaged bytes, the schema parser arbitrary
text, and the validator schemas and data from a restricted grammar for which
whether the data matches can be said by a few lines of Python (``accepts``).
Every property also runs under a time budget, so a hang is a failure.

By default the examples are the same on every run (``derandomize``), so a
failure is never a flake. To search further::

    CDDL_VERIFIER_FUZZ_EXAMPLES=5000 CDDL_VERIFIER_FUZZ_RANDOM=1 pytest tests/test_properties.py

A failing example found that way is minimized by Hypothesis; keep it as an
``@example`` on the property or as a case in ``TestRegressions``.
"""

import json
import os
import unittest
from datetime import timedelta

try:
    import hypothesis  # noqa: F401
except ImportError:
    raise unittest.SkipTest("hypothesis is not installed")

from hypothesis import HealthCheck, Phase, example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from cddl_verifier import Validator, schema_diagnostics, validate  # noqa: E402
from cddl_verifier._analyzer import CDDLParser  # noqa: E402
from cddl_verifier._cbor import (CBOR, CBORDecodeError, cbor_decode, cbor_decode_sequence,  # noqa: E402
                                 cbor_diag_dump, cbor_encode)
from cddl_verifier._cbor_values import CBORTag  # noqa: E402
from cddl_verifier._errors import SchemaError  # noqa: E402
from cddl_verifier._json_codec import cbor_to_json, json_to_cbor  # noqa: E402

EXAMPLES = int(os.environ.get("CDDL_VERIFIER_FUZZ_EXAMPLES", "100"))
RANDOM = bool(os.environ.get("CDDL_VERIFIER_FUZZ_RANDOM"))

settings.register_profile(
    "cddl-verifier", max_examples=EXAMPLES, derandomize=not RANDOM, database=None,
    deadline=timedelta(seconds=5), print_blob=True,
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
settings.load_profile("cddl-verifier")


# --------------------------------------------------------------- the decoder

# What the decoder may raise for bytes that are not one CBOR item.
CONTROLLED = (CBORDecodeError,)

cbor_scalars = st.one_of(
    st.integers(-2 ** 64, 2 ** 64 - 1),
    st.integers(-2 ** 100, 2 ** 100),  # past 64 bits: bignums
    st.binary(max_size=8),
    st.text(max_size=8),
    st.booleans(),
    st.none(),
    st.floats(allow_nan=False, allow_infinity=True))

map_keys = st.one_of(st.integers(-2 ** 32, 2 ** 32), st.text(max_size=6), st.binary(max_size=4))

cbor_values = st.recursive(
    cbor_scalars,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(map_keys, inner, max_size=4),
        # tag numbers without a meaning of their own for the decoder
        st.builds(CBORTag, st.integers(1000, 2 ** 32), inner)),
    max_leaves=12)


def damaged(data: bytes):
    """*data* with a byte changed, cut out, inserted, or the end cut off."""
    return st.one_of(
        st.builds(lambda i, b: data[:i] + bytes([b]) + data[i + 1:],
                  st.integers(0, max(len(data) - 1, 0)), st.integers(0, 255)) if data else st.just(data),
        st.builds(lambda i: data[:i] + data[i + 1:], st.integers(0, max(len(data) - 1, 0))),
        st.builds(lambda i, b: data[:i] + bytes([b]) + data[i:],
                  st.integers(0, len(data)), st.integers(0, 255)),
        st.builds(lambda n: data[:n], st.integers(0, len(data))))


def decode_controlled(function, data):
    """Run *function* on *data*: a result, or None for a controlled refusal."""
    try:
        return function(data)
    except CONTROLLED:
        return None


class TestDecoderRobustness(unittest.TestCase):
    @given(st.binary(max_size=96))
    def test_arbitrary_bytes(self, data):
        for function in (CBOR.loads, CBOR.loads_with_locations, cbor_decode_sequence,
                         CBOR.loads_sequence_with_locations, cbor_diag_dump,
                         lambda d: CBOR.loads(d, keep_encoding=True)):
            decode_controlled(function, data)

    @given(cbor_values.flatmap(lambda v: damaged(cbor_encode(v))))
    def test_damaged_encodings(self, data):
        decode_controlled(CBOR.loads, data)
        decode_controlled(CBOR.loads_with_locations, data)
        decode_controlled(cbor_diag_dump, data)

    def test_deep_nesting_is_refused_or_decoded(self):
        # a hundred thousand levels must not exhaust the stack
        for opener in (b"\x81", b"\xa1\x00", b"\xc1", b"\x9f", b"\xbf\x00", b"\x5f", b"\x7f"):
            with self.subTest(opener=opener):
                data = opener * 100_000 + b"\x00"
                decode_controlled(CBOR.loads, data)
                decode_controlled(CBOR.loads_with_locations, data)
                decode_controlled(cbor_diag_dump, data)

    def test_huge_claimed_lengths(self):
        # a length far beyond the input is a truncated item, not an allocation
        for header in ("5b" + "ff" * 8, "7b" + "ff" * 8, "9b" + "ff" * 8, "bb" + "ff" * 8,
                       "5affffffff", "9affffffff", "baffffffff"):
            with self.subTest(header=header):
                decode_controlled(CBOR.loads, bytes.fromhex(header))

    @given(st.binary(max_size=64))
    def test_a_decoded_value_encodes_again(self, data):
        value = decode_controlled(CBOR.loads, data)
        if value is not None or data[:1] == b"\xf6":
            again = cbor_encode(value)
            assert cbor_decode(again) == value or value != value  # NaN is not equal to itself


# ---------------------------------------------------------------- round trips

class TestRoundTrips(unittest.TestCase):
    @given(cbor_values)
    def test_decode_of_encode(self, value):
        assert cbor_decode(cbor_encode(value)) == value

    @given(cbor_values)
    def test_canonical_encoding_is_stable(self, value):
        canonical = cbor_encode(value, canonical=True)
        again = cbor_encode(cbor_decode(canonical), canonical=True)
        assert again == canonical

    @given(cbor_values)
    def test_canonical_encoding_does_not_depend_on_key_order(self, value):
        def reversed_keys(item):
            if isinstance(item, dict):
                return {k: reversed_keys(v) for k, v in reversed(list(item.items()))}
            if isinstance(item, list):
                return [reversed_keys(v) for v in item]
            if isinstance(item, CBORTag):
                return CBORTag(item.tag, reversed_keys(item.value))
            return item
        assert cbor_encode(reversed_keys(value), canonical=True) == cbor_encode(value, canonical=True)

    @given(cbor_values)
    def test_locations_cover_the_item(self, value):
        data = cbor_encode(value)
        decoded, locations = CBOR.loads_with_locations(data)
        assert decoded == value
        assert locations.span(()) == (0, len(data))

    @given(st.lists(cbor_values, max_size=4))
    def test_sequence_round_trip(self, values):
        assert cbor_decode_sequence(b"".join(cbor_encode(v) for v in values)) == values


class TestJsonCodec(unittest.TestCase):
    @given(cbor_values)
    def test_typed_json_round_trips(self, value):
        data = cbor_encode(value, canonical=True)
        back = json_to_cbor(cbor_to_json(data, typed=True))
        # compared as bytes: -0.0 and 0.0 are equal as values, but not the same item
        assert cbor_encode(cbor_decode(back), canonical=True) == data

    @given(st.binary(max_size=48), st.booleans())
    def test_any_cbor_gives_json_or_a_decode_error(self, data, typed):
        try:
            text = cbor_to_json(data, typed=typed)
        except CONTROLLED:
            return
        json.loads(text)

    @given(st.text(max_size=40))
    def test_any_text_gives_cbor_or_a_value_error(self, text):
        try:
            json_to_cbor(text)
        except ValueError:
            pass


# ----------------------------------------------------------- the schema parser

TOKENS = ["a", "b", "foo", "uint", "tstr", "bstr", "int", "bool", "nil", "any", "=", "/=", "//=", "/", "//",
          ":", "=>", ",", "{", "}", "[", "]", "(", ")", "<", ">", "?", "*", "+", "^", "~", "&", "#", "#6.1",
          "#7.25", ".size", ".regexp", ".cbor", ".cborseq", ".and", ".within", ".ge", "..", "...", "1", "0",
          "-1", '"x"', "h'ab'", "'q'", "$", "$$", "@", ";", "\n", ";# import m as p\n", "0.5", "1e3", "%",
          "\\", '"']
token_soup = st.lists(st.sampled_from(TOKENS), max_size=40).map(" ".join)


class TestSchemaParserRobustness(unittest.TestCase):
    @given(st.one_of(st.text(max_size=80), token_soup))
    def test_schema_diagnostics_never_raises(self, text):
        found = schema_diagnostics(text)
        assert all(d.code == "schema" for d in found)

    @given(st.one_of(st.text(max_size=80), token_soup))
    def test_the_parser_raises_only_schema_errors(self, text):
        try:
            CDDLParser(text)
        except SchemaError:
            pass

    @given(token_soup, st.binary(max_size=24))
    def test_validating_with_any_schema_is_controlled(self, text, data):
        try:
            validate(text, data, None)  # a result, whatever the data
        except SchemaError:
            pass  # a schema that cannot be used, or one without rules

    @given(st.integers(0, 40_000), st.sampled_from(["[", "(", "{a:", "[* ", "uint / "]))
    def test_deep_nesting_is_refused_or_parsed(self, depth, opener):
        close = {"[": "]", "(": ")", "{a:": "}", "[* ": "]", "uint / ": ""}[opener]
        schema_diagnostics("r = " + opener * depth + "uint" + close * depth)

    def test_long_schemas(self):
        rules = "\n".join(f"r{i} = [* r{i + 1}]" for i in range(3000)) + "\nr3000 = uint\n"
        assert schema_diagnostics(rules) == ()


# ------------------------------------------------ validation against a reference
#
# A model is a tuple, and each can be written as CDDL (``render``) and judged by
# ``accepts``, a few lines of Python that do not share code with the validator:
#
#   ("uint",) ("int",) ("tstr",) ("bstr",) ("bool",) ("nil",)
#   ("seq", [(model, occurrence)])    an array; occurrence is "", "?", "*" or "+"
#   ("map", [(key, model, optional)]) a map with fixed keys (text or integer)
#   ("mapof", key scalar, model)      { * tstr => T }
#   ("choice", [models])              T1 / T2
#   ("range", low, high)              low..high
#   ("lit", value)                    7 or "x"
#   ("size", low, high)               bstr .size (low..high)
#   ("tag", number, model)            #6.number(T)
#   ("named", model)                  the model as a rule of its own, used by name

SCALARS = [("uint",), ("int",), ("tstr",), ("bstr",), ("bool",), ("nil",)]
TEXT_KEYS = ["a", "b", "c"]
INT_KEYS = [1, 2, 3]
OCCURRENCES = ["", "?", "*", "+"]
TAG_NUMBER = 1000


class Rendering:
    """The CDDL text of a model: the first rule, ``r``, and the rules it names."""

    def __init__(self, model):
        self.rules = []
        self.main = self.type(model)

    @property
    def schema(self):
        return "".join(f"{rule}\n" for rule in [f"r = {self.main}", *self.rules])

    def type(self, model, in_array=False):
        kind = model[0]
        if kind in ("uint", "int", "tstr", "bstr", "bool", "nil"):
            return kind
        if kind == "seq":
            return "[" + ", ".join(
                (occ + " " if occ else "") + self.type(m, True) for m, occ in model[1]) + "]"
        if kind == "map":
            return "{ " + ", ".join(
                f"{'? ' if optional else ''}{key}: {self.type(m)}" for key, m, optional in model[1]) + " }"
        if kind == "mapof":
            return f"{{ * {model[1][0]} => {self.type(model[2])} }}"
        if kind == "range":
            return f"{model[1]}..{model[2]}"
        if kind == "lit":
            return json.dumps(model[1]) if isinstance(model[1], str) else str(model[1])
        if kind == "size":
            return f"bstr .size ({model[1]}..{model[2]})"
        if kind == "tag":
            return f"#6.{model[1]}({self.type(model[2])})"
        if kind == "named":
            name = f"n{len(self.rules)}"
            self.rules.append(None)
            self.rules[int(name[1:])] = f"{name} = {self.type(model[1])}"
            return name
        inner = " / ".join(self.type(m) for m in model[1])
        return f"({inner})" if in_array else inner


def render(model):
    return Rendering(model).schema


def sequence_accepts(entries, items, i=0, j=0):
    """Whether *items* from *j* are matched by the entries from *i*, in order."""
    if i == len(entries):
        return j == len(items)
    model, occ = entries[i]
    low, high = {"": (1, 1), "?": (0, 1), "*": (0, len(items)), "+": (1, len(items))}[occ]
    for count in range(low, min(high, len(items) - j) + 1):
        if all(accepts(model, item) for item in items[j:j + count]) \
                and sequence_accepts(entries, items, i + 1, j + count):
            return True
    return False


def accepts(model, value):
    kind = model[0]
    if kind == "uint":
        return type(value) is int and 0 <= value < 2 ** 64
    if kind == "int":
        return type(value) is int and -2 ** 64 <= value < 2 ** 64
    if kind == "tstr":
        return isinstance(value, str)
    if kind == "bstr":
        return isinstance(value, bytes)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "nil":
        return value is None
    if kind == "seq":
        return isinstance(value, list) and sequence_accepts(model[1], value)
    if kind == "map":
        if not isinstance(value, dict):
            return False
        keys = {key for key, _, _ in model[1]}
        if any(key not in keys for key in value):
            return False
        return all(accepts(m, value[key]) if key in value else optional
                   for key, m, optional in model[1])
    if kind == "mapof":
        return (isinstance(value, dict) and all(accepts(model[1], k) for k in value)
                and all(accepts(model[2], v) for v in value.values()))
    if kind == "range":
        return type(value) is int and model[1] <= value <= model[2]
    if kind == "lit":
        return type(value) is type(model[1]) and value == model[1]
    if kind == "size":
        return isinstance(value, bytes) and model[1] <= len(value) <= model[2]
    if kind == "tag":
        return isinstance(value, CBORTag) and value.tag == model[1] and accepts(model[2], value.value)
    if kind == "named":
        return accepts(model[1], value)
    return any(accepts(m, value) for m in model[1])


def models(depth=3):
    scalars = st.sampled_from(SCALARS)
    leaves = st.one_of(
        scalars,
        st.builds(lambda a, b: ("range", min(a, b), max(a, b)), st.integers(-3, 9), st.integers(-3, 9)),
        st.sampled_from([("lit", 7), ("lit", "x"), ("lit", 0)]),
        st.builds(lambda a, b: ("size", min(a, b), max(a, b)), st.integers(0, 3), st.integers(0, 3)))
    if depth == 0:
        return leaves
    inner = models(depth - 1)
    entries = st.lists(st.tuples(inner, st.sampled_from(OCCURRENCES)), max_size=3)
    fixed_keys = st.one_of(st.just(TEXT_KEYS), st.just(INT_KEYS), st.just(["a", 2, "c"]))
    return st.one_of(
        leaves,
        st.builds(lambda es: ("seq", es), entries),
        st.builds(lambda o, m: ("seq", [(m, o)]), st.sampled_from("*+"), inner),
        st.builds(lambda keys, ms, flags: ("map", [(k, m, f) for k, m, f in zip(keys, ms, flags)]),
                  fixed_keys, st.lists(inner, min_size=1, max_size=3),
                  st.lists(st.booleans(), min_size=3, max_size=3)),
        st.builds(lambda k, m: ("mapof", k, m), st.sampled_from([("tstr",), ("uint",)]), inner),
        st.builds(lambda ms: ("choice", ms), st.lists(leaves, min_size=2, max_size=3, unique=True)),
        st.builds(lambda a, b: ("choice", [a, b]), leaves, inner.filter(lambda m: m[0] in ("seq", "map", "mapof", "tag"))),
        st.builds(lambda m: ("tag", TAG_NUMBER, m), inner),
        st.builds(lambda m: ("named", m), inner))


LOOSE = st.recursive(
    st.one_of(st.sampled_from([0, 1, 7, -1, 2 ** 64 - 1, -2 ** 64]), st.integers(-5, 9), st.booleans(),
              st.none(), st.sampled_from(["", "x", "a"]), st.binary(max_size=4)),
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.dictionaries(st.sampled_from(TEXT_KEYS + INT_KEYS + ["z"]), inner, max_size=3),
        st.builds(CBORTag, st.sampled_from([TAG_NUMBER, TAG_NUMBER + 1]), inner)),
    max_leaves=6)


@st.composite
def pick(draw, strategies):
    """One of *strategies*, chosen by position. ``st.one_of`` skips the alternatives it finds
    empty at that moment, which for strategies built per example (and the recursive
    ``LOOSE``) is not the same on every replay, and Hypothesis then reports the
    strategy as flaky."""
    return draw(draw(st.sampled_from(list(strategies))))


def conforming(model):
    """Values that match *model*."""
    kind = model[0]
    if kind in ("uint", "int", "tstr", "bstr", "bool", "nil"):
        return {"uint": st.one_of(st.integers(0, 12), st.just(2 ** 64 - 1)),
                "int": st.one_of(st.integers(-12, 12), st.just(-2 ** 64)),
                "tstr": st.text(max_size=3), "bstr": st.binary(max_size=3),
                "bool": st.booleans(), "nil": st.none()}[kind]
    if kind == "range":
        return st.integers(model[1], model[2])
    if kind == "lit":
        return st.just(model[1])
    if kind == "size":
        return st.binary(min_size=model[1], max_size=model[2])
    if kind == "named":
        return conforming(model[1])
    if kind == "tag":
        return st.builds(CBORTag, st.just(model[1]), conforming(model[2]))
    if kind == "choice":
        return pick([conforming(m) for m in model[1]])
    if kind == "mapof":
        keys = st.text(max_size=2) if model[1][0] == "tstr" else st.integers(0, 5)
        return st.dictionaries(keys, conforming(model[2]), max_size=3)
    if kind == "map":
        members = [(key, conforming(m), optional) for key, m, optional in model[1]]
        return st.tuples(*[st.tuples(st.just(key), value, st.booleans() if optional else st.just(True))
                           for key, value, optional in members]).map(
            lambda parts: {key: value for key, value, present in parts if present})
    # an array: each entry takes a number of elements its occurrence allows
    counts = [{"": st.just(1), "?": st.integers(0, 1), "*": st.integers(0, 2),
               "+": st.integers(1, 2)}[occ] for _, occ in model[1]]
    parts = [st.integers(0, 0).flatmap(lambda _, c=c, m=m: c.flatmap(
        lambda n, m=m: st.lists(conforming(m), min_size=n, max_size=n)))
        for (m, _), c in zip(model[1], counts)]
    return st.tuples(*parts).map(lambda ps: [item for part in ps for item in part])


def near(model):
    """Values that match *model* or nearly do: something is off by one step."""
    kind = model[0]
    if kind == "named":
        return near(model[1])
    mutated = [conforming(model), LOOSE]
    if kind == "seq":
        mutated += [conforming(model).map(lambda v: v[:-1]),
                    conforming(model).map(lambda v: v + v[:1]),
                    st.lists(pick([near(m) for m, _ in model[1]] or [LOOSE]), max_size=3)]
    elif kind in ("map", "mapof"):
        mutated += [conforming(model).map(lambda v: {**v, "z": 0}),
                    conforming(model).flatmap(lambda v: st.sampled_from(sorted(v, key=repr) or [None]).map(
                        lambda k: {x: y for x, y in v.items() if x != k})),
                    conforming(model).flatmap(lambda v: LOOSE.map(
                        lambda w: {**v, **({sorted(v, key=repr)[0]: w} if v else {})}))]
    elif kind == "tag":
        mutated += [st.builds(CBORTag, st.sampled_from([TAG_NUMBER + 1, 5]), conforming(model[2])),
                    st.builds(CBORTag, st.just(model[1]), near(model[2]))]
    elif kind == "choice":
        mutated += [near(m) for m in model[1]]
    return pick(mutated)


@st.composite
def cases(draw):
    model = draw(models())
    return model, draw(near(model))


def check(model, value):
    schema = render(model)
    result = validate(schema, cbor_encode(value), "r")
    assert result.valid == accepts(model, value), (
        f"schema {schema!r} data {value!r}: validator says {result.valid}, "
        f"reference says {accepts(model, value)}; {[d.message for d in result.diagnostics]}")
    if not result.valid:
        assert result.diagnostics, "an invalid result without a diagnostic"


class TestValidationAgainstReference(unittest.TestCase):
    @given(cases())
    def test_agrees_with_the_reference(self, case):
        check(*case)

    @given(models().flatmap(lambda m: conforming(m).map(lambda v: (m, v))))
    def test_conforming_values_are_valid(self, case):
        model, value = case
        assert accepts(model, value)  # the generator and the reference agree
        check(model, value)

    @given(cases())
    def test_python_values_and_bytes_agree(self, case):
        model, value = case
        if isinstance(value, bytes):
            return  # a byte string given to validate() is CBOR to decode, not a value
        schema = render(model)
        assert validate(schema, value, "r").valid == validate(schema, cbor_encode(value), "r").valid

    @given(cases())
    def test_diagnostics_point_inside_the_data(self, case):
        model, value = case
        data = cbor_encode(value)
        result = validate(render(model), data, "r")
        for diagnostic in result.diagnostics:
            if diagnostic.offset is not None:
                assert 0 <= diagnostic.offset <= len(data)
            assert json.loads(diagnostic.to_json())["path_expr"].startswith("$")


# ----------------------------------------------------------------- regressions
# Minimized findings are kept here, with the issue that fixed them.

class TestRegressions(unittest.TestCase):
    def test_found_by_review(self):
        cases = [
            (("seq", [(("seq", [(("uint",), "*")]), "*")]), [[1, 2], [3, "x"]]),             # #196
            (("choice", [("uint",), ("seq", [(("uint",), "*")])]), [3, "x"]),                # #196
        ]
        for model, value in cases:
            with self.subTest(schema=render(model)):
                check(model, value)

    def test_computed_key_member_with_an_array_value(self):
        # found by TestValidationAgainstReference: { * tstr => [* uint] } took any value
        model = ("mapof", ("tstr",), ("seq", [(("uint",), "*")]))
        check(model, {"z": 0})
        check(model, {"z": ["x"]})
        check(model, {"z": [1, 2]})

    def test_diag_dump_of_very_deep_nesting_ends_with_an_error_line(self):
        # found by TestDecoderRobustness: the dump raised RecursionError
        dump = cbor_diag_dump(b"\x81" * 100_000 + b"\x00")
        assert dump.splitlines()[-1].endswith("# ERROR: Nesting too deep")
