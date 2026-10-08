#!/usr/bin/env python3
"""
Tests for CBOR.set_nested method
"""

import sys
from pathlib import Path

# Add repo root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from simple_cbor import CBOR

class TestCBORSetNested(unittest.TestCase):
    """Test cases for CBOR.set_nested method"""
    
    def test_set_nested_basic_dict(self):
        """Test setting nested values in dictionaries"""
        cbor = CBOR({})
        cbor.set_nested("a.b.c", 123)
        self.assertEqual(cbor.data, {"a": {"b": {"c": 123}}})
        
    def test_set_nested_with_lists(self):
        """Test setting nested values with list indices"""
        cbor = CBOR({})
        cbor.set_nested("items.0.id", "item1")
        cbor.set_nested("items.1.id", "item2")
        self.assertEqual(cbor.data, {
            "items": [
                {"id": "item1"},
                {"id": "item2"}
            ]
        })
        
    def test_set_nested_list_extension(self):
        """Test that lists are extended when index is out of range and create=True"""
        cbor = CBOR({"arr": [1]})
        cbor.set_nested("arr.3", 4)
        self.assertEqual(cbor.data, {"arr": [1, None, None, 4]})
        
    def test_set_nested_create_false_key_error(self):
        """Test that KeyError is raised when path doesn't exist and create=False"""
        cbor = CBOR({"a": {}})
        with self.assertRaises(KeyError):
            cbor.set_nested("a.b.c", 123, create=False)
            
    def test_set_nested_create_false_index_error(self):
        """Test that IndexError is raised when list index is out of range and create=False"""
        cbor = CBOR({"arr": [1, 2]})
        with self.assertRaises(IndexError):
            cbor.set_nested("arr.5", 123, create=False)
            
    def test_set_nested_type_error_navigation(self):
        """Test that TypeError is raised when trying to navigate into a non-container"""
        cbor = CBOR({"a": 123})
        with self.assertRaises(TypeError):
            cbor.set_nested("a.b", 456)
            
    def test_set_nested_type_error_list_key(self):
        """Test that TypeError is raised when using a non-numeric key for a list"""
        cbor = CBOR({"arr": [1, 2]})
        with self.assertRaises(TypeError):
            cbor.set_nested("arr.key", 123)
            
    def test_set_nested_empty_path(self):
        """Test that ValueError is raised for an empty path"""
        cbor = CBOR({})
        with self.assertRaises(ValueError):
            cbor.set_nested("", 123)
            
    def test_set_nested_custom_separator(self):
        """Test using a custom separator"""
        cbor = CBOR({})
        cbor.set_nested("a/b/c", 123, separator="/")
        self.assertEqual(cbor.data, {"a": {"b": {"c": 123}}})
        
    def test_set_nested_numeric_dict_keys(self):
        """Test handling of numeric strings as dictionary keys"""
        # If we have a dict and the key is "0", it should be a dict key, not a list index
        cbor = CBOR({"0": "string_key"})
        cbor.set_nested("0", "updated")
        self.assertEqual(cbor.data, {"0": "updated"})
        
        # Test creation of int keys vs string keys
        # Currently the implementation prefers string keys for dicts in set_nested
        cbor = CBOR({})
        cbor.set_nested("123", "value")
        self.assertEqual(cbor.data, {"123": "value"})
        
    def test_set_nested_fluent_api(self):
        """Test that set_nested returns self for method chaining"""
        cbor = CBOR({})
        result = cbor.set_nested("a", 1).set_nested("b", 2)
        self.assertIs(result, cbor)
        self.assertEqual(cbor.data, {"a": 1, "b": 2})
        
    def test_set_nested_cache_invalidation(self):
        """Test that set_nested invalidates the encoded bytes cache"""
        cbor = CBOR({"a": 1})
        cbor.encode()
        self.assertIsNotNone(cbor._cached_bytes)
        
        cbor.set_nested("a", 2)
        self.assertIsNone(cbor._cached_bytes)

    def test_deeply_nested_mixed(self):
        """Test deeply nested mixed structures"""
        cbor = CBOR({})
        cbor.set_nested("users.0.profile.tags.0", "admin")
        self.assertEqual(cbor.data, {
            "users": [
                {
                    "profile": {
                        "tags": ["admin"]
                    }
                }
            ]
        })

    def test_set_nested_integer_dict_keys(self):
        """Test that integer dictionary keys are handled correctly during navigation"""
        cbor = CBOR({123: {"a": 1}})
        cbor.set_nested("123.b", 2)
        self.assertEqual(cbor.data, {123: {"a": 1, "b": 2}})

    def test_set_nested_overwrite_non_container(self):
        """Test that it fails when trying to navigate through a non-container that is not None"""
        cbor = CBOR({"a": "not_a_container"})
        with self.assertRaises(TypeError):
            cbor.set_nested("a.b", 123)

if __name__ == "__main__":
    unittest.main()
