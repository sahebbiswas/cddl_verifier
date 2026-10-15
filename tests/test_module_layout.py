"""The private modules split out of ``_analyzer`` (#83) import cleanly and keep the old names."""

import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
MODULES = sorted(p.stem for p in (SRC / "cddl_verifier").glob("*.py") if p.stem != "__init__")


class Layout(unittest.TestCase):
    def test_every_module_imports_first_in_a_fresh_interpreter(self):
        """An import cycle shows only for the module that happens to be imported first."""
        for name in MODULES:
            if name == "__main__":
                continue
            with self.subTest(module=name):
                done = subprocess.run(
                    [sys.executable, "-I", "-c", f"import cddl_verifier.{name}"],
                    env={"PYTHONPATH": str(SRC)}, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_cli_names_are_still_importable_from_the_analyzer(self):
        from cddl_verifier import _analyzer, _cli
        for name in ("main", "load_cddl", "load_cbor", "_run_cli", "_exit_json", "_schema_problems"):
            self.assertIs(getattr(_analyzer, name), getattr(_cli, name), name)
        with self.assertRaises(AttributeError):
            _analyzer.no_such_name

    def test_the_logging_names_are_the_same_objects(self):
        from cddl_verifier import _analyzer, _log
        self.assertIs(_analyzer.logger, _log.logger)
        self.assertIs(_analyzer.Colors, _log.Colors)

    def test_the_analyzer_has_no_command_line_code(self):
        text = (SRC / "cddl_verifier" / "_analyzer.py").read_text(encoding="utf-8")
        for needle in ("argparse", "def _run_cli", "def main", "sys.exit"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
