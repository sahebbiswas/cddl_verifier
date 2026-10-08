#!/usr/bin/env python3
"""
Regression tests for strict single-item CBOR decoding (issue #64).

``CBOR.loads()`` must consume exactly one complete CBOR data item and reject
empty input, truncation and trailing bytes with a controlled
``CBORDecodeError`` that reports the byte offset of the problem.
"""

import sys
import subprocess
from pathlib import Path

# When run directly, add the repo root to sys.path (pytest uses conftest.py).
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import unittest
from simple_cbor import (
    CBOR,
    CBORDecodeError,
    CBORTrailingDataError,
    CBORTruncatedError,
    CBORUnsupportedError,
    cbor_decode,
    cbor_encode,
    load_cbor_bytes,
)
from _version import __version__


# (description, encoded item, expected decoded value)
VALID_ITEMS = [
    ("uint", b'\x18\x64', 100),
    ("nint", b'\x38\x63', -100),
    ("bstr", b'\x43\x01\x02\x03', b'\x01\x02\x03'),
    ("tstr", b'\x65hello', 'hello'),
    ("true", b'\xf5', True),
    ("null", b'\xf6', None),
    ("float64", b'\xfb\x3f\xf8\x00\x00\x00\x00\x00\x00', 1.5),
    ("array", b'\x83\x01\x02\x03', [1, 2, 3]),
    ("map", b'\xa2\x00\x61a\x01\x61b', {0: 'a', 1: 'b'}),
    ("tag", b'\xd8\x20\x65hello', (32, 'hello')),
    ("nested", cbor_encode({1: [1, {2: b'\x00'}, (24, [True])], "k": {"x": -1}}),
     {1: [1, {2: b'\x00'}, (24, [True])], "k": {"x": -1}}),
]


class TestSingleItemAccepted(unittest.TestCase):
    """Exactly one complete item decodes as before."""

    def test_valid_items(self):
        for name, data, expected in VALID_ITEMS:
            with self.subTest(name):
                self.assertEqual(CBOR.loads(data), expected)
                self.assertEqual(cbor_decode(data), expected)
                self.assertEqual(load_cbor_bytes(data), expected)

    def test_bytes_like_inputs(self):
        self.assertEqual(CBOR.loads(bytearray(b'\x82\x01\x02')), [1, 2])
        self.assertEqual(CBOR.loads(memoryview(b'\x82\x01\x02')), [1, 2])

    def test_load_caches_input(self):
        obj = CBOR.load(b'\x81\x01')
        self.assertEqual(obj.data, [1])
        self.assertEqual(obj._cached_bytes, b'\x81\x01')


class TestTrailingBytesRejected(unittest.TestCase):
    """Any bytes after the item are an error, reported at the first extra byte."""

    def test_trailing_bytes_after_each_item_type(self):
        for name, data, _ in VALID_ITEMS:
            for extra in (b'\x00', b'\xff', b'\x01\x02\x03'):
                with self.subTest(name, extra=extra):
                    with self.assertRaises(CBORTrailingDataError) as cm:
                        CBOR.loads(data + extra)
                    self.assertEqual(cm.exception.offset, len(data))
                    self.assertIn(f"{len(extra)} extra byte", str(cm.exception))

    def test_cbor_sequence_rejected(self):
        # Two concatenated items form a CBOR sequence (RFC 8742), not one item.
        seq = cbor_encode(1) + cbor_encode([2, 3])
        with self.assertRaises(CBORTrailingDataError) as cm:
            cbor_decode(seq)
        self.assertEqual(cm.exception.offset, 1)

    def test_is_value_error(self):
        # Backward compatibility: callers catching ValueError still work.
        with self.assertRaises(ValueError):
            cbor_decode(b'\x01\x02')


class TestEmptyAndTruncatedRejected(unittest.TestCase):

    def test_empty_input(self):
        for empty in (b'', bytearray(), memoryview(b'')):
            with self.subTest(type(empty).__name__):
                with self.assertRaises(CBORTruncatedError) as cm:
                    CBOR.loads(empty)
                self.assertEqual(cm.exception.offset, 0)
                self.assertIn("Empty CBOR data", str(cm.exception))

    def test_truncated_items(self):
        # (description, data, offset where more bytes were needed)
        cases = [
            ("uint16 argument", b'\x19\x01', 1),
            ("uint64 argument", b'\x1b\x00\x00', 1),
            ("bstr payload", b'\x43\x01', 1),
            ("tstr payload", b'\x65hel', 1),
            ("float32 payload", b'\xfa\x3f\x80', 1),
            ("array element", b'\x83\x01\x02', 3),
            ("map value", b'\xa1\x01', 2),
            ("tag content", b'\xd8\x20', 2),
            ("nested deep", b'\xa1\x01\x82\x61a\x43\x00', 6),
        ]
        for name, data, offset in cases:
            with self.subTest(name):
                with self.assertRaises(CBORTruncatedError) as cm:
                    CBOR.loads(data)
                self.assertEqual(cm.exception.offset, offset)
                self.assertIn("Unexpected end of data", cm.exception.reason)

    def test_every_strict_prefix_is_truncated(self):
        data = cbor_encode({1: [1, b'xyz', "str"], 2: (1, {"a": 1.5})})
        for cut in range(1, len(data)):
            with self.subTest(cut=cut):
                with self.assertRaises(CBORTruncatedError):
                    CBOR.loads(data[:cut])

    def test_huge_declared_length(self):
        # bstr claiming 2**63 bytes must fail cleanly, not allocate.
        with self.assertRaises(CBORTruncatedError) as cm:
            CBOR.loads(b'\x5b\x80\x00\x00\x00\x00\x00\x00\x00')
        self.assertEqual(cm.exception.offset, 9)


class TestMalformedRejected(unittest.TestCase):
    """Other malformed input also raises CBORDecodeError with an offset."""

    def test_reserved_additional_info(self):
        with self.assertRaises(CBORDecodeError) as cm:
            CBOR.loads(b'\x81\x1c')
        self.assertEqual(cm.exception.offset, 1)

    def test_invalid_utf8(self):
        with self.assertRaises(CBORDecodeError) as cm:
            CBOR.loads(b'\x82\x00\x63a\xffb')
        self.assertEqual(cm.exception.offset, 4)
        self.assertNotIsInstance(cm.exception, UnicodeDecodeError)

    def test_duplicate_key_offset(self):
        with self.assertRaises(CBORDecodeError) as cm:
            CBOR.loads(b'\xa2\x01\x02\x01\x03')
        self.assertEqual(cm.exception.offset, 3)

    def test_unsupported_is_not_implemented(self):
        for data, offset in ((b'\x9f\x01\xff', 0), (b'\x82\x01\xf7', 2)):
            with self.subTest(data=data):
                with self.assertRaises(CBORUnsupportedError) as cm:
                    CBOR.loads(data)
                self.assertIsInstance(cm.exception, NotImplementedError)
                self.assertEqual(cm.exception.offset, offset)

    def test_deep_nesting_is_controlled(self):
        with self.assertRaises(CBORDecodeError):
            CBOR.loads(b'\x81' * 100_000 + b'\x00')

    def test_non_bytes_rejected(self):
        with self.assertRaises(TypeError):
            CBOR.loads("a1 01 02")


class TestAnalyzerCLI(unittest.TestCase):
    """The analyzer CLI surfaces strict-decoding errors and its version."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "cbor_cddl_analyzer.py"), *args],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )

    def test_trailing_bytes_fail_cli(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cddl = Path(tmp) / "s.cddl"
            cddl.write_text("root = uint\n")
            cbor = Path(tmp) / "d.cbor"
            cbor.write_bytes(b'\x01\x02')
            result = self._run(str(cddl), str(cbor))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Trailing data after CBOR item", result.stderr)
        self.assertIn("offset 1", result.stderr)

    def test_version_flag(self):
        result = self._run("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(__version__, result.stdout)


if __name__ == '__main__':
    unittest.main()
