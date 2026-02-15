#!/usr/bin/env python3
"""
Tests for Canonical Encoding and JSON Conversion

Tests:
- Canonical CBOR encoding (RFC 8949 §4.2)
- JSON to CBOR conversion
- CBOR to JSON conversion
- Type preservation
- Round-trip conversions
"""

import unittest
import json
import math
from simple_cbor import CBOR, cbor_encode, cbor_decode
from cbor_json import (
    cbor_to_json, json_to_cbor, 
    CBORJSONEncoder, _process_cbor_annotations
)


class TestCanonicalEncoding(unittest.TestCase):
    """Test canonical CBOR encoding per RFC 8949 Section 4.2"""
    
    def test_map_key_sorting(self):
        """Test that map keys are sorted in canonical encoding"""
        # Create map with keys that should be sorted
        data = {
            "z": 1,
            "a": 2,
            "m": 3,
        }
        
        # Non-canonical encoding (dict order)
        normal = cbor_encode(data, canonical=False)
        
        # Canonical encoding (sorted by encoded bytes)
        canonical = cbor_encode(data, canonical=True)
        
        # They should be different
        self.assertNotEqual(normal, canonical)
        
        # But decode to same data
        self.assertEqual(cbor_decode(normal), cbor_decode(canonical))
        
        # In canonical form, keys are sorted by encoded representation
        # "a" (0x61 0x61) < "m" (0x61 0x6d) < "z" (0x61 0x7a)
        decoded = cbor_decode(canonical)
        self.assertEqual(decoded, data)
    
    def test_numeric_key_sorting(self):
        """Test canonical sorting with numeric keys"""
        data = {
            10: "ten",
            2: "two",
            100: "hundred",
            1: "one",
        }
        
        canonical = cbor_encode(data, canonical=True)
        decoded = cbor_decode(canonical)
        
        self.assertEqual(decoded, data)
    
    def test_mixed_key_sorting(self):
        """Test canonical sorting with mixed key types"""
        data = {
            10: "number",
            "key": "string",
            2: "small",
        }
        
        canonical = cbor_encode(data, canonical=True)
        decoded = cbor_decode(canonical)
        
        self.assertEqual(decoded, data)
    
    def test_nested_map_canonical(self):
        """Test canonical encoding with nested maps"""
        data = {
            "outer": {
                "z": 1,
                "a": 2,
            },
            "array": [3, 4, 5],
        }
        
        canonical = cbor_encode(data, canonical=True)
        decoded = cbor_decode(canonical)
        
        self.assertEqual(decoded, data)
    
    def test_deterministic_encoding(self):
        """Test that canonical encoding is deterministic"""
        data = {
            "name": "test",
            "id": 42,
            "tags": ["a", "b", "c"],
        }
        
        # Encode multiple times
        encoding1 = cbor_encode(data, canonical=True)
        encoding2 = cbor_encode(data, canonical=True)
        encoding3 = cbor_encode(data, canonical=True)
        
        # All should be identical
        self.assertEqual(encoding1, encoding2)
        self.assertEqual(encoding2, encoding3)
    
    def test_canonical_for_signing(self):
        """Test canonical encoding for signature use case"""
        # This is a common use case: encoding data for signing
        data = {
            "payload": "message",
            "timestamp": 1234567890,
            "nonce": "abc123",
        }
        
        canonical = cbor_encode(data, canonical=True)
        
        # Compute hash (simulated signature)
        import hashlib
        hash1 = hashlib.sha256(canonical).hexdigest()
        
        # Re-encode and hash
        canonical2 = cbor_encode(data, canonical=True)
        hash2 = hashlib.sha256(canonical2).hexdigest()
        
        # Hashes must match (deterministic)
        self.assertEqual(hash1, hash2)


class TestJSONToCBOR(unittest.TestCase):
    """Test JSON to CBOR conversion"""
    
    def test_simple_object(self):
        """Test converting simple JSON object"""
        json_str = '{"name": "test", "id": 42}'
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded, {"name": "test", "id": 42})
    
    def test_array(self):
        """Test converting JSON array"""
        json_str = '[1, 2, 3, "four", true, null]'
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded, [1, 2, 3, "four", True, None])
    
    def test_nested_structure(self):
        """Test converting nested JSON"""
        json_str = '''{
            "user": {
                "name": "Alice",
                "tags": ["admin", "user"],
                "active": true
            }
        }'''
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        expected = {
            "user": {
                "name": "Alice",
                "tags": ["admin", "user"],
                "active": True
            }
        }
        self.assertEqual(decoded, expected)
    
    def test_typed_bytes(self):
        """Test converting typed bytes annotation"""
        json_str = '''{
            "data": {
                "$cbor": "bytes",
                "$value": "AQIDBA=="
            }
        }'''
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded, {"data": b'\x01\x02\x03\x04'})
    
    def test_typed_tag(self):
        """Test converting typed tag annotation"""
        json_str = '''{
            "uri": {
                "$cbor": "tag",
                "$tag": 32,
                "$value": "http://example.com"
            }
        }'''
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded, {"uri": (32, "http://example.com")})
    
    def test_canonical_from_json(self):
        """Test canonical encoding from JSON"""
        json_str = '{"z": 1, "a": 2}'
        cbor_bytes = json_to_cbor(json_str, canonical=True)
        
        # Re-encode should be identical
        decoded = cbor_decode(cbor_bytes)
        cbor_bytes2 = cbor_encode(decoded, canonical=True)
        
        self.assertEqual(cbor_bytes, cbor_bytes2)


class TestCBORToJSON(unittest.TestCase):
    """Test CBOR to JSON conversion"""
    
    def test_simple_conversion(self):
        """Test converting simple CBOR to JSON"""
        data = {"name": "test", "value": 42}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes)
        
        parsed = json.loads(json_str)
        self.assertEqual(parsed, data)
    
    def test_bytes_without_typing(self):
        """Test bytes conversion without type preservation"""
        data = {"data": b'\x01\x02\x03'}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes, typed=False)
        
        parsed = json.loads(json_str)
        # Without typing, bytes become base64 string
        self.assertIn("data", parsed)
        self.assertIsInstance(parsed["data"], str)
    
    def test_bytes_with_typing(self):
        """Test bytes conversion with type preservation"""
        data = {"data": b'\x01\x02\x03'}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes, typed=True)
        
        parsed = json.loads(json_str)
        # With typing, bytes have $cbor annotation
        self.assertEqual(parsed["data"]["$cbor"], "bytes")
        self.assertIn("$value", parsed["data"])
    
    def test_tagged_value_without_typing(self):
        """Test tagged value conversion without type preservation"""
        data = {"uri": (32, "http://example.com")}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes, typed=False)
        
        parsed = json.loads(json_str)
        # Without typing, just get the value
        self.assertEqual(parsed["uri"], "http://example.com")
    
    def test_tagged_value_with_typing(self):
        """Test tagged value conversion with type preservation"""
        data = {"uri": (32, "http://example.com")}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes, typed=True)
        
        parsed = json.loads(json_str)
        # With typing, get tag annotation
        self.assertEqual(parsed["uri"]["$cbor"], "tag")
        self.assertEqual(parsed["uri"]["$tag"], 32)
        self.assertEqual(parsed["uri"]["$value"], "http://example.com")
    
    def test_pretty_printing(self):
        """Test pretty-printed JSON output"""
        data = {"a": 1, "b": 2, "c": 3}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes, pretty=True)
        
        # Should have newlines and indentation
        self.assertIn('\n', json_str)
        self.assertIn('  ', json_str)
        
        # Should still parse correctly
        parsed = json.loads(json_str)
        self.assertEqual(parsed, data)


class TestRoundTrip(unittest.TestCase):
    """Test round-trip conversions"""
    
    def test_json_cbor_json(self):
        """Test JSON → CBOR → JSON round trip"""
        original_json = '{"name": "test", "items": [1, 2, 3]}'
        
        # Convert to CBOR
        cbor_bytes = json_to_cbor(original_json)
        
        # Convert back to JSON
        result_json = cbor_to_json(cbor_bytes)
        
        # Parse both
        original_data = json.loads(original_json)
        result_data = json.loads(result_json)
        
        self.assertEqual(original_data, result_data)
    
    def test_cbor_json_cbor(self):
        """Test CBOR → JSON → CBOR round trip with typing"""
        original_data = {
            "text": "hello",
            "bytes": b'\x01\x02\x03',
            "tag": (32, "http://example.com"),
        }
        
        # Encode to CBOR
        cbor_bytes1 = cbor_encode(original_data)
        
        # Convert to JSON (with typing to preserve types)
        json_str = cbor_to_json(cbor_bytes1, typed=True)
        
        # Convert back to CBOR
        cbor_bytes2 = json_to_cbor(json_str)
        
        # Decode both
        decoded1 = cbor_decode(cbor_bytes1)
        decoded2 = cbor_decode(cbor_bytes2)
        
        self.assertEqual(decoded1, decoded2)
    
    def test_canonical_preserves_data(self):
        """Test canonical encoding preserves data integrity"""
        data = {
            "z": [1, 2, 3],
            "a": {"nested": "value"},
            "m": 42,
        }
        
        # Encode with and without canonical
        normal_bytes = cbor_encode(data, canonical=False)
        canonical_bytes = cbor_encode(data, canonical=True)
        
        # Both should decode to same data
        self.assertEqual(cbor_decode(normal_bytes), cbor_decode(canonical_bytes))
        self.assertEqual(cbor_decode(canonical_bytes), data)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and special values"""
    
    def test_empty_structures(self):
        """Test empty objects and arrays"""
        json_str = '{"empty_obj": {}, "empty_arr": []}'
        cbor_bytes = json_to_cbor(json_str)
        json_result = cbor_to_json(cbor_bytes)
        
        parsed = json.loads(json_result)
        self.assertEqual(parsed, {"empty_obj": {}, "empty_arr": []})
    
    def test_unicode(self):
        """Test Unicode handling"""
        data = {"text": "Hello 世界 🎉"}
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes)
        
        parsed = json.loads(json_str)
        self.assertEqual(parsed["text"], "Hello 世界 🎉")
    
    def test_large_numbers(self):
        """Test large number handling"""
        data = {"big": 2**53 - 1}  # Max safe integer in JSON
        cbor_bytes = cbor_encode(data)
        json_str = cbor_to_json(cbor_bytes)
        
        parsed = json.loads(json_str)
        self.assertEqual(parsed["big"], 2**53 - 1)
    
    def test_null_values(self):
        """Test null/None handling"""
        json_str = '{"value": null}'
        cbor_bytes = json_to_cbor(json_str)
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded, {"value": None})


def run_tests():
    """Run all tests"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestCanonicalEncoding))
    suite.addTests(loader.loadTestsFromTestCase(TestJSONToCBOR))
    suite.addTests(loader.loadTestsFromTestCase(TestCBORToJSON))
    suite.addTests(loader.loadTestsFromTestCase(TestRoundTrip))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    print("=" * 70)
    print("Canonical Encoding & JSON Conversion - Unit Tests")
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
    
    import sys
    sys.exit(0 if result.wasSuccessful() else 1)