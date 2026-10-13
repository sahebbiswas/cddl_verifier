#!/usr/bin/env python3
"""Choices are resolved by matching alone: no first-alternative fallback (#74)."""

import contextlib
import io
import sys
import tempfile
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import unittest

from cddl_verifier import Validator
from cddl_verifier._analyzer import CDDLParser, main
from cddl_verifier.cbor import encode
from cddl_verifier._cddl.typeexpr import as_type
from ._tables import as_text

MAPS = "m1 = {1: int}\nm2 = {2: tstr}\n"
SOCKET = "r = $s\n$s /= m1\n$s /= m2\n" + MAPS


def edn(schema, data, root=None):
    return " ".join(Validator(schema).to_edn(data, root).split())


class Matching(unittest.TestCase):
    def setUp(self):
        self.p = CDDLParser(SOCKET)

    def test_matches_are_in_schema_order(self):
        self.assertEqual(as_text(self.p.choice_matches("$s", {2: "x"})), ["m2"])
        self.assertEqual(as_text(self.p.choice_matches("$s", {1: 1})), ["m1"])

    def test_no_match_is_empty_not_the_first_alternative(self):
        self.assertEqual(self.p.choice_matches("$s", {3: 1}), [])
        self.assertIsNone(self.p.resolve_type_choice_for_data("$s", {3: 1}))
        self.assertIsNone(self.p.get_type("$s", {3: 1}))

    def test_a_choice_has_no_definition_without_data(self):
        self.assertIsNone(self.p.get_type("$s"))

    def test_several_matches_use_the_first(self):
        p = CDDLParser("$s /= m1\n$s /= m2\nm1 = {1: int, ? 2: tstr}\nm2 = {1: int, ? 3: tstr}")
        self.assertEqual(as_text(p.choice_matches("$s", {1: 1})), ["m1", "m2"])
        self.assertEqual(as_text(p.resolve_type_choice_for_data("$s", {1: 1})), "m1")

    def test_tags_are_matched_with_their_content(self):
        p = CDDLParser("$s /= #6.7(m1)\n$s /= #6.7(m2)\n" + MAPS)
        self.assertEqual(as_text(p.choice_matches("$s", (7, {2: "x"}))), ["#6.7(m2)"])
        self.assertEqual(p.choice_matches("$s", (8, {2: "x"})), [])

    def test_inline_choices(self):
        p = CDDLParser("r = {x: m1 / m2}\n" + MAPS)
        self.assertEqual(as_text(p.choice_alternatives(as_type("m1 / m2"))), ["m1", "m2"])
        self.assertIsNone(p.choice_alternatives("m1"))
        self.assertEqual(as_text(p.choice_matches(as_type("m1 / m2"), {2: "x"})), ["m2"])


class EdnLabels(unittest.TestCase):
    def test_socket_label_is_the_matching_alternative(self):
        self.assertIn("/ m2 /", edn(SOCKET, {2: "x"}))
        self.assertIn("/ m1 /", edn(SOCKET, {1: 1}))

    def test_same_tag_alternatives(self):
        text = edn("r = $s\n$s /= #6.7(m1)\n$s /= #6.7(m2)\n" + MAPS, (7, {2: "x"}))
        self.assertIn("#6.7(m2)", text)
        self.assertNotIn("#6.7(m1)", text)

    def test_no_match_is_not_labelled_with_an_alternative(self):
        text = edn("r = {x: $s}\n$s /= m1\n$s /= m2\n" + MAPS, {"x": {2: 5}})
        self.assertNotIn("m1", text)
        self.assertNotIn("m2", text)
        text = edn("r = $s\n$s /= #6.7(m1)\n$s /= #6.8(m2)\n" + MAPS, (9, {2: "x"}))
        self.assertNotIn("m1", text)
        self.assertNotIn("m2", text)

    def test_inline_choice_label(self):
        text = edn("r = {x: m1 / m2}\n" + MAPS, {"x": {2: "x"}})
        self.assertIn("/ m2 /", text)
        self.assertNotIn("m1 / m2", text)

    def test_every_element_of_a_repeated_choice_is_labelled(self):
        text = edn("r = [* $s]\n$s /= m1\n$s /= m2\n" + MAPS, [{1: 1}, {2: "x"}, {1: 2}])
        self.assertEqual([text.count("/ m1 /"), text.count("/ m2 /")], [2, 1])

    def test_trailing_repeat_in_a_longer_array(self):
        text = edn("r = [tstr, * $s]\n$s /= m1\n$s /= m2\n" + MAPS, ["a", {1: 1}, {2: "x"}])
        self.assertEqual([text.count("/ m1 /"), text.count("/ m2 /")], [1, 1])


class Validation(unittest.TestCase):
    def setUp(self):
        self.v = Validator(SOCKET + "c = m1 / m2\nr2 = {x: $s, y: [* $s]}")

    def test_any_match_is_valid_and_none_fails_with_every_reason(self):
        self.assertTrue(self.v.validate({2: "x"}).valid)
        result = self.v.validate({3: 1})
        self.assertFalse(result.valid)
        self.assertIn("matches none of m1 / m2", result.errors[0])
        self.assertIn("m1:", result.errors[0])
        self.assertIn("m2:", result.errors[0])

    def test_choice_as_root_type(self):
        for root in ("$s", "c"):
            with self.subTest(root=root):
                self.assertTrue(self.v.validate({1: 1}, root).valid)
                self.assertIn("matches none of", self.v.validate({3: 1}, root).errors[0])

    def test_choice_in_fields_and_elements(self):
        self.assertTrue(self.v.validate({"x": {1: 1}, "y": [{2: "a"}]}, "r2").valid)
        self.assertFalse(self.v.validate({"x": {1: "no"}, "y": []}, "r2").valid)
        self.assertFalse(self.v.validate({"x": {1: 1}, "y": [{2: 1}]}, "r2").valid)


class UniqueChoices(unittest.TestCase):
    SCHEMA = ("r = $s\n$s /= m1\n$s /= m2\n"
              "m1 = {1: int, ? 2: tstr}\nm2 = {1: int, ? 3: tstr}\n")

    def test_default_accepts_ambiguous_data(self):
        self.assertTrue(Validator(self.SCHEMA).validate({1: 1}).valid)

    def test_unique_choices_reports_it(self):
        v = Validator(self.SCHEMA, unique_choices=True)
        result = v.validate({1: 1})
        self.assertFalse(result.valid)
        self.assertIn("matches more than one alternative of $s: m1, m2", result.errors[0])
        self.assertTrue(v.validate({1: 1, 2: "x"}).valid)  # only m1 fits
        self.assertTrue(v.validate({1: 1, 3: "x"}).valid)  # only m2 fits

    def test_overlapping_primitives_are_not_ambiguous(self):
        v = Validator("r = {a: uint / int, b: tstr / \"x\"}", unique_choices=True)
        self.assertTrue(v.validate({"a": 1, "b": "x"}).valid)

    def test_inline_choice_and_tags(self):
        schema = "r = {x: m1 / m2, y: $t}\n$t /= #6.7(m1)\n$t /= #6.7(m2)\n" + self.SCHEMA.split("\n", 3)[3]
        v = Validator(schema, unique_choices=True)
        self.assertEqual(len(v.validate({"x": {1: 1}, "y": (7, {1: 1})}).errors), 2)

    def test_cli_flag(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "s.cddl").write_text(self.SCHEMA, encoding="utf-8")
            (root / "d.cbor").write_bytes(encode({1: 1}))
            codes = []
            for flags in ([], ["--unique-choices"]):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        main([str(root / "s.cddl"), str(root / "d.cbor"), "--type", "r", *flags])
                        codes.append(0)
                    except SystemExit as e:
                        codes.append(e.code or 0)
            self.assertEqual(codes[0], 0)
            self.assertNotEqual(codes[1], 0)


if __name__ == "__main__":
    unittest.main()
