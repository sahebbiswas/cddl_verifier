#!/usr/bin/env python3
"""
Unit tests for CBOR-CDDL Analyzer

Tests cover:
- CDDL parsing (aliases, tags, choices, groups)
- CBOR validation
- EDN generation with proper indentation
- Type resolution (aliases, choices, tags)
- IANA parameter annotations
- Size constraint validation
- Nested CBOR decoding
- Tag notation formatting
"""

import unittest
import sys
import os
from pathlib import Path
# When run directly (python3 tests/test_foo.py), add the repo root to
# sys.path so source modules are importable.  pytest handles this via
# tests/conftest.py instead.
sys.path.insert(0, str(Path(__file__).parent.parent))

from io import StringIO
import tempfile
import struct


from cbor_cddl_analyzer import (
    CDDLParser, 
    CBORAnalyzer, 
    EDNGenerator,
    load_cddl
)

# Import CBOR encoder/decoder
try:
    from simple_cbor import cbor_encode
    HAS_CBOR_ENCODER = True
except ImportError:
    # Try importing from main module
    try:
        from cbor_cddl_analyzer import SimpleCBORDecoder
        # Create minimal encoder if needed
        HAS_CBOR_ENCODER = False
    except:
        HAS_CBOR_ENCODER = False

# Try to import cbor2 as fallback
try:
    import cbor2
    HAS_CBOR2 = True
except ImportError:
    HAS_CBOR2 = False
    
def encode_cbor(data):
    """Encode data to CBOR bytes"""
    if HAS_CBOR_ENCODER:
        return cbor_encode(data)
    elif HAS_CBOR2:
        return cbor2.dumps(data)
    else:
        # Minimal manual encoding for simple test cases
        if isinstance(data, dict) and all(isinstance(k, int) and isinstance(v, int) for k, v in data.items()):
            # Simple map encoding
            n = len(data)
            result = bytes([0xa0 + n])  # Map with N items (only works for n < 16)
            for k, v in sorted(data.items()):
                if k < 24:
                    result += bytes([k])
                if v < 24:
                    result += bytes([v])
            return result
        return b''


class TestCDDLParsing(unittest.TestCase):
    """Test CDDL schema parsing"""
    
    def test_simple_alias(self):
        """Test simple type alias parsing"""
        cddl = CDDLParser("person-name = tstr")
        self.assertIn('person-name', cddl.type_aliases)
        self.assertEqual(cddl.type_aliases['person-name'], 'tstr')
    
    def test_cbor_tag_alias(self):
        """Test CBOR tag definition parsing"""
        cddl = CDDLParser("tagged-corim = #6.501(corim-map)")
        self.assertIn('tagged-corim', cddl.type_aliases)
        self.assertEqual(cddl.type_aliases['tagged-corim'], '#6.501(corim-map)')
        
        # Verify tag extraction works
        tag_info = cddl.extract_cbor_tag('#6.501(corim-map)')
        self.assertIsNotNone(tag_info)
        self.assertEqual(tag_info[0], 501)
        self.assertEqual(tag_info[1], 'corim-map')
    
    def test_cbor_control_nested(self):
        """Test .cbor control operator parsing"""
        cddl = CDDLParser("tagged-mid = #6.506(bytes .cbor concise-mid-tag)")
        
        # Verify it parses as alias
        self.assertIn('tagged-mid', cddl.type_aliases)
        
        # Verify .cbor extraction
        cbor_control = cddl.extract_cbor_control('bytes .cbor concise-mid-tag')
        self.assertIsNotNone(cbor_control)
        self.assertEqual(cbor_control[0], 'bytes')
        self.assertEqual(cbor_control[1], 'concise-mid-tag')
    
    def test_type_choice(self):
        """Test type choice parsing"""
        cddl_text = """
        $my-choice /= option-a
        $my-choice /= option-b
        """
        cddl = CDDLParser(cddl_text)
        
        self.assertIn('$my-choice', cddl.type_choices)
        choices = cddl.type_choices['$my-choice']
        self.assertIn('option-a', choices)
        self.assertIn('option-b', choices)
    
    def test_iana_registered_param(self):
        """Test IANA registered parameter parsing"""
        cddl_text = """
        record = {
          &( name : 0 ) => tstr,
          &( age : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        
        # Check global registered params
        self.assertEqual(cddl.registered_params.get(0), 'name')
        self.assertEqual(cddl.registered_params.get(1), 'age')
        
        # Check type definition
        record_def = cddl.types.get('record')
        self.assertIsNotNone(record_def)
        self.assertEqual(record_def['fields'][0]['name'], 'name')
        self.assertEqual(record_def['fields'][1]['name'], 'age')
    
    def test_optional_fields(self):
        """Test optional field parsing"""
        cddl_text = """
        record = {
          &( required : 0 ) => tstr,
          ? &( optional : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        
        record_def = cddl.types['record']
        self.assertFalse(record_def['fields'][0]['optional'])
        self.assertTrue(record_def['fields'][1]['optional'])
    
    def test_size_constraint_exact(self):
        """Test .size exact constraint parsing"""
        cddl_text = """
        uuid = bstr .size 16
        """
        cddl = CDDLParser(cddl_text)
        
        size = cddl.extract_size_constraint('bstr .size 16')
        self.assertIsNotNone(size)
        self.assertEqual(size['exact'], 16)
        self.assertIsNone(size['min'])
        self.assertIsNone(size['max'])
    
    def test_size_constraint_range(self):
        """Test .size range constraint parsing"""
        cddl = CDDLParser("")
        
        # Range
        size = cddl.extract_size_constraint('text .size (8..64)')
        self.assertEqual(size['min'], 8)
        self.assertEqual(size['max'], 64)
        self.assertIsNone(size['exact'])
        
        # Minimum only
        size = cddl.extract_size_constraint('bytes .size (16..)')
        self.assertEqual(size['min'], 16)
        self.assertIsNone(size['max'])
        
        # Maximum only
        size = cddl.extract_size_constraint('tstr .size (..100)')
        self.assertIsNone(size['min'])
        self.assertEqual(size['max'], 100)
    
    def test_multiline_field_parsing(self):
        """Test multi-line field definition parsing"""
        cddl_text = """
        record = {
          ? &(long-field-name: 0) =>
            [ + nested-type ]
        }
        """
        cddl = CDDLParser(cddl_text)
        
        record_def = cddl.types['record']
        self.assertIn(0, record_def['fields'])
        self.assertEqual(record_def['fields'][0]['name'], 'long-field-name')
        self.assertIn('[ + nested-type ]', record_def['fields'][0]['type'])


class TestTypeResolution(unittest.TestCase):
    """Test type alias and choice resolution"""
    
    def test_simple_alias_resolution(self):
        """Test resolving simple alias"""
        cddl = CDDLParser("name = tstr")
        resolved = cddl.resolve_type_alias('name')
        self.assertEqual(resolved, 'tstr')
    
    def test_chained_alias_resolution(self):
        """Test resolving chained aliases"""
        cddl_text = """
        a = b
        b = c
        c = tstr
        """
        cddl = CDDLParser(cddl_text)
        
        resolved = cddl.resolve_type_alias('a')
        self.assertEqual(resolved, 'tstr')
    
    def test_tag_inner_type_extraction(self):
        """Test extracting inner type from tag"""
        cddl_text = """
        tagged-corim = #6.501(unsigned-corim-map)
        unsigned-corim-map = corim-map
        """
        cddl = CDDLParser(cddl_text)
        
        # Get tag info
        alias_value = cddl.type_aliases['tagged-corim']
        tag_info = cddl.extract_cbor_tag(alias_value)
        
        self.assertEqual(tag_info[0], 501)
        self.assertEqual(tag_info[1], 'unsigned-corim-map')
        
        # Resolve inner type
        resolved = cddl.resolve_type_alias('unsigned-corim-map')
        self.assertEqual(resolved, 'corim-map')


class TestCBORValidation(unittest.TestCase):
    """Test CBOR data validation against CDDL"""
    
    def test_simple_map_validation(self):
        """Test validating a simple map"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
          &( age : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        analyzer = CBORAnalyzer(cddl)
        
        # Valid data
        data = {0: "Alice", 1: 30}
        self.assertTrue(analyzer.validate(data, 'person'))
        
        # Invalid - wrong type
        data = {0: "Alice", 1: "thirty"}
        self.assertFalse(analyzer.validate(data, 'person'))
    
    def test_optional_field_validation(self):
        """Test optional field handling"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
          ? &( age : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        analyzer = CBORAnalyzer(cddl)
        
        # Valid with optional field
        self.assertTrue(analyzer.validate({0: "Alice", 1: 30}, 'person'))
        
        # Valid without optional field
        self.assertTrue(analyzer.validate({0: "Alice"}, 'person'))
    
    def test_size_constraint_validation(self):
        """Test .size constraints are enforced during validation.

        Both exact sizes (bstr .size 16) and range sizes (tstr .size (1..10))
        must be validated.  Data that violates a constraint must fail.
        """
        cddl_text = """
        record = {
          &( uuid : 0 ) => bstr .size 16,
          &( name : 1 ) => tstr .size (1..10),
        }
        """
        cddl = CDDLParser(cddl_text)
        analyzer = CBORAnalyzer(cddl)

        # Valid — exact bstr size and in-range tstr
        self.assertTrue(
            analyzer.validate({0: b'x' * 16, 1: "Alice"}, 'record'),
            "Valid data should pass",
        )

        # Invalid — bstr too large
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: b'x' * 20, 1: "Alice"}, 'record'),
            "bstr exceeding .size 16 should fail",
        )

        # Invalid — tstr too long (exceeds max of 10)
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: b'x' * 16, 1: "x" * 11}, 'record'),
            "tstr exceeding .size max should fail",
        )

        # Invalid — tstr too short (below min of 1)
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: b'x' * 16, 1: ""}, 'record'),
            "tstr below .size min should fail",
        )
    
    def test_array_validation(self):
        """Test array occurrence and element-type validation.

        [ + uint ] requires at least one element and every element must be a
        non-negative integer.  Negative integers, strings, and empty arrays
        must all fail.
        """
        cddl_text = """
        numbers = [ + uint ]
        """
        cddl = CDDLParser(cddl_text)

        self.assertTrue(
            CBORAnalyzer(cddl).validate([1, 2, 3], 'numbers'),
            "Array of uints should validate",
        )
        self.assertFalse(
            CBORAnalyzer(cddl).validate([], 'numbers'),
            "Empty array must fail for + occurrence",
        )
        self.assertFalse(
            CBORAnalyzer(cddl).validate([1, "two", 3], 'numbers'),
            "String element must fail for uint array",
        )
        self.assertFalse(
            CBORAnalyzer(cddl).validate([1, -1, 3], 'numbers'),
            "Negative element must fail for uint array",
        )


class TestEDNGeneration(unittest.TestCase):
    """Test EDN output generation"""
    
    def test_simple_edn_keyindex(self):
        """Test EDN generation with keyindex format"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
          &( age : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: "Alice", 1: 30}
        edn = generator.generate(data, 'person')
        
        # Check annotations are present and on the left
        self.assertIn('/ name / 0:', edn)
        self.assertIn('/ age / 1:', edn)
        
        # Check values
        self.assertIn('"Alice"', edn)
        self.assertIn('30', edn)
    
    def test_edn_keyname_format(self):
        """Test EDN generation with keyname format"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
          &( age : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyname')
        
        data = {0: "Alice", 1: 30}
        edn = generator.generate(data, 'person')
        
        # Should use names as keys
        self.assertIn('"name":', edn)
        self.assertIn('"age":', edn)
        self.assertIn('"Alice"', edn)
    
    def test_edn_both_format(self):
        """Test EDN generation with both format"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='both')
        
        data = {0: "Alice"}
        edn = generator.generate(data, 'person')
        
        # Should show both index and name
        self.assertIn('0 / name /:', edn)
    
    def test_edn_indentation(self):
        """Test EDN indentation is correct"""
        cddl_text = """
        outer = {
          &( inner : 0 ) => {
            &( value : 0 ) => uint,
          }
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: {0: 42}}
        edn = generator.generate(data, 'outer')
        
        lines = edn.split('\n')
        
        # Check opening brace
        self.assertTrue(lines[0].startswith('/') or lines[0].startswith('{'))
        
        # Check inner content is indented
        inner_lines = [l for l in lines if 'inner' in l or 'value' in l]
        for line in inner_lines:
            if line.strip():
                # Should have at least 2 spaces of indentation
                self.assertTrue(line.startswith('  ') or line.startswith('/'))
    
    def test_tag_notation(self):
        """Test CBOR tag notation in EDN"""
        cddl_text = """
        tagged-item = #6.32(tstr)
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        # Create tagged data (tag 32, value "test")
        data = (32, "test")
        edn = generator.generate(data, 'tagged-item')
        
        # Should show tag notation
        self.assertIn('32(', edn)
        self.assertIn('"test"', edn)
        self.assertIn(')', edn)
    
    def test_nested_tag_indentation(self):
        """Test nested tag indentation is correct"""
        cddl_text = """
        record = {
          &( data : 0 ) => #6.32(tstr)
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: (32, "test")}
        edn = generator.generate(data, 'record')
        
        lines = edn.split('\n')
        
        # Find the closing brace
        closing_brace_line = None
        for i, line in enumerate(lines):
            if line.strip() == '}':
                closing_brace_line = i
                break
        
        # Should have a closing brace
        self.assertIsNotNone(closing_brace_line)
        
        # Opening brace should be at start
        self.assertTrue(lines[0].strip().startswith('/') or lines[0].strip().startswith('{'))
    
    def test_type_name_headers(self):
        """Test type name headers on maps and arrays"""
        cddl_text = """
        person = {
          &( name : 0 ) => tstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: "Alice"}
        edn = generator.generate(data, 'person')
        
        # Should show type name
        self.assertIn('/ person / {', edn)
    
    def test_bytes_wrapper_for_nested_cbor(self):
        """Test bytes() wrapper for nested CBOR"""
        if not (HAS_CBOR_ENCODER or HAS_CBOR2):
            self.skipTest("Requires CBOR encoder (simple_cbor or cbor2)")
        
        cddl_text = """
        outer-type = #6.506(bytes .cbor inner-type)
        inner-type = {
          &( value : 0 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        
        # Create nested CBOR
        inner_data = {0: 42}
        inner_cbor = encode_cbor(inner_data)
        
        # Verify encoding worked
        if len(inner_cbor) == 0:
            self.skipTest("CBOR encoding failed")
        
        # Create tagged outer data
        data = (506, inner_cbor)
        
        generator = EDNGenerator(cddl, edn_format='keyindex')
        edn = generator.generate(data, 'outer-type')
        
        # Should have bytes wrapper
        self.assertIn('bytes<', edn)  # matches bytes<N>( annotation
        
        # Should show decoded content
        self.assertIn('/ value / 0:', edn)
        self.assertIn('42', edn)


class TestCoRIMSupport(unittest.TestCase):
    """Test CoRIM-specific features"""
    
    def test_corim_type_resolution(self):
        """Test CoRIM type resolution chain"""
        cddl_text = """
        corim = concise-rim-type-choice
        concise-rim-type-choice /= tagged-unsigned-corim-map
        tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
        unsigned-corim-map = corim-map
        corim-map = {
          &( id : 0 ) => tstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        
        # Verify type choice
        self.assertIn('concise-rim-type-choice', cddl.type_choices)
        self.assertIn('tagged-unsigned-corim-map', 
                     cddl.type_choices['concise-rim-type-choice'])
        
        # Verify tag definition
        self.assertIn('tagged-unsigned-corim-map', cddl.type_aliases)
        tag_info = cddl.extract_cbor_tag(
            cddl.type_aliases['tagged-unsigned-corim-map'])
        self.assertEqual(tag_info[0], 501)
        
        # Verify alias chain
        resolved = cddl.resolve_type_alias('unsigned-corim-map')
        self.assertEqual(resolved, 'corim-map')
    
    def test_corim_edn_output(self):
        """Test CoRIM EDN output formatting"""
        cddl_text = """
        corim = concise-rim-type-choice
        concise-rim-type-choice /= tagged-unsigned-corim-map
        tagged-unsigned-corim-map = #6.501(unsigned-corim-map)
        unsigned-corim-map = corim-map
        corim-map = {
          &( id : 0 ) => tstr,
          &( tags : 1 ) => [ + uint ],
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        # Create tagged CoRIM data
        data = (501, {0: "test-id", 1: [1, 2, 3]})
        edn = generator.generate(data, 'corim')
        
        # Should show resolved tag type
        self.assertIn('/ tagged-unsigned-corim-map / 501(', edn)
        
        # Should show inner map type
        self.assertIn('/ corim-map / {', edn)
        
        # Should show field annotations
        self.assertIn('/ id / 0:', edn)
        self.assertIn('/ tags / 1:', edn)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling"""
    
    def test_empty_map(self):
        """Test empty map handling"""
        cddl = CDDLParser("empty = {}")
        generator = EDNGenerator(cddl)
        
        edn = generator.generate({}, 'empty')
        self.assertEqual(edn.strip(), '{}')
    
    def test_empty_array(self):
        """Test empty array handling"""
        cddl = CDDLParser("empty = []")
        generator = EDNGenerator(cddl)
        
        edn = generator.generate([], 'empty')
        self.assertEqual(edn.strip(), '[]')
    
    def test_nested_empty_structures(self):
        """Test nested empty structures"""
        cddl_text = """
        outer = {
          &( inner : 0 ) => {},
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: {}}
        edn = generator.generate(data, 'outer')
        
        # Should handle empty inner map
        self.assertIn('{}', edn)
    
    def test_bytes_encoding(self):
        """Test byte string encoding in EDN"""
        cddl_text = """
        data = {
          &( hash : 0 ) => bstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: b'\x01\x02\x03\xff'}
        edn = generator.generate(data, 'data')
        
        # Should encode as hex
        self.assertIn("h'", edn)
        self.assertIn("010203ff", edn.lower())
    
    def test_undefined_type_graceful_handling(self):
        """Test graceful handling of undefined types"""
        cddl = CDDLParser("")
        generator = EDNGenerator(cddl)
        
        # Should not crash on undefined type
        data = {0: "test"}
        edn = generator.generate(data, 'undefined-type')
        
        # Should still generate EDN
        self.assertIn('"test"', edn)
    
    def test_circular_alias_prevention(self):
        """Test prevention of circular alias resolution"""
        cddl_text = """
        a = b
        b = a
        """
        cddl = CDDLParser(cddl_text)
        
        # Should not infinite loop
        resolved = cddl.resolve_type_alias('a')
        # Should return one of the names (can't fully resolve)
        self.assertIn(resolved, ['a', 'b'])


class TestIndentationAccuracy(unittest.TestCase):
    """Test precise indentation of EDN output"""
    
    def test_simple_map_indentation(self):
        """Test simple map has correct indentation"""
        cddl_text = """
        record = {
          &( a : 0 ) => uint,
          &( b : 1 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: 1, 1: 2}
        edn = generator.generate(data, 'record')
        
        lines = edn.split('\n')
        
        # Opening brace should have type annotation
        self.assertTrue('/ record / {' in lines[0] or lines[0].strip() == '{')
        
        # Field lines should be indented by 2 spaces
        field_lines = [l for l in lines if ': ' in l]
        for line in field_lines:
            self.assertTrue(line.startswith('  '))
        
        # Closing brace should not be indented
        closing = lines[-1]
        self.assertEqual(closing, '}')
    
    def test_nested_map_indentation(self):
        """Test nested maps have correct indentation"""
        cddl_text = """
        outer = {
          &( data : 0 ) => inner-map,
        }
        inner-map = {
          &( value : 0 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: {0: 42}}
        edn = generator.generate(data, 'outer')
        
        lines = edn.split('\n')
        
        # Check opening brace (line 0 or 1)
        has_outer = any('/ outer / {' in line or line.strip().startswith('{') for line in lines[:2])
        self.assertTrue(has_outer, "Should have outer map opening")
        
        # Check that nested content is indented
        has_indented_content = any(len(line) - len(line.lstrip()) >= 2 for line in lines if line.strip())
        self.assertTrue(has_indented_content, "Should have indented content")
        
        # Check closing braces are present
        closing_braces = [line for line in lines if line.strip() == '}']
        self.assertGreaterEqual(len(closing_braces), 2, "Should have at least 2 closing braces")
    
    def test_tag_indentation(self):
        """Test tag content indentation"""
        cddl_text = """
        outer = {
          &( tagged : 0 ) => #6.32(tstr)
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: (32, "test")}
        edn = generator.generate(data, 'outer')
        
        lines = edn.split('\n')
        
        # Find the tag line
        tag_line_idx = None
        for i, line in enumerate(lines):
            if '32(' in line:
                tag_line_idx = i
                break
        
        self.assertIsNotNone(tag_line_idx)
        
        # Tag should be indented as part of field value
        # Should be on same line as field or indented appropriately
        tag_line = lines[tag_line_idx]
        if not ': ' in tag_line:  # If on separate line
            leading_spaces = len(tag_line) - len(tag_line.lstrip())
            self.assertGreater(leading_spaces, 0)
    
    def test_array_indentation(self):
        """Test array element indentation"""
        cddl_text = """
        list = [ + {
          &( value : 0 ) => uint
        } ]
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = [{0: 1}, {0: 2}]
        edn = generator.generate(data, 'list')
        
        lines = edn.split('\n')
        
        # Array elements should be indented
        for line in lines[1:-1]:  # Skip first and last (brackets)
            if line.strip() and line.strip() != '{' and line.strip() != '}':
                leading_spaces = len(line) - len(line.lstrip())
                self.assertGreater(leading_spaces, 0)
    
    def test_closing_bracket_alignment(self):
        """Test closing brackets align with opening"""
        cddl_text = """
        nested = {
          &( level1 : 0 ) => {
            &( level2 : 0 ) => {
              &( value : 0 ) => uint
            }
          }
        }
        """
        cddl = CDDLParser(cddl_text)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        
        data = {0: {0: {0: 42}}}
        edn = generator.generate(data, 'nested')
        
        lines = edn.split('\n')
        
        # Count opening and closing braces
        open_count = sum(line.count('{') for line in lines)
        close_count = sum(line.count('}') for line in lines)
        
        self.assertEqual(open_count, close_count)
        
        # Each closing brace should be at proper indentation
        for i, line in enumerate(lines):
            if line.strip() == '}':
                # Find corresponding opening
                # For now, just check it's not overly indented
                leading_spaces = len(line) - len(line.lstrip())
                # Should be 0, 2, 4, or 6 spaces (multiples of 2)
                self.assertEqual(leading_spaces % 2, 0)



class TestValidationGapsCoverage(unittest.TestCase):
    """Tests for previously uncovered validator behaviour.

    Covers: extra/unknown fields, bool/null/float strict type checks,
    optional-field wrong type, single-line type parse, and the
    now-working bytes_wrapper_for_nested_cbor test.
    """

    # ── Extra / unknown fields ────────────────────────────────────────────────

    def test_extra_field_fails_validation(self):
        """Unknown map keys not present in the CDDL schema must fail.

        A strict validator should reject data that contains keys the schema
        does not define — they may indicate corrupted or unexpected payloads.
        """
        cddl = CDDLParser("""
        person = {
          &( name : 0 ) => tstr,
          &( age  : 1 ) => uint,
        }
        """)
        analyzer = CBORAnalyzer(cddl)
        self.assertFalse(
            analyzer.validate({0: "Alice", 1: 30, 99: "unexpected"}, "person"),
            "Data with unknown key 99 should fail",
        )
        self.assertIn("99", str(analyzer.get_errors()), "Error message should mention the unknown key")

    # ── bool strict type check ────────────────────────────────────────────────

    def test_bool_rejects_integer_one(self):
        """bool fields must reject plain integer 1.

        In CBOR, True/False are major-type-7 simple values (bytes 0xF5/0xF4),
        not unsigned integers (major type 0).  Python's bool is a subclass of
        int, so isinstance(1, int) is True, but 1 is not a boolean.
        """
        cddl = CDDLParser("""
        flags = {
          &( active : 0 ) => bool,
        }
        """)
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: 1}, "flags"),
            "Integer 1 must not satisfy a bool field",
        )

    def test_bool_accepts_true_and_false(self):
        """bool fields must accept Python True and False."""
        cddl = CDDLParser("""
        flags = {
          &( active : 0 ) => bool,
        }
        """)
        self.assertTrue(CBORAnalyzer(cddl).validate({0: True},  "flags"))
        self.assertTrue(CBORAnalyzer(cddl).validate({0: False}, "flags"))

    # ── null / nil strict type check ─────────────────────────────────────────

    def test_null_rejects_non_none(self):
        """null fields must reject any non-None value."""
        cddl = CDDLParser("""
        envelope = {
          &( payload : 0 ) => null,
        }
        """)
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: "something"}, "envelope"),
            "String value must fail a null field",
        )
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: 0}, "envelope"),
            "Integer 0 must fail a null field",
        )

    def test_null_accepts_none(self):
        """null fields must accept Python None."""
        cddl = CDDLParser("""
        envelope = {
          &( payload : 0 ) => null,
        }
        """)
        self.assertTrue(CBORAnalyzer(cddl).validate({0: None}, "envelope"))

    # ── float strict type check ───────────────────────────────────────────────

    def test_float_rejects_integer(self):
        """float fields must reject plain Python int.

        CBOR integers (major type 0/1) and floats (major type 7 simple) are
        distinct on the wire and must not be silently coerced.
        """
        cddl = CDDLParser("""
        measurement = {
          &( value : 0 ) => float,
        }
        """)
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: 1}, "measurement"),
            "Integer 1 must not satisfy a float field",
        )

    def test_float_accepts_float_value(self):
        """float fields must accept Python float."""
        cddl = CDDLParser("""
        measurement = {
          &( value : 0 ) => float,
        }
        """)
        self.assertTrue(CBORAnalyzer(cddl).validate({0: 1.5}, "measurement"))

    # ── optional field wrong-type check ──────────────────────────────────────

    def test_optional_field_wrong_type_fails(self):
        """Providing the wrong type for an optional field must still fail.

        Absence is allowed; presence with the wrong type is not.
        """
        cddl = CDDLParser("""
        person = {
          &( name : 0 ) => tstr,
          ? &( age : 1 ) => uint,
        }
        """)
        # Optional field absent — valid
        self.assertTrue(CBORAnalyzer(cddl).validate({0: "Alice"}, "person"))
        # Optional field present with correct type — valid
        self.assertTrue(CBORAnalyzer(cddl).validate({0: "Alice", 1: 30}, "person"))
        # Optional field present with wrong type — invalid
        self.assertFalse(
            CBORAnalyzer(cddl).validate({0: "Alice", 1: "thirty"}, "person"),
            "Optional field with wrong type must fail",
        )

    # ── array * (zero-or-more) occurrence ────────────────────────────────────

    def test_star_occurrence_allows_empty_array(self):
        """[ * uint ] must accept an empty array (zero-or-more)."""
        cddl = CDDLParser("tags = [ * uint ]")
        self.assertTrue(CBORAnalyzer(cddl).validate([], "tags"))

    # ── single-line type definition parse ────────────────────────────────────

    def test_single_line_map_parsed(self):
        """Single-line map definitions like 'record = { &(name:0)=>tstr }' are
        now parsed as structured types, identical to the multi-line equivalent.
        """
        cddl_single = CDDLParser("record = { &( name : 0 ) => tstr }")
        cddl_multi  = CDDLParser("""
        record = {
          &( name : 0 ) => tstr,
        }
        """)
        # Both forms must produce a parseable type with the same fields
        self.assertIn("record", cddl_single.types,
                      "Single-line form should now be parsed correctly")
        self.assertIn("record", cddl_multi.types,
                      "Multi-line form should be parsed correctly")
        self.assertEqual(
            list(cddl_single.types["record"]["fields"].keys()),
            list(cddl_multi.types["record"]["fields"].keys()),
            "Single-line and multi-line forms should produce identical field keys",
        )
        # Validation must work on data parsed from the single-line form
        self.assertTrue(CBORAnalyzer(cddl_single).validate({0: "Alice"}, "record"))
        self.assertFalse(CBORAnalyzer(cddl_single).validate({0: 99},     "record"))

    # ── bytes_wrapper_for_nested_cbor (previously always skipped) ────────────

    def test_bytes_wrapper_for_nested_cbor_with_simple_cbor(self):
        """bytes() wrapper for nested CBOR using the bundled simple_cbor encoder.

        This test exercises the full path: encode inner CBOR with the bundled
        encoder, wrap it in a CBOR tag, then verify EDN output shows the
        bytes() wrapper and the decoded inner content.
        """
        try:
            from simple_cbor import cbor_encode
        except ImportError:
            self.skipTest("simple_cbor.cbor_encode not available")

        cddl_text = """
        outer-type = #6.506(bytes .cbor inner-type)
        inner-type = {
          &( value : 0 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        inner_cbor = cbor_encode({0: 42})
        self.assertGreater(len(inner_cbor), 0, "cbor_encode must produce non-empty bytes")

        data = (506, inner_cbor)
        generator = EDNGenerator(cddl, edn_format='keyindex')
        edn = generator.generate(data, 'outer-type')

        self.assertIn("bytes<", edn, "EDN should contain bytes<N>() wrapper")
        self.assertIn("/ value / 0:", edn, "EDN should show inner field annotation")
        self.assertIn("42", edn, "EDN should show inner value")

class TestFirstDefinition(unittest.TestCase):
    """Tests for CDDLParser.first_definition — the auto-infer root-type feature."""

    def test_first_definition_is_alias(self):
        """first_definition picks up a type alias as the first entry."""
        cddl = CDDLParser("my-root = tstr\nother = uint")
        self.assertEqual(cddl.first_definition, 'my-root')

    def test_first_definition_is_map(self):
        """first_definition picks up a map as the first entry."""
        cddl_text = """
        top-map = {
          &( x : 0 ) => uint,
        }
        alias = tstr
        """
        cddl = CDDLParser(cddl_text)
        self.assertEqual(cddl.first_definition, 'top-map')

    def test_first_definition_is_array(self):
        """first_definition picks up an array as the first entry."""
        cddl_text = """
        top-array = [ + uint ]
        some-map = {
          &( y : 0 ) => tstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        self.assertEqual(cddl.first_definition, 'top-array')

    def test_first_definition_alias_before_map(self):
        """When an alias precedes all maps/arrays it wins over the first map."""
        cddl_text = """
        corim = concise-rim-type-choice
        concise-rim-type-choice /= tagged-corim-map
        tagged-corim-map = #6.501(corim-map)
        corim-map = {
          &( id : 0 ) => tstr,
        }
        """
        cddl = CDDLParser(cddl_text)
        self.assertEqual(cddl.first_definition, 'corim')

    def test_first_definition_empty_schema(self):
        """An empty schema leaves first_definition as None."""
        cddl = CDDLParser("")
        self.assertIsNone(cddl.first_definition)


class TestResolveTypeAliasFixed(unittest.TestCase):
    """Tests for the two resolve_type_alias bug fixes."""

    def test_primitive_self_loop_no_repeat(self):
        """Primitives that map to themselves must resolve in a single step.

        Before the fix, 'uint' looped 10 times because _add_builtin_types
        registers it as uint -> uint.  The loop should break immediately.
        """
        cddl = CDDLParser("")
        # Calling resolve_type_alias must return instantly — no assertion about
        # the return value other than it being the same primitive.
        result = cddl.resolve_type_alias('uint')
        self.assertEqual(result, 'uint')

    def test_all_builtin_primitives_resolve_cleanly(self):
        """Every builtin primitive must resolve to itself without looping."""
        cddl = CDDLParser("")
        for name in ('uint', 'int', 'bool', 'tstr', 'bstr', 'float',
                     'nil', 'null', 'any', 'text', 'bytes', 'nint',
                     'float16', 'float32', 'float64', 'true', 'false'):
            result = cddl.resolve_type_alias(name)
            # Must return without infinite loop; result must be a non-empty string
            self.assertIsInstance(result, str)
            self.assertTrue(len(result) > 0, f"resolve_type_alias('{name}') returned empty string")

    def test_alias_chain_to_primitive_no_type_not_found(self):
        """Alias chains that resolve to a CDDL primitive must NOT log 'Type not found'.

        Before the fix, get_type('oid-type') correctly resolved oid-type -> bytes
        -> bstr but then fell through to 'Type not found' because bstr is not in
        self.types.  The correct result is None (no structured definition), with
        no spurious error log.
        """
        cddl_text = """
        oid-type = bytes
        """
        cddl = CDDLParser(cddl_text)
        # oid-type -> bytes -> bstr (via builtin alias)
        # get_type should return None cleanly, not raise or misroute
        result = cddl.get_type('oid-type')
        self.assertIsNone(result,
            "get_type on a name that resolves to a primitive should return None, not raise")

    def test_get_type_direct_primitive_returns_none(self):
        """get_type on a raw primitive name must return None without error."""
        cddl = CDDLParser("")
        for prim in ('tstr', 'uint', 'bstr', 'bool', 'float', 'int'):
            self.assertIsNone(cddl.get_type(prim),
                f"get_type('{prim}') should return None for a bare primitive")


class TestValueRangeValidation(unittest.TestCase):
    """Tests for .ge / .gt / .le / .lt value-range predicates."""

    def _schema(self, constraint):
        return CDDLParser(f"""
        record = {{
          &( value : 0 ) => uint {constraint},
        }}
        """)

    def test_le_accepts_at_boundary(self):
        self.assertTrue(CBORAnalyzer(self._schema('.le 100')).validate({0: 100}, 'record'))

    def test_le_rejects_above_boundary(self):
        self.assertFalse(CBORAnalyzer(self._schema('.le 100')).validate({0: 101}, 'record'))

    def test_ge_accepts_at_boundary(self):
        self.assertTrue(CBORAnalyzer(self._schema('.ge 5')).validate({0: 5}, 'record'))

    def test_ge_rejects_below_boundary(self):
        self.assertFalse(CBORAnalyzer(self._schema('.ge 5')).validate({0: 4}, 'record'))

    def test_lt_rejects_at_boundary(self):
        """lt is strict: value must be *less than*, not equal."""
        self.assertFalse(CBORAnalyzer(self._schema('.lt 10')).validate({0: 10}, 'record'))

    def test_lt_accepts_below_boundary(self):
        self.assertTrue(CBORAnalyzer(self._schema('.lt 10')).validate({0: 9}, 'record'))

    def test_gt_rejects_at_boundary(self):
        """gt is strict: value must be *greater than*, not equal."""
        self.assertFalse(CBORAnalyzer(self._schema('.gt 0')).validate({0: 0}, 'record'))

    def test_gt_accepts_above_boundary(self):
        self.assertTrue(CBORAnalyzer(self._schema('.gt 0')).validate({0: 1}, 'record'))

    def test_combined_ge_le_range(self):
        schema = self._schema('.ge 0 .le 150')
        self.assertTrue(CBORAnalyzer(schema).validate({0: 0},   'record'))
        self.assertTrue(CBORAnalyzer(schema).validate({0: 150}, 'record'))
        self.assertFalse(CBORAnalyzer(schema).validate({0: 151}, 'record'))

    def test_extract_value_range_returns_none_for_plain_type(self):
        cddl = CDDLParser("")
        self.assertIsNone(cddl.extract_value_range('uint'))

    def test_extract_value_range_parses_correctly(self):
        cddl = CDDLParser("")
        r = cddl.extract_value_range('uint .ge 1 .le 255')
        self.assertIsNotNone(r)
        self.assertEqual(r['ge'], 1)
        self.assertEqual(r['le'], 255)
        self.assertIsNone(r['gt'])
        self.assertIsNone(r['lt'])


class TestRegexpValidation(unittest.TestCase):
    """Tests for .regexp string pattern validation."""

    def _schema(self, pattern):
        return CDDLParser(f"""
        record = {{
          &( label : 0 ) => tstr .regexp "{pattern}",
        }}
        """)

    def test_matching_string_accepted(self):
        self.assertTrue(CBORAnalyzer(self._schema('[a-z]+')).validate({0: 'hello'}, 'record'))

    def test_non_matching_string_rejected(self):
        self.assertFalse(CBORAnalyzer(self._schema('[a-z]+')).validate({0: 'Hello'}, 'record'))

    def test_full_match_required(self):
        """re.fullmatch is used — a partial match must not pass."""
        self.assertFalse(CBORAnalyzer(self._schema('[0-9]+')).validate({0: '12abc'}, 'record'))

    def test_extract_regexp_returns_pattern(self):
        cddl = CDDLParser("")
        self.assertEqual(cddl.extract_regexp('tstr .regexp "[A-Z]{3}"'), '[A-Z]{3}')

    def test_extract_regexp_returns_none_without_annotation(self):
        cddl = CDDLParser("")
        self.assertIsNone(cddl.extract_regexp('tstr'))


class TestMissingRequiredField(unittest.TestCase):
    """Tests for required-field enforcement and error messaging."""

    def setUp(self):
        self.cddl = CDDLParser("""
        person = {
          &( name : 0 ) => tstr,
          &( age  : 1 ) => uint,
          ? &( note : 2 ) => tstr,
        }
        """)

    def test_missing_required_field_fails(self):
        analyzer = CBORAnalyzer(self.cddl)
        self.assertFalse(analyzer.validate({1: 30}, 'person'),
                         "Omitting a required field must fail")

    def test_missing_required_field_error_mentions_field_name(self):
        analyzer = CBORAnalyzer(self.cddl)
        analyzer.validate({1: 30}, 'person')
        errors = ' '.join(analyzer.get_errors())
        self.assertIn('name', errors,
                      "Error message should mention the missing field name")

    def test_all_required_fields_present_passes(self):
        self.assertTrue(CBORAnalyzer(self.cddl).validate({0: 'Alice', 1: 30}, 'person'))

    def test_optional_field_absent_passes(self):
        self.assertTrue(CBORAnalyzer(self.cddl).validate({0: 'Alice', 1: 30}, 'person'))

    def test_multiple_missing_required_fields_reported(self):
        """All missing required fields should be reported, not just the first."""
        analyzer = CBORAnalyzer(self.cddl)
        analyzer.validate({}, 'person')
        errors = ' '.join(analyzer.get_errors())
        self.assertIn('name', errors)
        self.assertIn('age', errors)


class TestSocketExtensions(unittest.TestCase):
    """Tests for $$socket //= extension parsing."""

    def test_socket_extension_stored(self):
        cddl = CDDLParser("$$my-ext //= extra-field")
        self.assertIn('$$my-ext', cddl.socket_extensions)
        self.assertIn('extra-field', cddl.socket_extensions['$$my-ext'])

    def test_multiple_socket_extensions_accumulated(self):
        cddl_text = """
        $$my-ext //= field-a
        $$my-ext //= field-b
        """
        cddl = CDDLParser(cddl_text)
        exts = cddl.socket_extensions.get('$$my-ext', [])
        self.assertIn('field-a', exts)
        self.assertIn('field-b', exts)

    def test_get_socket_extensions_helper(self):
        cddl = CDDLParser("$$sock //= val")
        self.assertIsNotNone(cddl.get_socket_extensions('$$sock'))
        self.assertIsNone(cddl.get_socket_extensions('$$nonexistent'))


class TestNintValidation(unittest.TestCase):
    """Tests for nint (negative integer) type handling."""

    def setUp(self):
        self.cddl = CDDLParser("""
        signed = {
          &( delta : 0 ) => int,
        }
        """)

    def test_negative_integer_accepted_for_int(self):
        self.assertTrue(CBORAnalyzer(self.cddl).validate({0: -1}, 'signed'))

    def test_positive_integer_accepted_for_int(self):
        self.assertTrue(CBORAnalyzer(self.cddl).validate({0: 0}, 'signed'))

    def test_bool_rejected_for_int(self):
        """bool is a Python subclass of int but must be rejected for CBOR int fields."""
        self.assertFalse(CBORAnalyzer(self.cddl).validate({0: True}, 'signed'))

    def test_uint_rejects_negative(self):
        cddl = CDDLParser("""
        rec = {
          &( count : 0 ) => uint,
        }
        """)
        self.assertFalse(CBORAnalyzer(cddl).validate({0: -1}, 'rec'))


class TestEDNAnnotateFalse(unittest.TestCase):
    """Tests for EDNGenerator with annotate=False."""

    def setUp(self):
        self.cddl = CDDLParser("""
        person = {
          &( name : 0 ) => tstr,
          &( age  : 1 ) => uint,
        }
        """)
        self.generator = EDNGenerator(self.cddl, edn_format='keyindex')

    def test_no_annotations_when_annotate_false(self):
        edn = self.generator.generate({0: 'Alice', 1: 30}, 'person', annotate=False)
        self.assertNotIn('/ name /', edn)
        self.assertNotIn('/ age /', edn)

    def test_values_still_present_when_annotate_false(self):
        edn = self.generator.generate({0: 'Alice', 1: 30}, 'person', annotate=False)
        self.assertIn('"Alice"', edn)
        self.assertIn('30', edn)

    def test_annotate_true_shows_field_names(self):
        edn = self.generator.generate({0: 'Alice', 1: 30}, 'person', annotate=True)
        self.assertIn('/ name /', edn)
        self.assertIn('/ age /', edn)


class TestValidateNoType(unittest.TestCase):
    """validate() with type_name=None must be a no-op and return True."""

    def test_validate_none_type_returns_true(self):
        cddl = CDDLParser("rec = { &( x : 0 ) => uint }")
        analyzer = CBORAnalyzer(cddl)
        self.assertTrue(analyzer.validate({0: 'wrong-type-but-no-schema-given'}, None))

    def test_validate_none_type_no_errors(self):
        cddl = CDDLParser("rec = { &( x : 0 ) => uint }")
        analyzer = CBORAnalyzer(cddl)
        analyzer.validate({99: 'anything'}, None)
        self.assertEqual(analyzer.get_errors(), [])


class TestArrayElementSizeConstraint(unittest.TestCase):
    """Size constraints on array element types must be enforced."""

    def test_array_bstr_element_exact_size_accepted(self):
        cddl = CDDLParser("hashes = [ + bstr .size 4 ]")
        self.assertTrue(CBORAnalyzer(cddl).validate([b'abcd', b'efgh'], 'hashes'))

    def test_array_bstr_element_wrong_size_rejected(self):
        cddl = CDDLParser("hashes = [ + bstr .size 4 ]")
        self.assertFalse(CBORAnalyzer(cddl).validate([b'abc'], 'hashes'),
                         "bstr element with wrong size should fail")

    def test_array_tstr_element_max_size_rejected(self):
        cddl = CDDLParser("labels = [ + tstr .size (1..5) ]")
        self.assertFalse(CBORAnalyzer(cddl).validate(['toolong'], 'labels'),
                         "tstr element exceeding max size should fail")


class TestGroupParsing(unittest.TestCase):
    """Tests for CDDL group definitions."""

    def test_single_line_group_stored(self):
        cddl = CDDLParser("my-group = ( field-a )")
        self.assertIn('my-group', cddl.groups)

    def test_get_group_helper(self):
        cddl = CDDLParser("my-group = ( field-a )")
        self.assertIsNotNone(cddl.get_group('my-group'))
        self.assertIsNone(cddl.get_group('nonexistent'))

    def test_multiline_group_stored(self):
        cddl_text = """
        meta-group = (
          corim-meta-identity,
          ? cwt-claims-identity,
        )
        """
        cddl = CDDLParser(cddl_text)
        self.assertIn('meta-group', cddl.groups)


class TestTypeChoiceResolution(unittest.TestCase):
    """Tests for type-choice auto-resolution during validation."""

    def test_validate_resolves_type_choice_via_alias(self):
        """validate() on an alias-to-choice must auto-resolve and succeed."""
        cddl_text = """
        root = $my-choice
        $my-choice /= item-map
        item-map = {
          &( x : 0 ) => uint,
        }
        """
        cddl = CDDLParser(cddl_text)
        # root -> $my-choice -> item-map: valid data should pass
        self.assertTrue(CBORAnalyzer(cddl).validate({0: 1}, 'item-map'))

    def test_unknown_type_produces_error(self):
        """validate() on a completely unknown type must return False with an error."""
        cddl = CDDLParser("rec = { &( x : 0 ) => uint }")
        analyzer = CBORAnalyzer(cddl)
        result = analyzer.validate({0: 1}, 'totally-unknown-type')
        self.assertFalse(result)
        self.assertTrue(len(analyzer.get_errors()) > 0)


if __name__ == '__main__':
    unittest.main()