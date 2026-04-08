#!/usr/bin/env python3
"""
Extra tests for cbor_diag_dump to ensure full coverage and correct formatting.
"""

import unittest
import struct
from simple_cbor import cbor_diag_dump, cbor_encode

class TestCBORDiagDumpExtra(unittest.TestCase):
    def test_dump_empty_bytes(self):
        """Test diagnostic dump of empty bytes."""
        # According to _generate_diag: if not data: return "# Empty CBOR data"
        self.assertEqual(cbor_diag_dump(b""), "# Empty CBOR data")

    def test_dump_truncated_data(self):
        """Test diagnostic dump of truncated data."""
        # Header for uint16 but no data
        data = b'\x19'
        dump = cbor_diag_dump(data)
        self.assertIn("# ERROR: Unexpected end of data", dump)

    def test_dump_invalid_utf8(self):
        """Test diagnostic dump of invalid UTF-8 string."""
        # Major type 3 (text string), length 1, followed by invalid start byte 0xff
        data = b'\x61\xff'
        dump = cbor_diag_dump(data)
        self.assertIn("# Invalid UTF-8: ff", dump)

    def test_dump_long_byte_string(self):
        """Test diagnostic dump of byte string > 32 bytes."""
        data = cbor_encode(b'A' * 40)
        dump = cbor_diag_dump(data)
        self.assertIn("bytes(40)", dump)
        self.assertIn("h'41414141414141414141414141414141'", dump) # 16 bytes
        self.assertIn("...", dump)
        self.assertIn("# ... (8 more bytes) ...", dump)

    def test_dump_long_text_string(self):
        """Test diagnostic dump of text string > 32 characters."""
        text = "This is a long text string that should be wrapped because it exceeds 32 characters."
        data = cbor_encode(text)
        dump = cbor_diag_dump(data)
        self.assertIn("text(83)", dump)
        # Check that it contains parts of the string
        self.assertIn("This is a long text string that ", dump)
        # It wraps at 32 chars.
        # "This is a long text string that " is 32 chars.
        self.assertIn('"This is a long text string that ', dump)

    def test_dump_alignment(self):
        """Test that comments are aligned correctly (min col 40)."""
        # Short item
        data = cbor_encode(1)
        dump = cbor_diag_dump(data)
        # 0000: 01                                 # uint(1)
        # The # should be at some position >= 40
        line = dump.split('\n')[0]
        hash_index = line.find('#')
        self.assertGreaterEqual(hash_index, 40)

    def test_dump_deep_nesting(self):
        """Test dump of deeply nested structures for indentation."""
        data = cbor_encode([[[[1]]]])
        dump = cbor_diag_dump(data, indent=">>")
        self.assertIn("array(1)", dump)
        self.assertIn(">>>>>>>>01", dump) # Indexing and uint(1)
        self.assertIn("uint(1)", dump)

    def test_dump_simple_values_all(self):
        """Test all simple value types."""
        # Simple(24) - not a named one
        data = b'\xf8\x18'
        dump = cbor_diag_dump(data)
        self.assertIn("simple(24)", dump)

        # float16
        data = b'\xf9\x3c\x00' # 1.0 in float16
        dump = cbor_diag_dump(data)
        self.assertIn("float16", dump)

        # float32
        data = b'\xfa\x40\x48\xf5\xc3' # 3.14 in float32
        dump = cbor_diag_dump(data)
        self.assertIn("float32(3.14", dump)

        # float64
        data = b'\xfb\x40\x09\x21\xfb\x54\x44\x2d\x18' # 3.141592653589793
        dump = cbor_diag_dump(data)
        self.assertIn("float64(3.141592653589793)", dump)

if __name__ == '__main__':
    unittest.main()
