#!/usr/bin/env python3
"""Tests for ``.github/scripts/check_version_bump.py``, the script behind the
*Version bumped* CI job (CONTRIBUTING.md, "Versioning").
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "check_version_bump.py"
VERSION_FILE = ROOT / "src" / "cddl_verifier" / "_version.py"
BASE = '__version__ = "0.2.2"\n'


def run_check(old, new):
    """Run the script on two version files with contents *old* and *new*."""
    with tempfile.TemporaryDirectory() as tmp:
        old_file = Path(tmp) / "old.py"
        new_file = Path(tmp) / "new.py"
        old_file.write_text(old, encoding="utf-8")
        new_file.write_text(new, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(old_file), str(new_file)],
            capture_output=True, text=True, check=False)


class TestVersionBumpCheck(unittest.TestCase):

    def test_one_step_bumps_pass(self):
        for new, kind in [("0.2.3", "patch"), ("0.3.0", "minor"), ("1.0.0", "major")]:
            with self.subTest(new=new):
                result = run_check(BASE, f'__version__ = "{new}"\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"({kind} bump)", result.stdout)

    def test_missing_or_oversized_bumps_fail(self):
        for new in ["0.2.2", "0.2.4", "0.3.1", "0.2.1", "2.0.0"]:
            with self.subTest(new=new):
                result = run_check(BASE, f'__version__ = "{new}"\n')
                self.assertEqual(result.returncode, 1)
                self.assertIn("src/cddl_verifier/_version.py", result.stderr)

    def test_malformed_version_files_fail(self):
        for new in ['__version__ = "0.2.3rc1"\n',
                    "__version__ = VERSION\n",
                    '__version__ = "0.2.3"\n__version__ = "0.2.4"\n',
                    '__version__ = "0.2"\n__version__ += ".3"\n',
                    "VERSION = 1\n"]:
            with self.subTest(new=new):
                result = run_check(BASE, new)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)

    def test_reads_the_package_version(self):
        spec = importlib.util.spec_from_file_location("check_version_bump", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        import cddl_verifier

        self.assertEqual(module.read_version(str(VERSION_FILE)), cddl_verifier.__version__)


if __name__ == "__main__":
    unittest.main()
