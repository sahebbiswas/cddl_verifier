"""The private modules split out of ``_analyzer`` (#83) import cleanly and keep the old names."""

import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
MODULES = sorted(p.stem for p in (SRC / "cddl_verifier").glob("*.py") if p.stem != "__init__")
VALIDATOR_MODULES = sorted(p.stem for p in (SRC / "cddl_verifier" / "_validator").glob("*.py") if p.stem != "__init__")


class Layout(unittest.TestCase):
    def test_every_module_imports_first_in_a_fresh_interpreter(self):
        """An import cycle shows only for the module that happens to be imported first."""
        for name in MODULES:
            if name == "__main__":
                continue
            with self.subTest(module=name):
                done = subprocess.run(
                    [sys.executable, "-I", "-c",
                     f"import sys; sys.path.insert(0, {str(SRC)!r}); import cddl_verifier.{name}; "
                     f"import cddl_verifier; assert cddl_verifier.__file__.startswith({str(SRC)!r})"],
                    capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_every_validator_module_imports_first_in_a_fresh_interpreter(self):
        for name in VALIDATOR_MODULES:
            with self.subTest(module=name):
                done = subprocess.run(
                    [sys.executable, "-I", "-c",
                     f"import sys; sys.path.insert(0, {str(SRC)!r}); import cddl_verifier._validator.{name}; "
                     f"import cddl_verifier; assert cddl_verifier.__file__.startswith({str(SRC)!r})"],
                    capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_analyzer_is_composed_of_every_mixin_and_none_shadows_another(self):
        """A method defined in two mixins silently hides one of them."""
        from cddl_verifier._analyzer import CBORAnalyzer
        mixins = [c for c in CBORAnalyzer.__mro__ if c.__module__.startswith("cddl_verifier._validator.")]
        self.assertEqual(len(mixins), len(VALIDATOR_MODULES) - 1)  # all but support
        seen = {}
        for mixin in mixins:
            for name, value in vars(mixin).items():
                if callable(value) and not name.startswith("__"):
                    self.assertNotIn(name, seen, f"{name} is in {seen.get(name)} and {mixin.__name__}")
                    seen[name] = mixin.__name__

    def test_the_cli_names_are_still_importable_from_the_analyzer(self):
        from cddl_verifier import _analyzer, _cli
        for name in ("main", "load_cddl", "load_cbor", "_run_cli", "_exit_json", "_schema_problems"):
            self.assertIs(getattr(_analyzer, name), getattr(_cli, name), name)
        with self.assertRaises(AttributeError):
            _analyzer.no_such_name

    def test_the_edn_generator_and_decoder_are_still_importable_from_the_analyzer(self):
        from cddl_verifier import _analyzer, _decode, _edn
        self.assertIs(_analyzer.EDNGenerator, _edn.EDNGenerator)
        self.assertIs(_analyzer.SimpleCBORDecoder, _decode.SimpleCBORDecoder)

    def test_the_parser_moved_to_schema_and_has_no_module_level_analyzer_import(self):
        import re
        from cddl_verifier import _analyzer, _schema
        self.assertIs(_analyzer.CDDLParser, _schema.CDDLParser)
        text = (SRC / "cddl_verifier" / "_schema.py").read_text(encoding="utf-8")
        top_level = re.findall(r"^(?:from|import) .*_analyzer.*$", text, re.M)
        self.assertEqual(top_level, [], "_schema must reach the analyzer only by a deferred import")
        self.assertIn("from ._analyzer import CBORAnalyzer", text)

    def test_the_logging_names_are_the_same_objects(self):
        from cddl_verifier import _analyzer, _log
        self.assertIs(_analyzer.logger, _log.logger)
        self.assertIs(_analyzer.Colors, _log.Colors)

    def test_the_analyzer_has_no_command_line_code(self):
        text = (SRC / "cddl_verifier" / "_analyzer.py").read_text(encoding="utf-8")
        for needle in ("argparse", "def _run_cli", "def main", "sys.exit", "class EDNGenerator",
                       "class SimpleCBORDecoder", "class CDDLParser", "def _prelude_tables"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
