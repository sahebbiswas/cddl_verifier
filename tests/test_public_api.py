#!/usr/bin/env python3
"""Tests for the public ``cddl_verifier`` API: validate(), Validator, CBOR and
JSON helpers, and the ``python -m cddl_verifier`` CLI entry point.

These tests only use public names, so they also check what an installed
package exposes.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

# When run directly (python3 tests/test_foo.py) without the package installed,
# fall back to the source tree. pytest handles this via tests/conftest.py.
try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cddl_verifier
from cddl_verifier import (
    CBORDecodeError,
    Diagnostic,
    SchemaError,
    ValidationResult,
    Validator,
    validate,
)
from cddl_verifier import cbor, json_codec

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "cddl-schemas"
DATA = REPO_ROOT / "test-data"

PERSON = "person = { name: tstr, age: uint }\n"


class TestPackage(unittest.TestCase):
    def test_version(self):
        self.assertRegex(cddl_verifier.__version__, r"^\d+\.\d+\.\d+")

    def test_all_names_exist(self):
        for name in cddl_verifier.__all__:
            self.assertTrue(hasattr(cddl_verifier, name), name)


class TestValidate(unittest.TestCase):
    def test_valid_bytes(self):
        result = validate(PERSON, cbor.encode({"name": "Alice", "age": 30}))
        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.valid)
        self.assertTrue(result)
        self.assertEqual(result.root_type, "person")
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.data, {"name": "Alice", "age": 30})

    def test_valid_decoded_data(self):
        self.assertTrue(validate(PERSON, {"name": "Alice", "age": 30}).valid)

    def test_bytearray_and_memoryview(self):
        raw = cbor.encode({"name": "Alice", "age": 30})
        self.assertTrue(validate(PERSON, bytearray(raw)).valid)
        self.assertTrue(validate(PERSON, memoryview(raw)).valid)

    def test_invalid_data(self):
        result = validate(PERSON, {"name": "Alice", "age": -1})
        self.assertFalse(result.valid)
        self.assertFalse(result)
        self.assertEqual(len(result.diagnostics), 1)
        diag = result.diagnostics[0]
        self.assertIsInstance(diag, Diagnostic)
        self.assertEqual(diag.code, "validation")
        self.assertEqual(diag.severity, "error")
        self.assertIn("age", diag.message)
        self.assertEqual(result.errors, (diag.message,))

    def test_unknown_root_type(self):
        result = validate(PERSON, {"name": "Alice", "age": 1}, root_type="nope")
        self.assertFalse(result.valid)
        self.assertEqual(result.root_type, "nope")
        self.assertIn("nope", result.errors[0])

    def test_truncated_cbor_is_a_decode_diagnostic(self):
        result = validate(PERSON, b"\xa1\x01")
        self.assertFalse(result.valid)
        self.assertIsNone(result.data)
        diag = result.diagnostics[0]
        self.assertEqual(diag.code, "decode")
        self.assertEqual(diag.offset, 2)

    def test_trailing_bytes_are_a_decode_diagnostic(self):
        result = validate("n = uint\n", b"\x01\x02")
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].code, "decode")
        self.assertEqual(result.diagnostics[0].offset, 1)

    def test_schema_from_path(self):
        result = validate(SCHEMAS / "example_iana.cddl",
                          (DATA / "example_iana.cbor").read_bytes())
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.root_type, "message")

    def test_schema_path_must_exist(self):
        with self.assertRaises(SchemaError):
            Validator(SCHEMAS / "does-not-exist.cddl")

    def test_schema_type_checked(self):
        with self.assertRaises(TypeError):
            Validator(b"person = uint")

    def test_schema_without_rules_needs_root_type(self):
        with self.assertRaises(SchemaError):
            validate("", 1)

    def test_corim_root_type(self):
        result = validate(SCHEMAS / "unified.cddl",
                          (DATA / "minimal-corim.cbor").read_bytes(),
                          root_type="corim-map")
        self.assertTrue(result.valid, result.errors)

    def test_primitive_root(self):
        self.assertTrue(validate("n = uint", 1).valid)
        self.assertTrue(validate("a = n\nn = tstr", "x").valid)
        result = validate("n = uint", -1)
        self.assertFalse(result.valid)
        self.assertIn("uint", result.errors[0])
        self.assertFalse(validate("n = bool", 1).valid)

    def test_constrained_primitive_root_is_not_silently_accepted(self):
        self.assertFalse(validate("n = tstr .size 2", "abc").valid)

    def test_tagged_root_checks_tag_number(self):
        schema = "root = #6.501(inner)\ninner = { a: uint }\n"
        self.assertTrue(validate(schema, (501, {"a": 1})).valid)
        wrong = validate(schema, (502, {"a": 1}))
        self.assertFalse(wrong.valid)
        self.assertIn("501", wrong.errors[0])
        self.assertFalse(validate(schema, {"a": 1}).valid)
        self.assertFalse(validate(schema, (501, {"a": "x"})).valid)

    def test_tagged_primitive_root(self):
        self.assertTrue(validate("r = #6.1(uint)", (1, 5)).valid)
        self.assertFalse(validate("r = #6.1(uint)", (1, "x")).valid)
        self.assertFalse(validate("r = #6.1(uint)", (2, 5)).valid)

    def test_tagged_root_from_cbor_bytes(self):
        result = validate(SCHEMAS / "unified.cddl",
                          (DATA / "minimal-corim.cbor").read_bytes(),
                          root_type="tagged-unsigned-corim-map")
        self.assertTrue(result.valid, result.errors)

    def test_schema_file_not_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = Path(tmp) / "bad.cddl"
            schema.write_bytes(b"n = uint ; \xff\xfe\n")
            with self.assertRaises(SchemaError):
                Validator(schema)

    def test_library_is_silent(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            validate(PERSON, {"name": "Alice", "age": -1})
        self.assertEqual(stderr.getvalue(), "")


class TestValidator(unittest.TestCase):
    def test_reuse(self):
        validator = Validator(PERSON)
        self.assertEqual(validator.default_root_type, "person")
        self.assertTrue(validator.validate({"name": "a", "age": 1}).valid)
        self.assertFalse(validator.validate({"name": "a"}).valid)
        self.assertTrue(validator.validate({"name": "b", "age": 2}).valid)

    def test_to_edn_annotated(self):
        validator = Validator(SCHEMAS / "example_iana.cddl")
        edn = validator.to_edn((DATA / "example_iana.cbor").read_bytes())
        self.assertIn("/ message /", edn)
        self.assertIn('"Hello, World!"', edn)

    def test_to_edn_plain(self):
        edn = Validator(PERSON).to_edn({"name": "a", "age": 1}, annotate=False)
        self.assertNotIn("/ name /", edn)
        self.assertIn('"a"', edn)

    def test_to_edn_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            Validator(PERSON).to_edn({}, edn_format="xml")

    def test_to_edn_raises_on_bad_cbor(self):
        with self.assertRaises(CBORDecodeError):
            Validator(PERSON).to_edn(b"\xa1")


class TestCBORHelpers(unittest.TestCase):
    def test_round_trip(self):
        value = {"a": [1, 2.5, None, True], "b": b"\x00\xff", 3: "x"}
        self.assertEqual(cbor.decode(cbor.encode(value)), value)

    def test_canonical_flag(self):
        self.assertEqual(cbor.encode({"b": 1, "a": 2}, canonical=True),
                         cbor.encode({"a": 2, "b": 1}, canonical=True))

    def test_decode_errors(self):
        with self.assertRaises(cbor.CBORTruncatedError) as ctx:
            cbor.decode(b"\x82\x01")
        self.assertEqual(ctx.exception.offset, 2)
        with self.assertRaises(cbor.CBORTrailingDataError):
            cbor.decode(b"\x01\x02")
        self.assertIs(cbor.CBORDecodeError, CBORDecodeError)

    def test_diag_dump(self):
        self.assertIn("uint(42)", cbor.diag_dump(b"\x18\x2a"))


class TestJSONCodec(unittest.TestCase):
    def test_round_trip(self):
        raw = json_codec.json_to_cbor('{"a": [1, "x", null]}')
        self.assertEqual(cbor.decode(raw), {"a": [1, "x", None]})
        self.assertEqual(json_codec.cbor_to_json(raw), '{"a": [1, "x", null]}')


class TestModuleCLI(unittest.TestCase):
    """``python -m cddl_verifier`` behaves like the ``cddl-verify`` script."""

    def _run(self, *args):
        package_root = str(Path(cddl_verifier.__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (package_root, env.get("PYTHONPATH")) if p)
        return subprocess.run([sys.executable, "-m", "cddl_verifier", *args],
                              capture_output=True, text=True, env=env)

    def test_help(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("cddl-verify", result.stdout)
        self.assertIn("--type", result.stdout)

    def test_validate_and_write_edn(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.edn"
            result = self._run(str(SCHEMAS / "example_iana.cddl"),
                               str(DATA / "example_iana.cbor"),
                               "--type", "message", "--output", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Validation successful", result.stderr)
            self.assertIn("/ message /", out.read_text(encoding="utf-8"))

    def test_validation_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = Path(tmp) / "s.cddl"
            schema.write_text("root = tstr\n")
            data = Path(tmp) / "d.cbor"
            data.write_bytes(cbor.encode(1))
            result = self._run(str(schema), str(data), "--type", "root")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Validation failed", result.stderr)

    def test_show_types(self):
        result = self._run(str(SCHEMAS / "example_iana.cddl"),
                           str(DATA / "example_iana.cbor"), "--show-types")
        self.assertEqual(result.returncode, 0)
        self.assertIn("message", result.stderr)


if __name__ == "__main__":
    unittest.main()
