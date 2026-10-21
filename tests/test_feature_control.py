"""``.feature`` (RFC 9165 section 4): the controller, the features a tool enables, the ones it used (#232)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cddl_verifier import SchemaError, Validator, validate
from cddl_verifier._cli import main
from cddl_verifier.cbor import encode

CHOICE = 'v = int .feature "ext-int" / tstr'


def check(schema, value, **options):
    return Validator(schema, **options).validate(encode(value))


class TestController(unittest.TestCase):
    """The controller is a feature name, or an array of the name and a detail."""

    def accepted(self, controller, preamble=""):
        Validator(f"{preamble}r = int .feature {controller}")

    def refused(self, controller, fragment, preamble=""):
        with self.assertRaises(SchemaError) as caught:
            Validator(f"{preamble}r = int .feature {controller}")
        self.assertIn(fragment, str(caught.exception))
        self.assertIn(".feature", str(caught.exception))
        return caught.exception

    def test_a_text_string(self):
        self.accepted('"a"')

    def test_an_array_of_the_name(self):
        self.accepted('["a"]')

    def test_an_array_of_the_name_and_a_detail(self):
        self.accepted('["a", "detail"]')
        self.accepted('["a", int]')
        self.accepted('["a", [1, 2]]')

    def test_a_computed_name(self):
        self.accepted('("a" .cat "b")')
        self.accepted('["a" .cat "b", "detail"]')

    def test_a_name_that_is_a_rule(self):
        self.accepted("name", 'name = "x"\n')
        self.accepted("[name]", 'name = "x"\n')
        self.accepted("pair", 'pair = ["x", "detail"]\n')

    def test_a_number_is_not_a_name(self):
        self.refused("5", "feature name")

    def test_a_byte_string_is_not_a_name(self):
        self.refused("h'61'", "feature name")

    def test_a_type_is_not_a_name(self):
        self.refused("int", "feature name")
        self.refused("tstr", "feature name")

    def test_an_array_starts_with_the_name(self):
        self.refused('[1, "a"]', "feature name")
        self.refused('[int]', "feature name")

    def test_an_array_has_a_name_and_at_most_one_detail(self):
        self.refused('["a", 1, 2]', "one detail")
        self.refused("[]", "one detail")

    def test_an_array_is_written_out(self):
        self.refused('[* "a"]', "written out")
        self.refused('["a", ? int]', "written out")

    def test_a_generic_parameter_is_checked_with_its_argument(self):
        pre = "g<T> = int .feature T\n"
        Validator(pre + 'r = g<"x">')
        error = self.refused_generic(pre + "r = g<5>")
        self.assertIn("passes it", str(error))

    def refused_generic(self, schema):
        with self.assertRaises(SchemaError) as caught:
            Validator(schema)
        return caught.exception

    def test_the_error_is_at_the_argument(self):
        error = self.refused("5", "feature name")
        self.assertEqual(error.line, 1)
        self.assertEqual(error.column, len("r = int .feature ") + 1)

    def test_schema_diagnostics_report_it(self):
        from cddl_verifier import schema_diagnostics
        found = schema_diagnostics("r = int .feature 5")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].category, "schema")


class TestDefault(unittest.TestCase):
    """No ``features``: every feature is enabled and the base type alone decides."""

    def test_the_issue_example(self):
        self.assertTrue(check(CHOICE, 42).valid)
        self.assertTrue(check(CHOICE, "x").valid)
        self.assertFalse(check(CHOICE, 4.0).valid)

    def test_features_none_is_the_default(self):
        self.assertTrue(check(CHOICE, 42, features=None).valid)

    def test_a_bare_feature_is_its_base_type(self):
        schema = 'r = uint .feature "f"'
        self.assertTrue(check(schema, 3).valid)
        self.assertFalse(check(schema, -3).valid)
        self.assertFalse(check(schema, "x").valid)


class TestEnabled(unittest.TestCase):
    """With a set of names, ``T .feature F`` matches only while F is in it."""

    def test_an_enabled_feature_matches(self):
        self.assertTrue(check(CHOICE, 42, features={"ext-int"}).valid)

    def test_a_disabled_feature_does_not_match(self):
        result = check(CHOICE, 42, features=set())
        self.assertFalse(result.valid)
        self.assertIn("feature 'ext-int' is not enabled", result.errors[0])

    def test_another_feature_is_not_this_one(self):
        self.assertFalse(check(CHOICE, 42, features={"other"}).valid)

    def test_a_choice_falls_through_to_the_other_alternative(self):
        self.assertTrue(check(CHOICE, "x", features=set()).valid)
        self.assertFalse(check(CHOICE, 4.0, features=set()).valid)

    def test_a_lone_disabled_feature_names_it(self):
        result = check('r = uint .feature "f"', 3, features={"g"})
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].category, "type")
        self.assertIn("feature 'f' is not enabled", result.errors[0])

    def test_the_type_is_still_checked_when_enabled(self):
        self.assertFalse(check('r = uint .feature "f"', -3, features={"f"}).valid)
        self.assertFalse(check('r = uint .feature "f"', "x", features={"f"}).valid)

    def test_the_name_may_be_computed(self):
        schema = 'r = uint .feature ("a" .cat "b")'
        self.assertTrue(check(schema, 1, features={"ab"}).valid)
        self.assertFalse(check(schema, 1, features={"a"}).valid)

    def test_an_array_controller_is_keyed_by_its_name(self):
        schema = 'r = uint .feature ["f", "some detail"]'
        self.assertTrue(check(schema, 1, features={"f"}).valid)
        self.assertFalse(check(schema, 1, features={"some detail"}).valid)

    def test_a_generic_instance_is_not_expanded_so_its_parameter_names_no_feature(self):
        # as for the other controls of a generic rule: accepted, and no feature recorded
        schema = 'g<T> = int .feature T\nr = g<"x">'
        result = check(schema, 1, features=set())
        self.assertTrue(result.valid)
        self.assertEqual(result.features_used, ())

    def test_any_collection_of_names(self):
        for names in (["ext-int"], ("ext-int",), frozenset({"ext-int"}), iter(["ext-int"]),
                      {"ext-int": 1}):
            with self.subTest(type(names).__name__):
                self.assertTrue(check(CHOICE, 42, features=names).valid)

    def test_one_string_is_refused(self):
        with self.assertRaises(TypeError) as caught:
            Validator(CHOICE, features="ext-int")
        self.assertIn("{'name'}", str(caught.exception))

    def test_names_are_text(self):
        with self.assertRaises(TypeError):
            Validator(CHOICE, features={1})

    def test_the_validate_function_takes_them(self):
        self.assertTrue(validate(CHOICE, encode(42), features={"ext-int"}).valid)
        self.assertFalse(validate(CHOICE, encode(42), features=set()).valid)

    def test_a_validator_is_reusable(self):
        validator = Validator(CHOICE, features={"ext-int"})
        for _ in range(3):
            self.assertTrue(validator.validate(encode(42)).valid)
            self.assertFalse(validator.validate(encode(4.0)).valid)

    def test_edn_output_works_with_features(self):
        self.assertEqual(Validator(CHOICE, features={"ext-int"}).to_edn(encode(42), root_type="v"), "42")


class TestRfcExamples(unittest.TestCase):
    """The patterns of RFC 9165 section 4 (figures 7 to 9)."""

    def test_map_extensibility(self):
        schema = 'person = { name: tstr, * (text .feature "further-person-extension") => any }'
        data = {"name": "a", "color": "blue"}
        self.assertTrue(check(schema, data).valid)
        self.assertTrue(check(schema, data, features={"further-person-extension"}).valid)
        self.assertTrue(check(schema, {"name": "a"}, features=set()).valid)
        rejected = check(schema, data, features=set())
        self.assertFalse(rejected.valid)

    def test_type_extensibility(self):
        schema = 'allowed = [* (int / tstr / (any .feature "allowed-type-extension"))]'
        self.assertTrue(check(schema, [1, "a", 2.5]).valid)
        self.assertTrue(check(schema, [1, "a", 2.5], features={"allowed-type-extension"}).valid)
        self.assertTrue(check(schema, [1, "a"], features=set()).valid)
        self.assertFalse(check(schema, [1, "a", 2.5], features=set()).valid)

    def test_variants(self):
        schema = 'v = tstr .feature "json" / bstr .feature "cbor"'
        self.assertTrue(check(schema, "x", features={"json"}).valid)
        self.assertFalse(check(schema, b"x", features={"json"}).valid)
        self.assertTrue(check(schema, b"x", features={"cbor"}).valid)
        self.assertFalse(check(schema, "x", features={"cbor"}).valid)
        self.assertTrue(check(schema, "x").valid and check(schema, b"x").valid)


class TestMembersAndElements(unittest.TestCase):

    def test_a_member_value(self):
        schema = 'r = { a: int .feature "x", b: tstr }'
        self.assertTrue(check(schema, {"a": 1, "b": "s"}, features={"x"}).valid)
        self.assertFalse(check(schema, {"a": 1, "b": "s"}, features=set()).valid)

    def test_an_optional_member(self):
        schema = 'r = { ? a: int .feature "x", b: tstr }'
        self.assertTrue(check(schema, {"b": "s"}, features=set()).valid)
        self.assertFalse(check(schema, {"a": 1, "b": "s"}, features=set()).valid)

    def test_array_elements(self):
        schema = 'r = [* int .feature "x"]'
        self.assertTrue(check(schema, [1, 2], features={"x"}).valid)
        self.assertFalse(check(schema, [1, 2], features=set()).valid)
        self.assertTrue(check(schema, [], features=set()).valid)

    def test_a_rule_that_is_a_feature(self):
        schema = 'r = [ext]\next = int .feature "x"'
        self.assertTrue(check(schema, [1], features={"x"}).valid)
        self.assertFalse(check(schema, [1], features=set()).valid)

    def test_a_feature_with_other_controls(self):
        schema = 'r = (uint .size 1) .feature "x"'
        self.assertTrue(check(schema, 200, features={"x"}).valid)
        self.assertFalse(check(schema, 300, features={"x"}).valid)
        self.assertFalse(check(schema, 200, features=set()).valid)

    def test_a_feature_beside_another_set_control(self):
        schema = 'r = (uint .feature "x") .le 5'
        self.assertTrue(check(schema, 3, features={"x"}).valid)
        self.assertFalse(check(schema, 9, features={"x"}).valid)
        self.assertFalse(check(schema, 3, features=set()).valid)

    def test_a_group_choice_in_a_map(self):
        schema = 'r = { a: int .feature "x" // b: tstr }'
        self.assertTrue(check(schema, {"b": "s"}, features=set()).valid)
        self.assertFalse(check(schema, {"a": 1}, features=set()).valid)
        self.assertTrue(check(schema, {"a": 1}, features={"x"}).valid)

    def test_a_tag(self):
        schema = 'r = #6.55799(int .feature "x")'
        from cddl_verifier.cbor import CBORTag
        self.assertTrue(check(schema, CBORTag(55799, 1), features={"x"}).valid)
        self.assertFalse(check(schema, CBORTag(55799, 1), features=set()).valid)

    def test_embedded_cbor(self):
        schema = 'r = bstr .cbor (int .feature "x")'
        self.assertTrue(check(schema, encode(1), features={"x"}).valid)
        self.assertFalse(check(schema, encode(1), features=set()).valid)


class TestFeaturesUsed(unittest.TestCase):
    """The first mode of RFC 9165: say which features the data that matched used."""

    def test_a_used_feature_is_listed(self):
        self.assertEqual(check(CHOICE, 42).features_used, ("ext-int",))

    def test_an_alternative_that_is_not_the_match_is_not_listed(self):
        self.assertEqual(check(CHOICE, "x").features_used, ())

    def test_only_the_matching_branch_counts(self):
        schema = 'r = (int .feature "a") / (tstr .feature "b") / (float .feature "c")'
        self.assertEqual(check(schema, 1).features_used, ("a",))
        self.assertEqual(check(schema, "s").features_used, ("b",))
        self.assertEqual(check(schema, 1.5).features_used, ("c",))

    def test_a_map_branch_that_failed_leaves_nothing(self):
        schema = 'r = { a: int .feature "k", z: int } / { a: int .feature "m", b: int }'
        self.assertEqual(check(schema, {"a": 1, "b": 2}).features_used, ("m",))
        self.assertEqual(check(schema, {"a": 1, "z": 2}).features_used, ("k",))

    def test_a_group_choice_that_failed_leaves_nothing(self):
        schema = 'r = { a: int .feature "k", z: int // a: int .feature "m", b: int }'
        self.assertEqual(check(schema, {"a": 1, "b": 2}).features_used, ("m",))

    def test_several_features_in_the_order_met(self):
        schema = 'r = [int .feature "a", tstr .feature "b", bstr .feature "c"]'
        self.assertEqual(check(schema, [1, "s", b"x"]).features_used, ("a", "b", "c"))

    def test_a_feature_is_listed_once(self):
        self.assertEqual(check('r = [* int .feature "a"]', [1, 2, 3]).features_used, ("a",))

    def test_an_array_controller_lists_its_name(self):
        self.assertEqual(check('r = int .feature ["a", "detail"]', 1).features_used, ("a",))

    def test_an_enabled_feature_is_listed(self):
        self.assertEqual(check(CHOICE, 42, features={"ext-int"}).features_used, ("ext-int",))

    def test_a_disabled_feature_is_not_listed(self):
        self.assertEqual(check('r = (int .feature "a") / int', 1, features=set()).features_used, ())

    def test_an_invalid_result_lists_none(self):
        result = check('r = [int .feature "a", tstr]', [1, 2])
        self.assertFalse(result.valid)
        self.assertEqual(result.features_used, ())

    def test_a_schema_without_features_lists_none(self):
        self.assertEqual(check("r = int", 1).features_used, ())

    def test_each_validation_starts_over(self):
        validator = Validator(CHOICE)
        self.assertEqual(validator.validate(encode(1)).features_used, ("ext-int",))
        self.assertEqual(validator.validate(encode("x")).features_used, ())
        self.assertEqual(validator.validate(encode(1)).features_used, ("ext-int",))

    def test_a_probe_of_a_computed_key_member_is_not_a_use(self):
        schema = 'r = { ? "z" => bstr, ? tstr => ((int .feature "a") / tstr), * tstr => any }'
        self.assertEqual(check(schema, {"a": "text", "z": 1}).features_used, ())
        self.assertEqual(check(schema, {"a": 5}).features_used, ("a",))

    def test_the_member_an_entry_goes_to_uses_its_key_feature(self):
        schema = ('p = { * (tstr .feature "k") => int, * tstr => any }')
        self.assertEqual(check(schema, {"x": 1}).features_used, ("k",))
        self.assertEqual(check(schema, {"x": "s"}).features_used, ())

    def test_an_optional_array_entry_that_took_no_element_is_not_a_use(self):
        schema = 'r = [? (int .feature "a"), int]'
        self.assertEqual(check(schema, [1]).features_used, ())
        self.assertEqual(check(schema, [1, 2]).features_used, ("a",))

    def test_array_entries_that_took_elements_are_uses(self):
        schema = 'r = [* int .feature "a", * tstr .feature "b"]'
        self.assertEqual(check(schema, [1, "s"]).features_used, ("a", "b"))
        self.assertEqual(check(schema, [1, 2]).features_used, ("a",))
        self.assertEqual(check(schema, ["s"]).features_used, ("b",))
        self.assertEqual(check(schema, []).features_used, ())

    def test_an_array_in_a_group_choice(self):
        schema = 'r = [(int .feature "a", tstr) // (int, tstr .feature "b")]'
        self.assertIn(check(schema, [1, "s"]).features_used, (("a",), ("b",)))
        self.assertTrue(check(schema, [1, "s"]).valid)

    def test_a_recursive_group_in_an_array(self):
        schema = 'r = [g]\ng = (? g, int .feature "n")'
        self.assertEqual(check(schema, [1, 2, 3]).features_used, ("n",))

    def test_the_choice_checker_of_the_schema_does_not_keep_what_it_probes(self):
        validator = Validator('t = int .feature "a" / tstr .feature "b"\nr = {x: t}')
        for _ in range(3):
            validator.validate(encode({"x": 1}))
            validator.to_edn(encode({"x": 1}), root_type="r")
        checker = validator._parser._choice_checker
        self.assertTrue(checker is None or checker._feature_trail == [])

    def test_extension_members_are_listed(self):
        schema = 'p = { name: tstr, * (text .feature "further") => any }'
        self.assertEqual(check(schema, {"name": "a"}).features_used, ())
        self.assertEqual(check(schema, {"name": "a", "x": 1}).features_used, ("further",))

    def test_json(self):
        result = check(CHOICE, 42)
        document = json.loads(result.to_json())
        self.assertEqual(document["features_used"], ["ext-int"])
        self.assertEqual(result.to_dict()["features_used"], ["ext-int"])
        self.assertEqual(document["format_version"], 1)

    def test_json_is_unchanged_when_no_feature_was_used(self):
        document = json.loads(check(CHOICE, "x").to_json())
        self.assertNotIn("features_used", document)
        self.assertEqual(set(document), {"format_version", "valid", "root_type", "diagnostics"})

    def test_the_analyzer_exposes_them(self):
        from cddl_verifier import _analyzer, _schema
        parser = _schema.CDDLParser('r = [int .feature "a", tstr .feature "b", int .feature "a"]')
        analyzer = _analyzer.CBORAnalyzer(parser)
        self.assertEqual(analyzer.features_used, ())
        self.assertTrue(analyzer.validate([1, "s", 2], "r"))
        self.assertEqual(analyzer.features_used, ("a", "b"))

    def test_the_result_still_compares_by_value(self):
        self.assertEqual(check(CHOICE, 42), check(CHOICE, 42))
        self.assertNotEqual(check(CHOICE, 42), check(CHOICE, "x"))


class TestCli(unittest.TestCase):

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main(list(args))
            except SystemExit as exit_:
                code = exit_.code or 0
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "s.cddl").write_text('v = int .feature "ext-int" / int .feature "other" / tstr\n',
                                     encoding="utf-8")
        (root / "int.cbor").write_bytes(encode(42))
        (root / "text.cbor").write_bytes(encode("x"))
        self.schema, self.integer, self.text = (str(root / "s.cddl"), str(root / "int.cbor"),
                                                str(root / "text.cbor"))

    def test_every_feature_is_enabled_by_default(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--type", "v")
        self.assertEqual(code, 0, err)
        self.assertIn("Features used: ext-int", err)

    def test_one_feature_enables_only_that_one(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--type", "v", "--feature", "other")
        self.assertEqual(code, 0, err)
        self.assertIn("Features used: other", err)

    def test_a_feature_is_repeatable(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--type", "v",
                                       "--feature", "x", "--feature", "ext-int")
        self.assertEqual(code, 0, err)
        self.assertIn("Features used: ext-int", err)

    def test_a_feature_the_schema_does_not_use_enables_nothing(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--type", "v", "--feature", "x")
        self.assertEqual(code, 1)
        self.assertIn("feature 'ext-int' is not enabled", err)

    def test_no_features(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--type", "v", "--no-features")
        self.assertEqual(code, 1)
        self.assertIn("not enabled", err)
        code, _out, err = self.run_cli(self.schema, self.text, "--type", "v", "--no-features")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Features used", err)

    def test_the_two_options_exclude_each_other(self):
        code, _out, err = self.run_cli(self.schema, self.integer, "--feature", "a", "--no-features")
        self.assertEqual(code, 2)
        self.assertIn("cannot be used together", err)

    def test_json_lists_the_features_used(self):
        code, out, err = self.run_cli(self.schema, self.integer, "--type", "v", "--diagnostics", "json",
                                      "--feature", "other")
        self.assertEqual(code, 0, err)
        document = json.loads(out)
        self.assertEqual(document["features_used"], ["other"])
        self.assertTrue(document["valid"])

    def test_json_without_a_feature_used_is_as_before(self):
        code, out, err = self.run_cli(self.schema, self.text, "--type", "v", "--diagnostics", "json")
        self.assertEqual(code, 0, err)
        self.assertNotIn("features_used", json.loads(out))

    def test_json_failure_names_the_feature(self):
        code, out, _err = self.run_cli(self.schema, self.integer, "--type", "v", "--diagnostics", "json",
                                       "--no-features")
        self.assertEqual(code, 1)
        document = json.loads(out)
        self.assertFalse(document["valid"])
        self.assertNotIn("features_used", document)
        self.assertIn("not enabled", document["diagnostics"][0]["message"])


if __name__ == "__main__":
    unittest.main()
