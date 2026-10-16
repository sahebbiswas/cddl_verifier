"""Typed JSON annotations are read strictly (#79).

``{"$cbor": ...}`` is the one reserved key. An object that has it is an annotation and
must be well formed; anything else is an error naming the place, not a quiet map.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cddl_verifier.cbor import CBORTag, cbor_decode, cbor_encode
from cddl_verifier.json_codec import cbor_to_json, json_file_to_cbor_file, json_to_cbor, main


def reject(text, *fragments, **kwargs):
    try:
        json_to_cbor(text, **kwargs)
    except ValueError as exc:
        for fragment in fragments:
            assert fragment in str(exc), (fragment, str(exc))
        return str(exc)
    raise AssertionError(f"{text} was accepted")


class Base64(unittest.TestCase):
    def test_the_form_cbor_to_json_writes_is_read_back(self):
        for raw in (b"", b"\x00", b"ab", b"abc", bytes(range(256))):
            typed = cbor_to_json(cbor_encode(raw), typed=True)
            self.assertEqual(cbor_decode(json_to_cbor(typed)), raw)

    def test_bad_base64_is_rejected_with_the_place(self):
        for bad, why in [("AQ", "padding"), ("A Q I D", "base64"), ("AQ==\n", "base64"),
                         ("AQ=", "padding"), ("AQ-_", "base64"), ("é", "ASCII"),
                         ("AR==", "canonical"), ("AQI=\u0000", "base64")]:
            with self.subTest(bad=bad):
                reject(json.dumps({"k": [{"$cbor": "bytes", "$value": bad}]}),
                       "$.k[0].$value", why)

    def test_a_value_that_is_not_a_string(self):
        for bad in (5, None, ["AQ=="], {"a": 1}, True):
            with self.subTest(bad=bad):
                reject(json.dumps({"$cbor": "bytes", "$value": bad}), "base64 string")


class Structure(unittest.TestCase):
    def test_unknown_or_mistyped_type_is_an_error_not_a_map(self):
        for kind in ("byte", "Bytes", "", 5, None, ["bytes"], True):
            with self.subTest(kind=kind):
                reject(json.dumps({"$cbor": kind, "$value": "AQ=="}), "unknown $cbor type", "$")

    def test_missing_and_extra_keys(self):
        reject('{"$cbor": "bytes"}', "needs $value")
        reject('{"$cbor": "tag", "$value": 1}', "needs $tag")
        reject('{"$cbor": "tag", "$tag": 1}', "needs $value")
        reject('{"$cbor": "map"}', "needs $value")
        reject('{"$cbor": "simple"}', "needs $value")
        reject('{"$cbor": "bytes", "$value": "AQ==", "note": 1}', "no other key", "note")
        reject('{"$cbor": "tag", "$tag": 1, "$value": 1, "$x": 2}', "no other key")
        for kind in ("undefined", "NaN", "Infinity", "-Infinity"):
            reject(json.dumps({"$cbor": kind, "$value": 1}), "no other key", kind)

    def test_the_place_follows_nesting(self):
        reject('{"a": [1, {"b": {"$cbor": "bytes"}}]}', "$.a[1].b")
        reject('{"a b": {"$cbor": "nope"}}', '$["a b"]')
        reject('{"$cbor": "tag", "$tag": 1, "$value": {"$cbor": "bytes", "$value": "x"}}',
               "$.$value.$value")

    def test_tag_numbers(self):
        for bad in (1.5, -1, 2 ** 64, True, "1", None):
            with self.subTest(tag=bad):
                reject(json.dumps({"$cbor": "tag", "$tag": bad, "$value": 1}), "$tag", "$.$tag")
        self.assertEqual(cbor_decode(json_to_cbor('{"$cbor":"tag","$tag":18446744073709551615,'
                                                  '"$value":0}')),
                         CBORTag(2 ** 64 - 1, 0))

    def test_simple_values(self):
        for bad in (20, 24, 256, -1, 1.5, "16", True, None):
            with self.subTest(simple=bad):
                reject(json.dumps({"$cbor": "simple", "$value": bad}), "simple", "$.$value")

    def test_map_entries(self):
        reject('{"$cbor": "map", "$value": {"a": 1}}', "list of [key, value] pairs")
        reject('{"$cbor": "map", "$value": [[1]]}', "[key, value] pair", "$.$value[0]")
        reject('{"$cbor": "map", "$value": [[1, 2], [1, 3]]}', "Duplicate map key", "[1]")

    def test_valid_annotations_still_work(self):
        text = json.dumps({"b": {"$cbor": "bytes", "$value": "AQID"},
                           "t": {"$cbor": "tag", "$tag": 1, "$value": {"$cbor": "bytes", "$value": ""}},
                           "m": {"$cbor": "map", "$value": [[1, "a"], ["1", "b"]]},
                           "s": {"$cbor": "simple", "$value": 16}})
        value = cbor_decode(json_to_cbor(text))
        self.assertEqual(value["b"], b"\x01\x02\x03")
        self.assertEqual(value["t"], CBORTag(1, b""))
        self.assertEqual(value["m"], {1: "a", "1": "b"})


class ReservedKeys(unittest.TestCase):
    """What an ordinary object with a reserved-looking key means."""

    def test_only_cbor_is_reserved(self):
        value = cbor_decode(json_to_cbor('{"$tag": 5, "$value": "x", "$other": [1]}'))
        self.assertEqual(value, {"$tag": 5, "$value": "x", "$other": [1]})

    def test_an_ordinary_cbor_key_is_written_as_a_map_and_read_back(self):
        original = {"$cbor": "hello", "n": 1}
        typed = cbor_to_json(cbor_encode(original), typed=True)
        self.assertEqual(json.loads(typed)["$cbor"], "map")
        self.assertEqual(cbor_decode(json_to_cbor(typed)), original)

    def test_plain_mode_reads_the_json_as_data(self):
        text = '{"$cbor": "bytes", "$value": "AQ==", "$tag": 1}'
        self.assertEqual(cbor_decode(json_to_cbor(text, annotations=False)),
                         {"$cbor": "bytes", "$value": "AQ==", "$tag": 1})
        # nested, and not read even when malformed
        self.assertEqual(cbor_decode(json_to_cbor('[{"$cbor": 5}]', annotations=False)),
                         [{"$cbor": 5}])

    def test_plain_mode_cannot_mint_tags_or_bytes(self):
        value = cbor_decode(json_to_cbor('{"$cbor": "tag", "$tag": 24, "$value": 1}',
                                         annotations=False))
        self.assertIsInstance(value, dict)


class Hostile(unittest.TestCase):
    def test_deep_nesting_is_a_value_error(self):
        reject("[" * 5000 + "]" * 5000, "nested too deeply")
        reject('{"a":' * 5000 + "1" + "}" * 5000, "nested too deeply")
        deep = '{"$cbor":"tag","$tag":1,"$value":' * 2000 + "1" + "}" * 2000
        reject(deep, "nested too deeply")

    def test_duplicate_object_keys_are_still_rejected(self):
        reject('{"a": 1, "a": 2}', "Duplicate JSON object key")
        reject('{"$cbor": "bytes", "$cbor": "map"}', "Duplicate JSON object key")


class Command(unittest.TestCase):
    def run_cli(self, text, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "in.json"), Path(tmp, "out.cbor")
            source.write_text(text)
            err = io.StringIO()
            code = 0
            with redirect_stderr(err):
                try:
                    main(["to-cbor", str(source), str(target), *flags])
                except SystemExit as exc:
                    code = exc.code
            return code, err.getvalue(), target.read_bytes() if target.exists() else None

    def test_a_malformed_annotation_exits_1_naming_the_place(self):
        code, err, out = self.run_cli('{"a": {"$cbor": "byte"}}')
        self.assertEqual(code, 1)
        self.assertIn("$.a", err)
        self.assertIsNone(out)

    def test_plain_flag(self):
        code, err, out = self.run_cli('{"$cbor": "byte"}', "--plain")
        self.assertEqual((code, cbor_decode(out)), (0, {"$cbor": "byte"}))

    def test_file_helper_takes_the_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.json").write_text('{"$cbor": 1}')
            json_file_to_cbor_file(str(Path(tmp, "a.json")), str(Path(tmp, "a.cbor")),
                                   annotations=False)
            self.assertEqual(cbor_decode(Path(tmp, "a.cbor").read_bytes()), {"$cbor": 1})
            with self.assertRaises(ValueError):
                json_file_to_cbor_file(str(Path(tmp, "a.json")), str(Path(tmp, "b.cbor")))

    def test_file_helpers_use_utf8_whatever_the_locale(self):
        # Non-ASCII text must survive a round trip where the locale encoding is not UTF-8.
        import os
        import subprocess
        text = "naïve ✓"
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from cddl_verifier.cbor import decode\n"
            "from cddl_verifier.json_codec import cbor_file_to_json_file, json_file_to_cbor_file\n"
            "d = Path(sys.argv[1])\n"
            "cbor_file_to_json_file(str(d / 'a.cbor'), str(d / 'a.json'))\n"
            "json_file_to_cbor_file(str(d / 'a.json'), str(d / 'b.cbor'))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.cbor").write_bytes(cbor_encode({"k": text}))
            env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
            done = subprocess.run([sys.executable, "-c", script, tmp], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(text, Path(tmp, "a.json").read_bytes().decode("utf-8"))
            self.assertEqual(cbor_decode(Path(tmp, "b.cbor").read_bytes()), {"k": text})


if __name__ == "__main__":
    unittest.main()
