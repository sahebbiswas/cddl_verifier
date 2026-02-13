#!/usr/bin/env python3
"""
Unit Tests for simple_cbor module

Comprehensive tests for CBOR encoding, decoding, and diagnostic dumping.
"""

import sys
import unittest
import struct
from simple_cbor import (
    SimpleCBOREncoder,
    SimpleCBORDecoder,
    cbor_encode,
    cbor_decode,
    cbor_diag_dump,
    CBORDiagnosticDumper
)


class TestCBOREncoding(unittest.TestCase):
    """Test CBOR encoding functionality"""
    
    def test_encode_small_uint(self):
        """Test encoding small unsigned integers (0-23)"""
        encoder = SimpleCBOREncoder()
        
        # 0-23 encoded in single byte
        self.assertEqual(encoder.encode(0), b'\x00')
        self.assertEqual(encoder.encode(1), b'\x01')
        self.assertEqual(encoder.encode(23), b'\x17')
    
    def test_encode_uint8(self):
        """Test encoding uint8 (24-255)"""
        encoder = SimpleCBOREncoder()
        
        # 24 requires additional byte
        self.assertEqual(encoder.encode(24), b'\x18\x18')
        self.assertEqual(encoder.encode(100), b'\x18\x64')
        self.assertEqual(encoder.encode(255), b'\x18\xff')
    
    def test_encode_uint16(self):
        """Test encoding uint16"""
        encoder = SimpleCBOREncoder()
        
        self.assertEqual(encoder.encode(256), b'\x19\x01\x00')
        self.assertEqual(encoder.encode(1000), b'\x19\x03\xe8')
        self.assertEqual(encoder.encode(65535), b'\x19\xff\xff')
    
    def test_encode_uint32(self):
        """Test encoding uint32"""
        encoder = SimpleCBOREncoder()
        
        self.assertEqual(encoder.encode(65536), b'\x1a\x00\x01\x00\x00')
        self.assertEqual(encoder.encode(1000000), b'\x1a\x00\x0f\x42\x40')
    
    def test_encode_negative_int(self):
        """Test encoding negative integers"""
        encoder = SimpleCBOREncoder()
        
        # -1 is encoded as 0x20
        self.assertEqual(encoder.encode(-1), b'\x20')
        self.assertEqual(encoder.encode(-10), b'\x29')
        self.assertEqual(encoder.encode(-100), b'\x38\x63')
        self.assertEqual(encoder.encode(-1000), b'\x39\x03\xe7')
    
    def test_encode_bytes(self):
        """Test encoding byte strings"""
        encoder = SimpleCBOREncoder()
        
        # Empty bytes
        self.assertEqual(encoder.encode(b''), b'\x40')
        
        # Short bytes
        self.assertEqual(encoder.encode(b'\x01\x02\x03'), b'\x43\x01\x02\x03')
        
        # 16 bytes
        data = b'\x00' * 16
        expected = b'\x50' + data
        self.assertEqual(encoder.encode(data), expected)
    
    def test_encode_text(self):
        """Test encoding text strings"""
        encoder = SimpleCBOREncoder()
        
        # Empty string
        self.assertEqual(encoder.encode(''), b'\x60')
        
        # Short string
        self.assertEqual(encoder.encode('a'), b'\x61a')
        self.assertEqual(encoder.encode('hello'), b'\x65hello')
        
        # Unicode
        result = encoder.encode('🎉')
        self.assertTrue(result.startswith(b'\x64'))  # Length 4 (UTF-8 encoding)
    
    def test_encode_array(self):
        """Test encoding arrays"""
        encoder = SimpleCBOREncoder()
        
        # Empty array
        self.assertEqual(encoder.encode([]), b'\x80')
        
        # Single element
        self.assertEqual(encoder.encode([1]), b'\x81\x01')
        
        # Multiple elements
        self.assertEqual(encoder.encode([1, 2, 3]), b'\x83\x01\x02\x03')
        
        # Nested array
        result = encoder.encode([1, [2, 3]])
        self.assertEqual(result, b'\x82\x01\x82\x02\x03')
    
    def test_encode_map(self):
        """Test encoding maps"""
        encoder = SimpleCBOREncoder()
        
        # Empty map
        self.assertEqual(encoder.encode({}), b'\xa0')
        
        # Single entry
        result = encoder.encode({0: 1})
        self.assertEqual(result, b'\xa1\x00\x01')
        
        # Multiple entries (order matters in Python 3.7+)
        result = encoder.encode({0: "a", 1: "b"})
        self.assertEqual(result, b'\xa2\x00\x61a\x01\x61b')
    
    def test_encode_tagged_value(self):
        """Test encoding tagged values"""
        encoder = SimpleCBOREncoder()
        
        # Tag 32 (URI)
        result = encoder.encode((32, "http://example.com"))
        self.assertTrue(result.startswith(b'\xd8\x20'))  # Tag 32
        
        # Tag 501 (CoRIM)
        result = encoder.encode((501, {0: "test"}))
        self.assertTrue(result.startswith(b'\xd9\x01\xf5'))  # Tag 501
    
    def test_encode_bool(self):
        """Test encoding booleans"""
        encoder = SimpleCBOREncoder()
        
        self.assertEqual(encoder.encode(False), b'\xf4')
        self.assertEqual(encoder.encode(True), b'\xf5')
    
    def test_encode_null(self):
        """Test encoding null"""
        encoder = SimpleCBOREncoder()
        
        self.assertEqual(encoder.encode(None), b'\xf6')
    
    def test_encode_float(self):
        """Test encoding floats"""
        encoder = SimpleCBOREncoder()
        
        # Float64
        result = encoder.encode(3.14)
        self.assertEqual(len(result), 9)  # 1 byte header + 8 bytes value
        self.assertEqual(result[0], 0xfb)


class TestCBORDecoding(unittest.TestCase):
    """Test CBOR decoding functionality"""
    
    def test_decode_small_uint(self):
        """Test decoding small unsigned integers"""
        self.assertEqual(cbor_decode(b'\x00'), 0)
        self.assertEqual(cbor_decode(b'\x01'), 1)
        self.assertEqual(cbor_decode(b'\x17'), 23)
    
    def test_decode_uint8(self):
        """Test decoding uint8"""
        self.assertEqual(cbor_decode(b'\x18\x18'), 24)
        self.assertEqual(cbor_decode(b'\x18\x64'), 100)
        self.assertEqual(cbor_decode(b'\x18\xff'), 255)
    
    def test_decode_uint16(self):
        """Test decoding uint16"""
        self.assertEqual(cbor_decode(b'\x19\x01\x00'), 256)
        self.assertEqual(cbor_decode(b'\x19\x03\xe8'), 1000)
    
    def test_decode_negative_int(self):
        """Test decoding negative integers"""
        self.assertEqual(cbor_decode(b'\x20'), -1)
        self.assertEqual(cbor_decode(b'\x29'), -10)
        self.assertEqual(cbor_decode(b'\x38\x63'), -100)
    
    def test_decode_bytes(self):
        """Test decoding byte strings"""
        self.assertEqual(cbor_decode(b'\x40'), b'')
        self.assertEqual(cbor_decode(b'\x43\x01\x02\x03'), b'\x01\x02\x03')
    
    def test_decode_text(self):
        """Test decoding text strings"""
        self.assertEqual(cbor_decode(b'\x60'), '')
        self.assertEqual(cbor_decode(b'\x61a'), 'a')
        self.assertEqual(cbor_decode(b'\x65hello'), 'hello')
    
    def test_decode_array(self):
        """Test decoding arrays"""
        self.assertEqual(cbor_decode(b'\x80'), [])
        self.assertEqual(cbor_decode(b'\x81\x01'), [1])
        self.assertEqual(cbor_decode(b'\x83\x01\x02\x03'), [1, 2, 3])
        
        # Nested
        self.assertEqual(cbor_decode(b'\x82\x01\x82\x02\x03'), [1, [2, 3]])
    
    def test_decode_map(self):
        """Test decoding maps"""
        self.assertEqual(cbor_decode(b'\xa0'), {})
        self.assertEqual(cbor_decode(b'\xa1\x00\x01'), {0: 1})
        self.assertEqual(cbor_decode(b'\xa2\x00\x61a\x01\x61b'), {0: 'a', 1: 'b'})
    
    def test_decode_tagged_value(self):
        """Test decoding tagged values"""
        # Tag 32 with string
        result = cbor_decode(b'\xd8\x20\x65hello')
        self.assertEqual(result, (32, 'hello'))
        
        # Tag 501 with map
        result = cbor_decode(b'\xd9\x01\xf5\xa1\x00\x01')
        self.assertEqual(result, (501, {0: 1}))
    
    def test_decode_bool(self):
        """Test decoding booleans"""
        self.assertEqual(cbor_decode(b'\xf4'), False)
        self.assertEqual(cbor_decode(b'\xf5'), True)
    
    def test_decode_null(self):
        """Test decoding null"""
        self.assertEqual(cbor_decode(b'\xf6'), None)
    
    def test_decode_float32(self):
        """Test decoding float32"""
        # 3.14 as float32
        data = b'\xfa' + struct.pack('>f', 3.14)
        result = cbor_decode(data)
        self.assertAlmostEqual(result, 3.14, places=5)
    
    def test_decode_float64(self):
        """Test decoding float64"""
        # 3.14159265359 as float64
        data = b'\xfb' + struct.pack('>d', 3.14159265359)
        result = cbor_decode(data)
        self.assertAlmostEqual(result, 3.14159265359, places=10)


class TestRoundTrip(unittest.TestCase):
    """Test encode -> decode round trips"""
    
    def test_roundtrip_integers(self):
        """Test integer round trip"""
        values = [0, 1, 23, 24, 255, 256, 65535, 65536, -1, -10, -100, -1000]
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val, f"Failed for {val}")
    
    def test_roundtrip_strings(self):
        """Test string round trip"""
        values = ['', 'a', 'hello', 'hello world', 'emoji: 🎉', 'unicode: café']
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val, f"Failed for '{val}'")
    
    def test_roundtrip_bytes(self):
        """Test bytes round trip"""
        values = [b'', b'\x00', b'\x01\x02\x03', b'\xff' * 10, bytes(range(256))]
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val)
    
    def test_roundtrip_arrays(self):
        """Test array round trip"""
        values = [
            [],
            [1],
            [1, 2, 3],
            [1, "two", 3],
            [[1, 2], [3, 4]],
            [1, [2, [3, [4]]]]
        ]
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val)
    
    def test_roundtrip_maps(self):
        """Test map round trip"""
        values = [
            {},
            {0: 1},
            {0: "a", 1: "b"},
            {0: {1: 2}},
            {"a": 1, "b": 2},  # String keys
        ]
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val)
    
    def test_roundtrip_complex(self):
        """Test complex nested structures"""
        data = {
            0: "name",
            1: [1, 2, 3],
            2: {
                0: True,
                1: False,
                2: None
            },
            3: (32, "http://example.com")  # Tagged value
        }
        
        encoded = cbor_encode(data)
        decoded = cbor_decode(encoded)
        self.assertEqual(decoded, data)


class TestDiagnosticDump(unittest.TestCase):
    """Test CBOR diagnostic dump functionality"""
    
    def test_dump_small_uint(self):
        """Test diagnostic dump of small uint"""
        data = cbor_encode(42)
        dump = cbor_diag_dump(data)
        
        # Should show offset, hex, and type
        self.assertIn('0000:', dump)
        self.assertIn('2a', dump)
        self.assertIn('uint(42)', dump)
    
    def test_dump_negative_int(self):
        """Test diagnostic dump of negative int"""
        data = cbor_encode(-100)
        dump = cbor_diag_dump(data)
        
        self.assertIn('nint(-100)', dump)
    
    def test_dump_text_string(self):
        """Test diagnostic dump of text string"""
        data = cbor_encode("hello")
        dump = cbor_diag_dump(data)
        
        self.assertIn('text(5)', dump)
        self.assertIn('"hello"', dump)
    
    def test_dump_byte_string(self):
        """Test diagnostic dump of byte string"""
        data = cbor_encode(b'\x01\x02\x03')
        dump = cbor_diag_dump(data)
        
        self.assertIn('bytes(3)', dump)
        self.assertIn('010203', dump)
    
    def test_dump_array(self):
        """Test diagnostic dump of array"""
        data = cbor_encode([1, 2, 3])
        dump = cbor_diag_dump(data)
        
        self.assertIn('array(3)', dump)
        self.assertIn('[0]', dump)
        self.assertIn('[1]', dump)
        self.assertIn('[2]', dump)
    
    def test_dump_map(self):
        """Test diagnostic dump of map"""
        data = cbor_encode({0: "a", 1: "b"})
        dump = cbor_diag_dump(data)
        
        self.assertIn('map(2)', dump)
        self.assertIn('key:', dump)
        self.assertIn('val:', dump)
    
    def test_dump_tagged_value(self):
        """Test diagnostic dump of tagged value"""
        data = cbor_encode((32, "http://example.com"))
        dump = cbor_diag_dump(data)
        
        self.assertIn('tag(32)', dump)
        self.assertIn('http://example.com', dump)
    
    def test_dump_bool_null(self):
        """Test diagnostic dump of bool and null"""
        # True
        data = cbor_encode(True)
        dump = cbor_diag_dump(data)
        self.assertIn('true', dump)
        
        # False
        data = cbor_encode(False)
        dump = cbor_diag_dump(data)
        self.assertIn('false', dump)
        
        # Null
        data = cbor_encode(None)
        dump = cbor_diag_dump(data)
        self.assertIn('null', dump)
    
    def test_dump_nested_structure(self):
        """Test diagnostic dump of complex nested structure"""
        data = cbor_encode({
            0: "test",
            1: [1, 2, 3],
            2: {0: True}
        })
        dump = cbor_diag_dump(data)
        
        # Check it has multiple levels
        lines = dump.split('\n')
        self.assertGreater(len(lines), 5)
        
        # Check indentation
        self.assertTrue(any('  ' in line for line in lines))
    
    def test_dump_format(self):
        """Test diagnostic dump format structure"""
        data = cbor_encode({0: 42})
        dump = cbor_diag_dump(data)
        
        lines = dump.split('\n')
        
        for line in lines:
            # Each line should have offset, hex, and comment
            self.assertIn(':', line)
            self.assertIn('#', line)
    
    def test_dump_long_bytes(self):
        """Test diagnostic dump handles long byte strings"""
        # Create a 100-byte string
        data = cbor_encode(b'\x00' * 100)
        dump = cbor_diag_dump(data)
        
        # Should show it's 100 bytes
        self.assertIn('bytes(100)', dump)
        
        # Should indicate truncation for long data
        self.assertTrue('...' in dump or len(dump) < 1000)
    
    def test_dump_custom_indent(self):
        """Test diagnostic dump with custom indentation"""
        data = cbor_encode([1, [2, 3]])
        
        # Default indent
        dump1 = cbor_diag_dump(data, indent="  ")
        
        # Custom indent
        dump2 = cbor_diag_dump(data, indent="    ")
        
        # Different indents should produce different output
        self.assertNotEqual(dump1, dump2)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling"""
    
    def test_empty_data(self):
        """Test handling of empty CBOR data"""
        with self.assertRaises(ValueError):
            cbor_decode(b'')
    
    def test_truncated_data(self):
        """Test handling of truncated data"""
        # Uint16 header without data
        with self.assertRaises(Exception):
            cbor_decode(b'\x19')  # Missing 2 bytes
        
        # Text string header without data
        with self.assertRaises(Exception):
            cbor_decode(b'\x65')  # Says 5 bytes but none follow
    
    def test_large_integers(self):
        """Test very large integers"""
        # Large positive
        val = 2**63 - 1
        encoded = cbor_encode(val)
        decoded = cbor_decode(encoded)
        self.assertEqual(decoded, val)
        
        # Large negative
        val = -(2**63)
        encoded = cbor_encode(val)
        decoded = cbor_decode(encoded)
        self.assertEqual(decoded, val)
    
    def test_unicode_edge_cases(self):
        """Test Unicode edge cases"""
        # Various Unicode ranges
        values = [
            '𝕳𝖊𝖑𝖑𝖔',  # Mathematical alphanumeric symbols
            '你好世界',  # Chinese
            '🎉🎊🎈',  # Emojis
            'Ω≈ç√∫',  # Math symbols
        ]
        
        for val in values:
            encoded = cbor_encode(val)
            decoded = cbor_decode(encoded)
            self.assertEqual(decoded, val)
    
    def test_empty_containers(self):
        """Test empty arrays and maps"""
        # Empty array
        self.assertEqual(cbor_encode([]), b'\x80')
        self.assertEqual(cbor_decode(b'\x80'), [])
        
        # Empty map
        self.assertEqual(cbor_encode({}), b'\xa0')
        self.assertEqual(cbor_decode(b'\xa0'), {})
    
    def test_mixed_type_array(self):
        """Test arrays with mixed types"""
        data = [1, "two", 3.0, True, None, b'\x00', {0: 1}]
        encoded = cbor_encode(data)
        decoded = cbor_decode(encoded)
        
        # Compare element by element (float comparison needs tolerance)
        self.assertEqual(len(decoded), len(data))
        self.assertEqual(decoded[0], 1)
        self.assertEqual(decoded[1], "two")
        self.assertAlmostEqual(decoded[2], 3.0)
        self.assertEqual(decoded[3], True)
        self.assertEqual(decoded[4], None)
        self.assertEqual(decoded[5], b'\x00')
        self.assertEqual(decoded[6], {0: 1})


def run_tests():
    """Run all tests and return results"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestCBOREncoding))
    suite.addTests(loader.loadTestsFromTestCase(TestCBORDecoding))
    suite.addTests(loader.loadTestsFromTestCase(TestRoundTrip))
    suite.addTests(loader.loadTestsFromTestCase(TestDiagnosticDump))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    print("=" * 70)
    print("Simple CBOR Module - Comprehensive Unit Tests")
    print("=" * 70)
    print()
    
    result = run_tests()
    
    print()
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 70)
    
    sys.exit(0 if result.wasSuccessful() else 1)
