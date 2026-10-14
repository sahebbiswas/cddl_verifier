"""Arrays written inside arrays and choices are checked element by element (#196)."""

import unittest

from cddl_verifier import validate

BAD = bytes.fromhex("8282010282036178")      # [[1, 2], [3, "x"]]
GOOD = bytes.fromhex("8282010282030 4".replace(" ", ""))  # [[1, 2], [3, 4]]


def paths(schema, data, root="r"):
    result = validate(schema, data, root)
    return result.valid, [d.path_expr for d in result.diagnostics]


class TestNestedArrays(unittest.TestCase):
    def test_array_of_arrays(self):
        self.assertEqual(paths("r = [* [* uint]]", GOOD), (True, []))
        self.assertEqual(paths("r = [* [* uint]]", BAD), (False, ["$[1][1]"]))

    def test_positional_arrays(self):
        self.assertEqual(paths("r = [ [* uint], [* uint] ]", GOOD), (True, []))
        self.assertEqual(paths("r = [ [* uint], [* uint] ]", BAD), (False, ["$[1][1]"]))

    def test_three_levels(self):
        data = bytes.fromhex("81 81 82 01 61 78".replace(" ", ""))  # [[[1, "x"]]]
        self.assertEqual(paths("r = [* [* [* uint]]]", data), (False, ["$[0][0][1]"]))
        good = bytes.fromhex("81 81 82 01 02".replace(" ", ""))
        self.assertEqual(paths("r = [* [* [* uint]]]", good), (True, []))

    def test_in_a_map_member(self):
        schema = "r = { 1: [* [* uint]] }"
        self.assertEqual(paths(schema, bytes.fromhex("a101") + GOOD), (True, []))
        self.assertEqual(paths(schema, bytes.fromhex("a101") + BAD), (False, ["$<key 1>[1][1]"]))

    def test_array_of_arrays_of_maps(self):
        data = bytes.fromhex("81 81 a1 6161 6178".replace(" ", ""))  # [[{"a": "x"}]]
        self.assertEqual(paths("r = [* [* { a: uint }]]", data), (False, ["$[0][0].a"]))

    def test_named_arrays_still_work(self):
        schema = "row = [* uint]\nr = [* row]"
        self.assertEqual(paths(schema, GOOD), (True, []))
        self.assertEqual(paths(schema, BAD), (False, ["$[1][1]"]))

    def test_offsets_of_nested_failures(self):
        diag = validate("r = [* [* uint]]", BAD).diagnostics[0]
        self.assertEqual((diag.offset, diag.end_offset), (6, 8))


class TestArrayAlternatives(unittest.TestCase):
    BAD_ITEM = bytes.fromhex("82036178")  # [3, "x"]

    def test_choice_with_an_array(self):
        for schema in ("r = uint / [* uint]", "r = uint\nr /= [* uint]"):
            with self.subTest(schema):
                self.assertTrue(validate(schema, bytes.fromhex("05"), "r").valid)
                self.assertTrue(validate(schema, bytes.fromhex("820102"), "r").valid)
                self.assertFalse(validate(schema, self.BAD_ITEM, "r").valid)

    def test_choice_in_a_map_member(self):
        schema = "r = { 1: uint / [* uint] }"
        self.assertTrue(validate(schema, bytes.fromhex("a1010 5".replace(" ", "")), "r").valid)
        self.assertTrue(validate(schema, bytes.fromhex("a101820102"), "r").valid)
        self.assertFalse(validate(schema, bytes.fromhex("a101") + self.BAD_ITEM, "r").valid)

    def test_choice_as_an_element(self):
        for schema in ("r = [* (uint / [* uint])]", "r = [* m]\nm = uint / [* uint]"):
            with self.subTest(schema):
                ok = bytes.fromhex("83 01 82 02 03 04".replace(" ", ""))
                self.assertTrue(validate(schema, ok, "r").valid)
                bad = bytes.fromhex("81") + self.BAD_ITEM
                result = validate(schema, bad, "r")
                self.assertFalse(result.valid)
                self.assertEqual(result.diagnostics[0].path_expr, "$[0]")


class TestSequenceMatcherPaths(unittest.TestCase):
    """Errors of arrays matched entry by entry point at the element."""

    def test_element_that_matches_no_entry(self):
        data = bytes.fromhex("83 01 61 78 05".replace(" ", ""))  # [1, "x", 5]
        result = validate("r = [uint, * tstr]", data, "r")
        diag = result.diagnostics[0]
        self.assertEqual(diag.path_expr, "$[2]")
        self.assertEqual((diag.offset, diag.end_offset), (4, 5))

    def test_unexpected_element(self):
        data = bytes.fromhex("83 01 02 03".replace(" ", ""))
        diag = validate("r = [uint, ? uint]", data, "r").diagnostics[0]
        self.assertEqual(diag.path_expr, "$[2]")
        self.assertEqual(diag.offset, 3)

    def test_failure_inside_the_element_keeps_its_path_and_offset(self):
        schema = "r = [ ? uint, [* uint] ]"
        cases = (("81 81 61 78", "$[0][0]", 2),           # [["x"]]
                 ("82 01 81 61 78", "$[1][0]", 3))        # [1, ["x"]]
        for hexed, path, offset in cases:
            with self.subTest(path):
                diag = validate(schema, bytes.fromhex(hexed.replace(" ", "")), "r").diagnostics[0]
                self.assertEqual((diag.path_expr, diag.offset), (path, offset))

    def test_parenthesized_arrays(self):
        bad = bytes.fromhex("82036178")
        self.assertTrue(validate("r = uint / ([* uint])", bytes.fromhex("820102"), "r").valid)
        self.assertFalse(validate("r = uint / ([* uint])", bad, "r").valid)
        result = validate("r = [* ([* uint])]", bytes.fromhex("81") + bad, "r")
        self.assertFalse(result.valid)
        self.assertEqual(result.diagnostics[0].path_expr, "$[0][1]")
        self.assertTrue(validate("r = [* ([* uint])]", bytes.fromhex("81820102"), "r").valid)

    def test_missing_element_stays_on_the_array(self):
        diag = validate("r = [uint, ? uint, tstr]", bytes.fromhex("8101"), "r").diagnostics[0]
        self.assertEqual(diag.path_expr, "$")


if __name__ == "__main__":
    unittest.main()
