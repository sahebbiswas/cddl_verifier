#!/usr/bin/env python3
"""
Tests for CBOR Iterative Construction and Modification

Tests the builder pattern and fluent API for easy CBOR construction.
"""

import unittest
from simple_cbor import CBOR, cbor_encode, cbor_decode


class TestBuilderPattern(unittest.TestCase):
    """Test fluent API builder pattern"""
    
    def test_set_method(self):
        """Test fluent set() method"""
        cbor = CBOR({}).set("name", "Alice").set("age", 30).set("active", True)
        
        self.assertEqual(cbor.data, {
            "name": "Alice",
            "age": 30,
            "active": True
        })
    
    def test_append_method(self):
        """Test fluent append() method"""
        cbor = CBOR([]).append(1).append(2).append(3)
        
        self.assertEqual(cbor.data, [1, 2, 3])
    
    def test_extend_method(self):
        """Test fluent extend() method"""
        cbor = CBOR([1, 2]).extend([3, 4, 5])
        
        self.assertEqual(cbor.data, [1, 2, 3, 4, 5])
    
    def test_update_method(self):
        """Test fluent update() method"""
        cbor = CBOR({"a": 1}).update({"b": 2}, c=3)
        
        self.assertEqual(cbor.data, {"a": 1, "b": 2, "c": 3})
    
    def test_delete_method(self):
        """Test fluent delete() method"""
        cbor = CBOR({"a": 1, "b": 2, "c": 3}).delete("b")
        
        self.assertEqual(cbor.data, {"a": 1, "c": 3})
    
    def test_chained_operations(self):
        """Test chaining multiple operations"""
        cbor = (CBOR({})
                .set("users", [])
                .set("count", 0))
        
        # Add users
        cbor["users"].append({"name": "Alice"})
        cbor["users"].append({"name": "Bob"})
        cbor.set("count", len(cbor["users"]))
        
        self.assertEqual(cbor.data, {
            "users": [
                {"name": "Alice"},
                {"name": "Bob"}
            ],
            "count": 2
        })
    
    def test_builder_pattern_encoding(self):
        """Test that built objects can be encoded"""
        cbor = (CBOR({})
                .set("id", "test-001")
                .set("tags", [])
                .set("active", True))
        
        cbor["tags"].extend(["important", "verified"])
        
        # Encode
        cbor_bytes = cbor.encode()
        
        # Decode and verify
        decoded = cbor_decode(cbor_bytes)
        self.assertEqual(decoded, cbor.data)


class TestNestedAccess(unittest.TestCase):
    """Test nested path access"""
    
    def test_get_nested_simple(self):
        """Test getting nested values"""
        cbor = CBOR({
            "user": {
                "name": "Alice",
                "address": {
                    "city": "NYC",
                    "zip": "10001"
                }
            }
        })
        
        self.assertEqual(cbor.get_nested("user.name"), "Alice")
        self.assertEqual(cbor.get_nested("user.address.city"), "NYC")
        self.assertEqual(cbor.get_nested("user.address.zip"), "10001")
    
    def test_get_nested_with_default(self):
        """Test getting nested values with defaults"""
        cbor = CBOR({"user": {"name": "Alice"}})
        
        self.assertEqual(cbor.get_nested("user.name"), "Alice")
        self.assertEqual(cbor.get_nested("user.age", default=0), 0)
        self.assertEqual(cbor.get_nested("missing.path", default="N/A"), "N/A")
    
    def test_set_nested_create(self):
        """Test setting nested values with auto-creation"""
        cbor = CBOR({})
        cbor.set_nested("user.address.city", "NYC")
        
        self.assertEqual(cbor.data, {
            "user": {
                "address": {
                    "city": "NYC"
                }
            }
        })
    
    def test_set_nested_existing(self):
        """Test setting nested values in existing structure"""
        cbor = CBOR({
            "user": {
                "name": "Alice"
            }
        })
        
        cbor.set_nested("user.age", 30)
        cbor.set_nested("user.address.city", "NYC")
        
        self.assertEqual(cbor.data, {
            "user": {
                "name": "Alice",
                "age": 30,
                "address": {
                    "city": "NYC"
                }
            }
        })
    
    def test_nested_with_arrays(self):
        """Test nested access with array indices"""
        cbor = CBOR({
            "items": [
                {"name": "item1"},
                {"name": "item2"}
            ]
        })
        
        # Access by index
        self.assertEqual(cbor.get_nested("items.0.name"), "item1")
        self.assertEqual(cbor.get_nested("items.1.name"), "item2")


class TestUtilityMethods(unittest.TestCase):
    """Test utility methods"""
    
    def test_get_with_default(self):
        """Test get() method"""
        cbor = CBOR({"name": "Alice"})
        
        self.assertEqual(cbor.get("name"), "Alice")
        self.assertEqual(cbor.get("age", 0), 0)
    
    def test_keys_values_items(self):
        """Test dict-like methods"""
        cbor = CBOR({"a": 1, "b": 2, "c": 3})
        
        self.assertEqual(set(cbor.keys()), {"a", "b", "c"})
        self.assertEqual(set(cbor.values()), {1, 2, 3})
        self.assertEqual(set(cbor.items()), {("a", 1), ("b", 2), ("c", 3)})
    
    def test_clear(self):
        """Test clear() method"""
        cbor = CBOR({"a": 1, "b": 2})
        cbor.clear()
        
        self.assertEqual(cbor.data, {})
        
        # Test with list
        cbor2 = CBOR([1, 2, 3])
        cbor2.clear()
        
        self.assertEqual(cbor2.data, [])
    
    def test_copy(self):
        """Test copy() method"""
        cbor1 = CBOR({"a": 1, "nested": {"b": 2}})
        cbor2 = cbor1.copy()
        
        # Modify copy
        cbor2.set("a", 999)
        cbor2["nested"]["b"] = 888
        
        # Original unchanged
        self.assertEqual(cbor1["a"], 1)
        self.assertEqual(cbor1["nested"]["b"], 2)
    
    def test_merge_dicts(self):
        """Test merge() with dicts"""
        cbor1 = CBOR({"a": 1, "b": 2})
        cbor2 = CBOR({"c": 3, "d": 4})
        
        cbor1.merge(cbor2)
        
        self.assertEqual(cbor1.data, {"a": 1, "b": 2, "c": 3, "d": 4})
    
    def test_merge_lists(self):
        """Test merge() with lists"""
        cbor1 = CBOR([1, 2, 3])
        cbor2 = CBOR([4, 5, 6])
        
        cbor1.merge(cbor2)
        
        self.assertEqual(cbor1.data, [1, 2, 3, 4, 5, 6])
    
    def test_to_dict_to_list(self):
        """Test to_dict() and to_list() methods"""
        cbor_dict = CBOR({"a": 1})
        self.assertEqual(cbor_dict.to_dict(), {"a": 1})
        
        cbor_list = CBOR([1, 2, 3])
        self.assertEqual(cbor_list.to_list(), [1, 2, 3])


class TestIterativeConstruction(unittest.TestCase):
    """Test building CBOR objects iteratively"""
    
    def test_build_corim_structure(self):
        """Test building a CoRIM-like structure"""
        # Build CoRIM iteratively
        corim = CBOR({})
        
        # Set basic fields
        corim.set("id", "corim-001")
        corim.set("tags", [])
        
        # Add tags
        tag1 = {"id": "tag-001", "values": [1, 2, 3]}
        tag2 = {"id": "tag-002", "values": [4, 5, 6]}
        
        corim["tags"].append(tag1)
        corim["tags"].append(tag2)
        
        # Verify structure
        self.assertEqual(corim["id"], "corim-001")
        self.assertEqual(len(corim["tags"]), 2)
        self.assertEqual(corim["tags"][0]["id"], "tag-001")
        
        # Encode and decode
        cbor_bytes = corim.encode()
        decoded = cbor_decode(cbor_bytes)
        self.assertEqual(decoded, corim.data)
    
    def test_incremental_array_building(self):
        """Test building arrays incrementally"""
        cbor = CBOR([])
        
        # Add items one by one
        for i in range(1, 11):
            cbor.append(i)
        
        self.assertEqual(cbor.data, list(range(1, 11)))
        
        # Extend with more
        cbor.extend([11, 12, 13])
        
        self.assertEqual(len(cbor.data), 13)
    
    def test_incremental_map_building(self):
        """Test building maps incrementally"""
        cbor = CBOR({})
        
        # Add fields one by one
        fields = {
            "name": "Alice",
            "age": 30,
            "city": "NYC",
            "active": True
        }
        
        for key, value in fields.items():
            cbor.set(key, value)
        
        self.assertEqual(cbor.data, fields)
    
    def test_modify_after_encoding(self):
        """Test modifying and re-encoding"""
        cbor = CBOR({"count": 0, "items": []})
        
        # Encode initial state
        bytes1 = cbor.encode()
        
        # Modify
        cbor.set("count", 3)
        cbor["items"].extend([1, 2, 3])
        
        # Re-encode
        bytes2 = cbor.encode()
        
        # Should be different
        self.assertNotEqual(bytes1, bytes2)
        
        # Decode both
        decoded1 = cbor_decode(bytes1)
        decoded2 = cbor_decode(bytes2)
        
        self.assertEqual(decoded1["count"], 0)
        self.assertEqual(decoded2["count"], 3)


class TestComplexScenarios(unittest.TestCase):
    """Test complex real-world scenarios"""
    
    def test_api_request_building(self):
        """Test building an API request payload"""
        request = (CBOR({})
                   .set("method", "create_user")
                   .set("version", "1.0")
                   .set("params", {}))
        
        # Add parameters
        request["params"]["username"] = "alice"
        request["params"]["email"] = "alice@example.com"
        request["params"]["roles"] = ["admin", "user"]
        
        # Encode
        cbor_bytes = request.encode()
        
        # Verify it decodes correctly
        decoded = cbor_decode(cbor_bytes)
        self.assertEqual(decoded["method"], "create_user")
        self.assertEqual(decoded["params"]["username"], "alice")
        self.assertEqual(len(decoded["params"]["roles"]), 2)
    
    def test_sensor_data_accumulation(self):
        """Test accumulating sensor readings"""
        sensor_log = CBOR({
            "sensor_id": "temp-001",
            "readings": []
        })
        
        # Simulate sensor readings over time
        readings = [
            {"timestamp": 1000, "value": 20.5},
            {"timestamp": 2000, "value": 21.0},
            {"timestamp": 3000, "value": 20.8},
        ]
        
        for reading in readings:
            sensor_log["readings"].append(reading)
        
        # Add metadata
        sensor_log.set("count", len(sensor_log["readings"]))
        sensor_log.set("avg", sum(r["value"] for r in sensor_log["readings"]) / len(sensor_log["readings"]))
        
        # Encode
        cbor_bytes = sensor_log.encode()
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded["count"], 3)
        self.assertAlmostEqual(decoded["avg"], 20.77, places=2)
    
    def test_nested_structure_modification(self):
        """Test modifying deeply nested structures"""
        cbor = CBOR({})
        
        # Build nested structure
        cbor.set_nested("user.profile.personal.name", "Alice")
        cbor.set_nested("user.profile.personal.age", 30)
        cbor.set_nested("user.profile.contact.email", "alice@example.com")
        cbor.set_nested("user.settings.theme", "dark")
        cbor.set_nested("user.settings.notifications", True)
        
        # Verify structure
        self.assertEqual(cbor.get_nested("user.profile.personal.name"), "Alice")
        self.assertEqual(cbor.get_nested("user.settings.theme"), "dark")
        
        # Encode and verify
        cbor_bytes = cbor.encode()
        decoded = cbor_decode(cbor_bytes)
        
        self.assertEqual(decoded["user"]["profile"]["personal"]["name"], "Alice")
        self.assertEqual(decoded["user"]["settings"]["theme"], "dark")


def run_tests():
    """Run all tests"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestBuilderPattern))
    suite.addTests(loader.loadTestsFromTestCase(TestNestedAccess))
    suite.addTests(loader.loadTestsFromTestCase(TestUtilityMethods))
    suite.addTests(loader.loadTestsFromTestCase(TestIterativeConstruction))
    suite.addTests(loader.loadTestsFromTestCase(TestComplexScenarios))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    print("=" * 70)
    print("CBOR Iterative Construction & Modification - Unit Tests")
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
