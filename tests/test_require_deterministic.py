"""``require_deterministic``: reject input that is not deterministically encoded (#95).

RFC 8949 §4.2.1: shortest heads, definite lengths, shortest floats, map keys in
bytewise order of their encodings, and bignums only where an integer does not fit.
What the check accepts is exactly what ``encode(canonical=True)`` writes.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import cddl_verifier  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import Validator, validate
from cddl_verifier.cbor import (CBOR, CBORDecodeError, CBORNotDeterministicError, CBORTag, decode,
                                decode_sequence, encode)
from cddl_verifier.cli import main


def hexb(text):
    return bytes.fromhex(text.replace(" ", ""))


def strict(text):
    return decode(hexb(text), require_deterministic=True)


def refused(text):
    with unittest.TestCase().assertRaises(CBORNotDeterministicError) as raised:
        strict(text)
    return raised.exception


# (input, offset of the first violation)
NOT_DETERMINISTIC = [
    # integers and heads
    ("18 05", 0), ("19 00 ff", 0), ("1a 00 00 ff ff", 0), ("1b 00 00 00 00 ff ff ff ff", 0),
    ("38 00", 0), ("39 00 18", 0),
    ("58 00", 0), ("59 00 01 41", 0), ("78 01 61", 0),             # string lengths
    ("98 00", 0), ("99 00 01 01", 0), ("b8 00", 0),               # array and map lengths
    ("d8 01 00", 0), ("d9 00 01 00", 0), ("da 00 00 00 01 00", 0),  # tag numbers
    # indefinite lengths
    ("5f 41 61 ff", 0), ("7f 61 61 ff", 0), ("9f 01 ff", 0), ("bf 01 02 ff", 0),
    # floats
    ("fb 3f f8 00 00 00 00 00 00", 0), ("fa 3f c0 00 00", 0), ("fa 7f 80 00 00", 0),
    ("fb 47 ef ff ff e0 00 00 00", 0), ("fa 80 00 00 00", 0),
    ("f9 7e 01", 0), ("fa 7f c0 00 00", 0), ("fb 7f f8 00 00 00 00 00 00", 0),
    # bignums
    ("c2 41 00", 0), ("c2 42 00 01", 0), ("c2 40", 0), ("c3 40", 0),
    ("c2 48 01 00 00 00 00 00 00 00", 0), ("c3 48 ff ff ff ff ff ff ff ff", 0),
    # map keys out of order
    ("a2 02 00 01 00", 3), ("a2 61 62 00 61 61 00", 4), ("a2 41 02 00 41 01 00", 4),
    ("a2 18 18 00 17 00", 4),     # a longer key sorts after a shorter one
    # nested: the violation is reported where it is
    ("82 01 18 02", 2), ("a1 01 82 02 1801", 4), ("c1 18 05", 1),
]

DETERMINISTIC = [
    "00", "17", "18 18", "18 ff", "19 01 00", "1a 00 01 00 00", "1b 00 00 00 01 00 00 00 00",
    "20", "38 18", "40", "41 00", "60", "61 61", "80", "a0", "81 00", "a1 00 00",
    "d8 18 40", "c1 1a 51 4b 67 b0",
    "f4", "f5", "f6", "f7", "f0", "f8 20",
    "f9 3e 00", "f9 00 00", "f9 80 00", "f9 7c 00", "f9 fc 00", "f9 7e 00",
    "fa 47 c3 50 00", "fa 7f 7f ff ff", "fb 3f f1 99 99 99 99 99 9a", "fb 7e 37 e4 3c 88 00 75 9c",
    "c2 49 01 00 00 00 00 00 00 00 00", "c3 49 01 00 00 00 00 00 00 00 00",
    "c2 49 ff ff ff ff ff ff ff ff ff",
    "a2 01 00 02 00", "a2 0a 00 18 18 00", "a2 61 61 00 61 62 00", "a2 18 64 00 61 61 00",
    "a3 00 00 20 00 40 00", "a2 f4 00 f5 00",
]


class TestDecoder(unittest.TestCase):
    def test_not_deterministic_inputs_are_refused_at_the_first_violation(self):
        for text, offset in NOT_DETERMINISTIC:
            with self.subTest(text):
                error = refused(text)
                self.assertEqual(error.offset, offset)
                self.assertTrue(error.reason.startswith("Not deterministically encoded: "))
                self.assertIn(f"(at offset {offset})", str(error))

    def test_the_same_inputs_decode_without_the_flag(self):
        for text, _ in NOT_DETERMINISTIC:
            with self.subTest(text):
                decode(hexb(text))

    def test_deterministic_inputs_are_accepted(self):
        for text in DETERMINISTIC:
            with self.subTest(text):
                value = strict(text)
                self.assertEqual(encode(value, canonical=True), hexb(text))

    def test_the_error_is_a_decode_error(self):
        error = refused("18 05")
        self.assertIsInstance(error, CBORDecodeError)
        self.assertIsInstance(error, ValueError)
        self.assertIn("shortest form", error.reason)

    def test_malformed_input_is_still_reported_as_malformed(self):
        for text in ("1c", "f8 10", "ff", "18", "a1 00", "61 ff"):
            with self.subTest(text), self.assertRaises(CBORDecodeError) as raised:
                strict(text)
            self.assertNotIsInstance(raised.exception, CBORNotDeterministicError)

    def test_trailing_data_is_still_rejected(self):
        with self.assertRaises(CBORDecodeError):
            strict("00 00")

    def test_every_rule_names_itself(self):
        reasons = {
            "18 05": "shortest form", "5f 41 61 ff": "indefinite-length byte string",
            "7f 61 61 ff": "indefinite-length text string", "9f ff": "indefinite-length array",
            "bf ff": "indefinite-length map", "fb 3f f8 00 00 00 00 00 00": "fits in a float16",
            "fb 47 ef ff ff e0 00 00 00": "fits in a float32", "f9 7e 01": "quiet NaN",
            "c2 41 00": "leading zero", "c2 40": "fits in 64 bits", "a2 02 00 01 00": "bytewise order",
        }
        for text, words in reasons.items():
            with self.subTest(text):
                self.assertIn(words, refused(text).reason)

    def test_keep_encoding_works_with_the_flag(self):
        self.assertEqual(decode(hexb("a1 01 82 02 03"), keep_encoding=True, require_deterministic=True),
                         {1: [2, 3]})

    def test_a_decoded_sequence_is_checked_item_by_item(self):
        self.assertEqual(decode_sequence(hexb("01 02 61 61"), require_deterministic=True), [1, 2, "a"])
        with self.assertRaises(CBORNotDeterministicError) as raised:
            decode_sequence(hexb("01 18 02"), require_deterministic=True)
        self.assertEqual(raised.exception.offset, 1)
        self.assertIn("item 1 of the sequence", raised.exception.reason)

    def test_load_and_loads(self):
        data = hexb("18 05")
        self.assertEqual(CBOR.loads(data), 5)
        with self.assertRaises(CBORNotDeterministicError):
            CBOR.loads(data, require_deterministic=True)
        with self.assertRaises(CBORNotDeterministicError):
            CBOR.load(data, require_deterministic=True)

    def test_off_by_default(self):
        for text, _ in NOT_DETERMINISTIC:
            decode(hexb(text))


class TestWhatCanonicalEncodingWrites(unittest.TestCase):
    VALUES = [
        0, 23, 24, 255, 256, 65535, 65536, 2 ** 32 - 1, 2 ** 32, 2 ** 64 - 1, 2 ** 64, -2 ** 64, -2 ** 64 - 1,
        -1, -24, -25, -256, -257, 1.5, -0.0, 0.0, 65504.0, 65505.0, 1e300, 3.4028234663852886e38, 1.1,
        float("inf"), float("-inf"), float("nan"), "", "a" * 24, "a" * 256, b"", b"x" * 300,
        [], [1, [2, [3]]], {}, {"b": 1, "a": 2, 10: 3, 1: 4, b"k": 5, None: 6, "": 7},
        {i: i for i in range(30)}, CBORTag(1000, 5), CBORTag(2 ** 40, [1.5]),
    ]

    def test_canonical_output_is_accepted_and_stable(self):
        for value in self.VALUES:
            with self.subTest(repr(value)):
                canonical = encode(value, canonical=True)
                again = encode(decode(canonical, require_deterministic=True), canonical=True)
                self.assertEqual(again, canonical)

    def test_accepted_bytes_are_the_canonical_encoding_of_what_they_decode_to(self):
        for text in DETERMINISTIC:
            with self.subTest(text):
                data = hexb(text)
                self.assertEqual(encode(decode(data, require_deterministic=True), canonical=True), data)


class TestValidateFlag(unittest.TestCase):
    SCHEMA = "r = { 1: uint, 2: [* uint] }\n"

    def test_valid_and_deterministic(self):
        result = validate(self.SCHEMA, hexb("a2 01 05 02 82 01 02"), "r", require_deterministic=True)
        self.assertTrue(result.valid)

    def test_not_deterministic_is_a_decode_diagnostic(self):
        data = hexb("a2 01 18 05 02 82 01 02")
        self.assertTrue(validate(self.SCHEMA, data, "r").valid)  # fine without the flag
        result = validate(self.SCHEMA, data, "r", require_deterministic=True)
        self.assertFalse(result.valid)
        (diagnostic,) = result.diagnostics
        self.assertEqual((diagnostic.code, diagnostic.category, diagnostic.offset), ("decode", "decode", 2))
        self.assertIn("shortest form", diagnostic.message)

    def test_keys_out_of_order(self):
        result = validate(self.SCHEMA, hexb("a2 02 82 01 02 01 05"), "r", require_deterministic=True)
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].offset, 5)

    def test_the_validator_method(self):
        validator = Validator(self.SCHEMA)
        self.assertFalse(validator.validate(hexb("a2 01 18 05 02 80"), "r", require_deterministic=True).valid)
        self.assertTrue(validator.validate(hexb("a2 01 05 02 80"), "r", require_deterministic=True).valid)

    def test_a_decoded_value_has_no_encoding_to_check(self):
        with self.assertRaises(TypeError):
            validate(self.SCHEMA, {1: 5, 2: []}, "r", require_deterministic=True)

    def test_a_schema_failure_is_still_reported_for_deterministic_input(self):
        result = validate(self.SCHEMA, hexb("a2 01 61 78 02 80"), "r", require_deterministic=True)
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].code, "validation")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.schema = self.root / "s.cddl"
        self.schema.write_text("doc = { 1: [* uint], name: tstr }\n")

    def run_cli(self, data, *flags):
        path = self.root / "d.cbor"
        path.write_bytes(data)
        out, err, status = io.StringIO(), io.StringIO(), 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                main([str(self.schema), str(path), "--type", "doc", *flags])
            except SystemExit as e:
                status = e.code or 0
        return status, out.getvalue(), err.getvalue()

    GOOD = hexb("a2 01 82 01 02 64 6e 61 6d 65 61 78")
    WIDE = hexb("a2 01 82 18 01 02 64 6e 61 6d 65 61 78")  # the 1 is written in two bytes

    def test_accepts_deterministic_input(self):
        status, _, err = self.run_cli(self.GOOD, "--require-deterministic")
        self.assertEqual(status, 0)
        self.assertIn("Validation successful", err)

    def test_refuses_the_other_input_with_the_offset(self):
        status, out, err = self.run_cli(self.WIDE, "--require-deterministic")
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("Error decoding CBOR: Not deterministically encoded", err)
        self.assertIn("(at offset 3)", err)

    def test_without_the_flag_nothing_changes(self):
        self.assertEqual(self.run_cli(self.WIDE)[0], 0)

    def test_json_diagnostics(self):
        status, out, _ = self.run_cli(self.WIDE, "--require-deterministic", "--diagnostics", "json")
        self.assertEqual(status, 1)
        document = json.loads(out)
        (diagnostic,) = document["diagnostics"]
        self.assertEqual((diagnostic["code"], diagnostic["offset"]), ("decode", 3))
        self.assertFalse(document["valid"])


if __name__ == "__main__":
    unittest.main()
