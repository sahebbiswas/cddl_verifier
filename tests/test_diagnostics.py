#!/usr/bin/env python3
"""Structured validation diagnostics (#75): data path, byte offsets, expected and
found values, schema location, severity and category, and the text and JSON
renderers.

Only public names are used, except for the decoder's location table.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import (CBORDecodeError, Diagnostic, ValidationResult, Validator, cbor,
                           render_json, render_text, validate)
from cddl_verifier._cbor import CBOR, MAX_LOCATION_STEPS, CBORKey
from cddl_verifier._diagnostics import CATEGORIES, MAX_HEX_BYTES, SEVERITIES

SCHEMA = """\
measurement = {
  0: tstr,
  1: [* uint],
  ? 2: bstr .size 2,
  ? 3: uint .le 100,
}
"""


def diagnostics_of(data, schema=SCHEMA, root_type=None):
    result = validate(schema, cbor.encode(data), root_type=root_type)
    return result, result.diagnostics


class TestLocations(unittest.TestCase):
    """The decoder records where each item sits."""

    def test_spans_of_nested_items(self):
        raw = cbor.encode({0: "ab", 1: [10, 300]})
        data, locations = CBOR.loads_with_locations(raw)
        self.assertEqual(data, {0: "ab", 1: [10, 300]})
        self.assertEqual(locations.span(()), (0, len(raw)))
        self.assertEqual(raw[slice(*locations.span((0,)))], b"\x62ab")
        self.assertEqual(raw[slice(*locations.span((1,)))], bytes.fromhex("82 0a 19012c"))
        self.assertEqual(raw[slice(*locations.span((1, 0)))], b"\x0a")
        self.assertEqual(raw[slice(*locations.span((1, 1)))], bytes.fromhex("19012c"))
        self.assertEqual(raw[slice(*locations.key_span((), 1))], b"\x01")

    def test_a_tag_is_addressed_as_a_whole(self):
        raw = bytes.fromhex("d9 01f5 82 01 02")          # 501([1, 2])
        _, locations = CBOR.loads_with_locations(raw)
        self.assertEqual(locations.span(()), (0, 6))
        self.assertEqual(locations.span((0,)), (4, 5))

    def test_non_string_keys(self):
        raw = bytes.fromhex("a2 f5 01 41 aa 02")          # {true: 1, h'aa': 2}
        data, locations = CBOR.loads_with_locations(raw)
        self.assertEqual(locations.span((CBORKey(True),)), (2, 3))
        self.assertEqual(locations.span((b"\xaa",)), (5, 6))
        self.assertEqual(locations.key_span((), b"\xaa"), (3, 5))

    def test_unknown_path(self):
        _, locations = CBOR.loads_with_locations(cbor.encode([1]))
        self.assertIsNone(locations.span((5,)))
        self.assertIsNone(locations.key_span((), "x"))
        self.assertIsNone(locations.key_span((), [1]))   # unhashable

    def test_same_errors_as_loads(self):
        for raw in (b"", b"\xa1\x01", b"\x01\x02", b"\x7f"):
            with self.subTest(raw=raw):
                with self.assertRaises(CBORDecodeError) as plain:
                    CBOR.loads(raw)
                with self.assertRaises(CBORDecodeError) as located:
                    CBOR.loads_with_locations(raw)
                self.assertEqual(str(plain.exception), str(located.exception))

    def test_budget_bounds_the_index(self):
        raw = cbor.encode([[1, 2, 3], [4, 5, 6]])
        data, full = CBOR.loads_with_locations(raw)
        self.assertTrue(full.complete)
        data_small, small = CBOR.loads_with_locations(raw, max_steps=4)
        self.assertEqual(data_small, data)                    # still decoded in full
        self.assertFalse(small.complete)
        self.assertEqual(small.span(()), (0, len(raw)))       # the root is always known
        self.assertIsNone(small.span((1, 2)))
        self.assertLess(len(small._items), len(full._items))

    def test_budget_is_not_spent_on_a_tag_twice(self):
        raw = bytes.fromhex("d9 01f5 82 01 02")
        _, locations = CBOR.loads_with_locations(raw, max_steps=5)
        self.assertEqual(locations.span(()), (0, 6))
        self.assertEqual(locations.span((0,)), (4, 5))
        self.assertEqual(locations.span((1,)), (5, 6))
        self.assertTrue(locations.complete)

    def test_zero_budget_still_decodes(self):
        raw = cbor.encode({1: [1, 2]})
        data, locations = CBOR.loads_with_locations(raw, max_steps=0)
        self.assertEqual(data, {1: [1, 2]})
        self.assertEqual(locations.span(()), (0, len(raw)))
        self.assertIsNone(locations.span((1,)))
        self.assertIsNone(locations.key_span((), 1))

    def test_default_budget(self):
        self.assertEqual(MAX_LOCATION_STEPS, 1_000_000)

    def test_loads_does_not_track(self):
        decoder = CBOR()
        self.assertIsNone(decoder._locations)
        CBOR.loads(cbor.encode({1: [1]}))


class TestDiagnosticFields(unittest.TestCase):

    def test_wrong_type_in_a_nested_array(self):
        data = {0: "a", 1: [1, 2, "three"]}
        raw = cbor.encode(data)
        result, found = diagnostics_of(data)
        self.assertFalse(result.valid)
        self.assertEqual(len(found), 1)
        diag = found[0]
        self.assertEqual(diag.category, "type")
        self.assertEqual(diag.severity, "error")
        self.assertEqual(diag.code, "validation")
        self.assertEqual(diag.path, (1, 2))
        self.assertEqual(diag.path_text, "/1/2")
        self.assertEqual(diag.rule, "measurement")
        self.assertEqual(diag.schema_location, "<schema>:1:1")
        self.assertEqual(diag.expected, "uint")
        self.assertEqual(diag.actual, "tstr")
        self.assertEqual(raw[diag.offset:diag.end_offset], cbor.encode("three"))
        self.assertEqual(diag.hex, cbor.encode("three").hex(" "))
        self.assertFalse(diag.hex_truncated)

    def test_value_violating_a_control(self):
        data = {0: "a", 1: [], 3: 200}
        raw = cbor.encode(data)
        _, found = diagnostics_of(data)
        self.assertEqual([d.category for d in found], ["value"])
        diag = found[0]
        self.assertEqual(diag.path, (3,))
        self.assertEqual(raw[diag.offset:diag.end_offset], cbor.encode(200))

    def test_size_violation(self):
        data = {0: "a", 1: [], 2: b"abc"}
        _, found = diagnostics_of(data)
        self.assertEqual([d.category for d in found], ["value"])
        self.assertEqual(found[0].path, (2,))

    def test_missing_member_points_at_its_map(self):
        data = {1: [1]}
        raw = cbor.encode(data)
        _, found = diagnostics_of(data)
        self.assertEqual([d.category for d in found], ["missing"])
        diag = found[0]
        self.assertEqual(diag.path, ())
        self.assertEqual((diag.offset, diag.end_offset), (0, len(raw)))
        self.assertIn("key 0", diag.expected)
        self.assertEqual(diag.actual, "absent")

    def test_unknown_member_points_at_its_key(self):
        data = {0: "a", 1: [], 9: 1}
        raw = cbor.encode(data)
        _, found = diagnostics_of(data)
        self.assertEqual([d.category for d in found], ["unknown"])
        diag = found[0]
        self.assertEqual(raw[diag.offset:diag.end_offset], b"\x09")
        self.assertIn("9", diag.actual)

    def test_tag_errors(self):
        schema = "r = #6.501({ 0: tstr })"
        result = validate(schema, cbor.encode({0: "a"}))
        diag = result.diagnostics[0]
        self.assertEqual(diag.category, "tag")
        self.assertEqual(diag.expected, "tag 501")
        self.assertEqual(diag.actual, "map")
        wrong = validate(schema, cbor.encode((7, {0: "a"}))).diagnostics[0]
        self.assertEqual((wrong.category, wrong.actual), ("tag", "tag 7"))

    def test_root_type_mismatch(self):
        result = validate("n = uint", cbor.encode("x"))
        diag = result.diagnostics[0]
        self.assertEqual(diag.path, ())
        self.assertEqual((diag.offset, diag.end_offset), (0, 2))
        self.assertEqual(diag.rule, "n")

    def test_unknown_root_type_is_a_schema_diagnostic(self):
        diag = validate(SCHEMA, {0: "a", 1: []}, root_type="nope").diagnostics[0]
        self.assertEqual(diag.category, "schema")
        self.assertIsNone(diag.offset)

    def test_decoded_data_has_no_offsets(self):
        _, found = diagnostics_of({0: "a", 1: ["x"]})
        found = validate(SCHEMA, {0: "a", 1: ["x"]}).diagnostics
        self.assertEqual(found[0].path, (1, 0))
        self.assertIsNone(found[0].offset)
        self.assertIsNone(found[0].hex)

    def test_integer_keys_stay_integers_in_the_path(self):
        diag = diagnostics_of({0: "a", 1: ["x"]})[1][0]
        self.assertEqual(diag.path, (1, 0))
        self.assertEqual(diag.to_dict()["path"], [1, 0])

    def test_text_keys(self):
        schema = "r = { name: tstr, tags: [* tstr] }"
        diag = diagnostics_of({"name": "a", "tags": ["x", 5]}, schema)[1][0]
        self.assertEqual(diag.path, ("tags", 1))
        self.assertEqual(diag.path_text, "/tags/1")

    def test_schema_path_and_rule_of_nested_types(self):
        schema = "outer = { inner: part }\npart = { n: uint }\n"
        diag = diagnostics_of({"inner": {"n": "x"}}, schema)[1][0]
        self.assertEqual(diag.path, ("inner", "n"))
        self.assertEqual(diag.rule, "part")
        self.assertEqual(diag.schema_location, "<schema>:2:1")
        self.assertEqual(diag.schema_path[0], "outer")

    def test_multiple_findings_in_order(self):
        data = {1: ["x"], 9: 1}
        categories = [d.category for d in diagnostics_of(data)[1]]
        # members in schema order (0 is missing, 1 is wrong), then unknown keys
        self.assertEqual(categories, ["missing", "type", "unknown"])

    def test_every_finding_has_a_known_severity_and_category(self):
        for data in ({1: ["x"], 9: 1}, {0: 5}, {0: "a", 1: [], 2: b"abc", 3: 999}):
            for diag in diagnostics_of(data)[1]:
                self.assertIn(diag.severity, SEVERITIES)
                self.assertIn(diag.category, CATEGORIES)

    def test_message_and_str_are_unchanged(self):
        diag = diagnostics_of({0: "a", 1: ["x"]})[1][0]
        self.assertEqual(str(diag), diag.message)
        self.assertEqual(validate(SCHEMA, {0: "a", 1: ["x"]}).errors, (diag.message,))

    def test_hex_excerpt_is_bounded(self):
        big = b"x" * 200
        schema = "r = { 0: tstr }"
        data = {0: big}
        raw = cbor.encode(data)
        diag = validate(schema, raw).diagnostics[0]
        self.assertEqual(diag.category, "type")
        self.assertEqual(len(diag.hex.split()), MAX_HEX_BYTES)
        self.assertTrue(diag.hex_truncated)
        self.assertEqual(diag.end_offset - diag.offset, len(cbor.encode(big)))

    def test_embedded_cbor_points_at_the_byte_string(self):
        schema = "r = { 0: bstr .cbor inner }\ninner = { 0: uint }\n"
        inner = cbor.encode({0: "no"})
        raw = cbor.encode({0: inner})
        found = validate(schema, raw).diagnostics
        self.assertTrue(found)
        for diag in found:
            self.assertEqual(raw[diag.offset:diag.end_offset], cbor.encode(inner))

    def test_input_too_large_to_index_still_fails_validation(self):
        original = CBOR.loads_with_locations
        raw = cbor.encode([1, "x", 3])
        with mock.patch.object(CBOR, "loads_with_locations",
                               staticmethod(lambda data: original(data, max_steps=1))):
            nested = validate("r = [* uint]", raw)
            root = validate("n = uint", raw)
        self.assertFalse(nested.valid)
        self.assertEqual(nested.diagnostics[0].path, (1,))
        self.assertIsNone(nested.diagnostics[0].offset)       # past the budget: no guess
        self.assertIsNone(nested.diagnostics[0].hex)
        self.assertFalse(root.valid)
        self.assertEqual((root.diagnostics[0].offset, root.diagnostics[0].end_offset),
                         (0, len(raw)))

    def test_valid_data_has_no_diagnostics(self):
        result, found = diagnostics_of({0: "a", 1: [1, 2], 2: b"ab", 3: 5})
        self.assertTrue(result.valid)
        self.assertEqual(found, ())


class TestDecodeDiagnostics(unittest.TestCase):

    def test_truncated(self):
        diag = validate("n = uint", b"\xa1\x01").diagnostics[0]
        self.assertEqual((diag.code, diag.category, diag.offset), ("decode", "decode", 2))
        self.assertEqual(diag.path, ())
        self.assertIsNone(diag.hex)            # nothing at the end of the input

    def test_trailing_data_shows_the_extra_bytes(self):
        diag = validate("n = uint", b"\x01\x02\x03").diagnostics[0]
        self.assertEqual(diag.offset, 1)
        self.assertEqual(diag.hex, "02 03")
        self.assertFalse(diag.hex_truncated)

    def test_invalid_utf8(self):
        diag = validate("n = tstr", b"\x62\xff\xfe").diagnostics[0]
        self.assertEqual(diag.category, "decode")
        self.assertEqual(diag.offset, 1)


class TestRenderers(unittest.TestCase):

    def setUp(self):
        self.result, self.found = diagnostics_of({0: "a", 1: [1, "x"], 9: 1})

    def test_text_is_one_message_per_line_by_default(self):
        text = render_text(self.found)
        self.assertEqual(text.splitlines(), [d.message for d in self.found])
        self.assertEqual(self.result.format(), text)

    def test_verbose_text(self):
        text = render_text(self.found, verbose=True)
        first = self.found[0]
        self.assertIn(f"error[{first.category}]: {first.message}", text)
        self.assertIn("at data /1/1, offset ", text)
        self.assertIn("in rule measurement (<schema>:1:1)", text)
        self.assertIn("expected uint, found tstr", text)
        self.assertIn("bytes 61 78", text)
        self.assertEqual(self.result.format(verbose=True), text)

    def test_verbose_text_without_location(self):
        diag = Diagnostic("plain")
        self.assertEqual(diag.format(), "error[other]: plain")

    def test_verbose_text_marks_truncated_bytes(self):
        diag = Diagnostic("m", offset=0, end_offset=40, hex="00 01", hex_truncated=True)
        self.assertIn("bytes 00 01 ...", diag.format())

    def test_decode_diagnostic_text_has_no_data_path(self):
        diag = validate("n = uint", b"\xa1\x01").diagnostics[0]
        self.assertNotIn("data /", diag.format())
        self.assertIn("offset 2", diag.format())

    def test_json_is_valid_and_complete(self):
        document = json.loads(render_json(self.found))
        self.assertEqual(document["format_version"], 1)
        self.assertEqual(len(document["diagnostics"]), len(self.found))
        keys = {"severity", "category", "code", "message", "path", "offset", "end_offset",
                "hex", "hex_truncated", "rule", "schema_path", "schema_location",
                "expected", "actual"}
        for item in document["diagnostics"]:
            self.assertEqual(set(item), keys)

    def test_json_reserved_members_cannot_be_overwritten(self):
        for name in ("format_version", "diagnostics"):
            with self.subTest(name=name):
                with self.assertRaises(TypeError) as caught:
                    render_json((), **{name: 2})
                self.assertIn(name, str(caught.exception))
        with self.assertRaises(TypeError):
            render_json((), format_version=2, diagnostics=[])
        self.assertTrue(self.result.to_json())  # its own members are not reserved

    def test_json_extra_members(self):
        document = json.loads(render_json((), indent=None, valid=True, root_type="r"))
        self.assertEqual(document, {"format_version": 1, "valid": True, "root_type": "r",
                                    "diagnostics": []})

    def test_result_to_json(self):
        document = json.loads(self.result.to_json())
        self.assertFalse(document["valid"])
        self.assertEqual(document["root_type"], "measurement")
        self.assertEqual(document, self.result.to_dict())
        self.assertEqual(len(document["diagnostics"]), 2)

    def test_json_is_deterministic(self):
        again = diagnostics_of({0: "a", 1: [1, "x"], 9: 1})[0]
        self.assertEqual(self.result.to_json(), again.to_json())

    def test_non_text_keys_are_text_in_json(self):
        diag = Diagnostic("m", path=(CBORKey(True), b"\xaa", None, 3, "a", CBORKey(1.5)))
        self.assertEqual(diag.to_dict()["path"], ["true", "h'aa'", "null", 3, "a", "1.5"])
        self.assertEqual(diag.path_text, "/true/h'aa'/null/3/a/1.5")
        json.loads(diag.to_json())

    def test_diagnostic_to_json(self):
        diag = self.found[0]
        self.assertEqual(json.loads(diag.to_json()), diag.to_dict())

    def test_diagnostics_are_hashable_and_immutable(self):
        diag = self.found[0]
        self.assertEqual(diag, self.found[0])
        hash(Diagnostic("m"))
        with self.assertRaises(Exception):
            diag.message = "other"

    def test_validator_reuse_does_not_leak_state(self):
        validator = Validator(SCHEMA)
        bad = validator.validate(cbor.encode({0: "a", 1: ["x"]}))
        good = validator.validate(cbor.encode({0: "a", 1: [1]}))
        again = validator.validate(cbor.encode({0: "a", 1: ["x"]}))
        self.assertEqual(len(bad.diagnostics), 1)
        self.assertEqual(good.diagnostics, ())
        self.assertEqual(bad.diagnostics, again.diagnostics)
        self.assertIsInstance(good, ValidationResult)


if __name__ == "__main__":
    unittest.main()
