"""Members are named by their key by default; a trailing comment names one only with comment_names (#108)."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cddl_verifier import Validator, validate
from cddl_verifier._schema import CDDLParser
from cddl_verifier.cbor import encode
from cddl_verifier.cli import main

SCHEMA = "m = {\n  0: tstr ; name\n  1: uint ; count\n}\n"
BAD = encode({0: "x", 1: "y"})
GOOD = encode({0: "x", 1: 2})
LABELLED = "m = {\n  &(id: 0) => uint ; ignored\n}\n"


def messages(result):
    return [d.message for d in result.diagnostics]


class TestDefault(unittest.TestCase):
    def test_comment_does_not_name_member(self):
        text = " ".join(messages(validate(SCHEMA, BAD)))
        self.assertIn("'1'", text)
        self.assertNotIn("count", text)

    def test_comment_names_member_when_on(self):
        text = " ".join(messages(validate(SCHEMA, BAD, comment_names=True)))
        self.assertIn("'count'", text)

    def test_validator_option(self):
        self.assertNotIn("count", " ".join(messages(Validator(SCHEMA).validate(BAD))))
        on = Validator(SCHEMA, comment_names=True).validate(BAD)
        self.assertIn("count", " ".join(messages(on)))

    def test_validity_is_the_same(self):
        for flag in (False, True):
            self.assertTrue(validate(SCHEMA, GOOD, comment_names=flag).valid)
            self.assertFalse(validate(SCHEMA, BAD, comment_names=flag).valid)

    def test_bareword_key_wins_over_comment_only_when_off(self):
        schema = "m = { a: uint ; note\n}"
        data = encode({"a": "x"})
        self.assertIn("'a'", " ".join(messages(validate(schema, data))))
        self.assertIn("'note'", " ".join(messages(validate(schema, data, comment_names=True))))

    def test_registered_label_unaffected(self):
        data = encode({0: "x"})
        for flag in (False, True):
            text = " ".join(messages(validate(LABELLED, data, comment_names=flag)))
            self.assertIn("'id'", text)
            self.assertNotIn("ignored", text)

    def test_member_without_comment_keeps_key_name(self):
        schema = "m = { 5: uint }"
        for flag in (False, True):
            self.assertIn("'5'", " ".join(messages(validate(schema, encode({5: "x"}), comment_names=flag))))


class TestParser(unittest.TestCase):
    def names(self, **kw):
        return {k: v['name'] for k, v in CDDLParser(SCHEMA, **kw).types['m']['fields'].items()}

    def test_names(self):
        self.assertEqual(self.names(), {0: '0', 1: '1'})
        self.assertEqual(self.names(comment_names=True), {0: 'name', 1: 'count'})


class TestCli(unittest.TestCase):
    def run_cli(self, *flags):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "s.cddl").write_text(SCHEMA, encoding="utf-8")
            (root / "d.cbor").write_bytes(GOOD)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    main([str(root / "s.cddl"), str(root / "d.cbor"), "--type", "m",
                          "--output", str(root / "o.edn"), *flags])
                except SystemExit as e:
                    self.assertFalse(e.code)
            return (root / "o.edn").read_text(encoding="utf-8")

    def test_edn_annotations(self):
        default = self.run_cli()
        self.assertNotIn("count", default)
        self.assertNotIn("/ name /", default)
        opted = self.run_cli("--comment-names")
        self.assertIn("/ name / 0:", opted)
        self.assertIn("/ count / 1:", opted)

    def test_edn_keyname_format(self):
        self.assertIn("count", self.run_cli("--comment-names", "--edn-format", "both"))


if __name__ == "__main__":
    unittest.main()
