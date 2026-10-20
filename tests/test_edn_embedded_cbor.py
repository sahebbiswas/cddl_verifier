"""EDN for embedded CBOR: ``bstr .cbor T`` and ``bstr .cborseq`` in << >> (#228, RFC 8610 Appendix G.3)."""

import unittest
import warnings
from pathlib import Path

from cddl_verifier import Validator
from cddl_verifier.cbor import CBORTag, encode, float_width

ROOT = Path(__file__).resolve().parent.parent

INNER = "inner = { &( alg : 1 ) => int }\n"


def edn(schema: str, value, root: str = "r") -> str:
    return Validator(schema).to_edn(encode(value), root_type=root)


class TestUntaggedEmbeddedCBOR(unittest.TestCase):
    """A byte string that is not under a tag is expanded like a tagged one."""

    def test_map_member(self):
        schema = "outer = { &( hdr : 0 ) => bstr .cbor inner }\n" + INNER
        self.assertEqual(
            edn(schema, {0: encode({1: -7})}, "outer"),
            "/ outer / {\n"
            "  / hdr / 0: <<\n"
            "    / inner / {\n"
            "      / alg / 1: -7\n"
            "    }\n"
            "  >>\n"
            "}")

    def test_array_elements_and_choices(self):
        schema = "r = [bstr .cbor inner, bstr .cbor inner / nil, bstr .cbor inner / nil]\n" + INNER
        text = edn(schema, [encode({1: 1}), encode({1: 2}), None])
        self.assertEqual(text.count("<<"), 2)
        self.assertIn("/ alg / 1: 1", text)
        self.assertIn("/ alg / 1: 2", text)
        self.assertTrue(text.rstrip().endswith("null\n]"))

    def test_uniform_array(self):
        text = edn("r = [* bstr .cbor inner]\n" + INNER, [encode({1: 1}), encode({1: 2})])
        self.assertEqual(text.count("<<"), 2)
        self.assertEqual(text.count("/ inner /"), 2)

    def test_root_rule(self):
        self.assertEqual(
            edn("r = bstr .cbor inner\n" + INNER, encode({1: 5})),
            "<<\n  / inner / {\n    / alg / 1: 5\n  }\n>>")

    def test_root_rule_that_is_a_choice(self):
        schema = "r = bstr .cbor inner / nil\n" + INNER
        self.assertIn("/ alg / 1: 5", edn(schema, encode({1: 5})))
        self.assertEqual(edn(schema, None), "null")

    def test_named_alias_of_the_embedded_type(self):
        schema = "r = { &( h : 0 ) => wrapped }\nwrapped = bstr .cbor inner\n" + INNER
        text = edn(schema, {0: encode({1: 1})})
        self.assertIn("/ h / 0: <<", text)
        self.assertIn("/ alg / 1: 1", text)

    def test_nested_embedded_cbor(self):
        schema = ("r = { &( a : 0 ) => bstr .cbor mid }\n"
                  "mid = { &( b : 0 ) => bstr .cbor leaf }\n"
                  "leaf = { &( c : 0 ) => int }\n")
        text = edn(schema, {0: encode({0: encode({0: 5})})})
        self.assertEqual(text.count("<<"), 2)
        self.assertEqual(text.count(">>"), 2)
        self.assertIn("/ c / 0: 5", text)

    def test_item_that_is_not_a_map(self):
        self.assertEqual(edn("r = bstr .cbor int", encode(7)), "<< 7 >>")
        self.assertEqual(edn("r = { a: bstr .cbor [* int] }", {"a": encode([1, 2])}),
                         '/ r / {\n  "a": <<\n    [\n      1,\n      2\n    ]\n  >>\n}')

    def test_other_byte_strings_stay_hex(self):
        self.assertEqual(edn("r = { a: bstr }", {"a": b"\x01\x02"}), '/ r / {\n  "a": h\'0102\'\n}')
        self.assertEqual(edn("r = any", b"\x01"), "h'01'")


class TestNotation(unittest.TestCase):
    """The notation is RFC 8610 Appendix G.3's, not a project-specific wrapper."""

    schema = "r = #6.506(bytes .cbor inner)\n" + INNER

    def test_tagged_embedded_cbor(self):
        self.assertEqual(
            edn(self.schema, CBORTag(506, encode({1: 42}))),
            "/ r / 506(\n"
            "  <<\n"
            "    / inner / {\n"
            "      / alg / 1: 42\n"
            "    }\n"
            "  >>\n"
            ")")

    def test_single_line_item_stays_on_the_tag_line(self):
        self.assertEqual(edn("r = #6.24(bstr .cbor int)", CBORTag(24, encode(9))), "/ r / 24(<< 9 >>)")

    def test_tag_24_with_a_map(self):
        text = edn("r = #6.24(bstr .cbor inner)\n" + INNER, CBORTag(24, encode({1: 9})))
        self.assertIn("24(\n  <<", text)
        self.assertIn("/ alg / 1: 9", text)

    def test_no_bytes_wrapper_anywhere(self):
        for text in (edn(self.schema, CBORTag(506, encode({1: 42}))),
                     edn("r = [bstr .cbor inner]\n" + INNER, [encode({1: 1})])):
            self.assertNotIn("bytes<", text)

    def test_annotation_can_be_turned_off(self):
        # without annotation the item is still decoded, only the comments go
        text = Validator("r = bstr .cbor inner\n" + INNER).to_edn(
            encode(encode({1: 1})), root_type="r", annotate=False)
        self.assertEqual(text, "<<\n  {\n    1: 1\n  }\n>>")


class TestSequences(unittest.TestCase):
    """``bstr .cborseq`` holds a CBOR sequence (RFC 8742), written ``<< a, b >>``."""

    def test_flat_sequence(self):
        self.assertEqual(edn("r = bstr .cborseq [* int]", encode(1) + encode(2)), "<< 1, 2 >>")

    def test_empty_sequence(self):
        self.assertEqual(edn("r = bstr .cborseq [* int]", b""), "<<>>")

    def test_items_take_the_type_of_the_array(self):
        schema = "r = { &( a : 0 ) => bstr .cborseq [* inner] }\ninner = { &( v : 0 ) => int }\n"
        text = edn(schema, {0: encode({0: 1}) + encode({0: 2})})
        self.assertEqual(text.count("/ inner /"), 2)
        self.assertIn("/ v / 0: 1", text)
        self.assertIn("/ v / 0: 2", text)
        self.assertIn("},\n", text)

    def test_positional_items(self):
        schema = "r = bstr .cborseq [inner, tstr]\ninner = { &( v : 0 ) => int }\n"
        text = edn(schema, encode({0: 1}) + encode("x"))
        self.assertIn("/ inner /", text)
        self.assertTrue(text.rstrip().endswith('"x"\n>>'))


class TestNotShownAsEmbedded(unittest.TestCase):
    """Bytes that << >> would not encode back to are shown as a byte string, not altered."""

    schema = "r = { &( a : 0 ) => bstr .cbor inner }\ninner = { &( v : 0 ) => float }\n"

    def hex_member(self, raw: bytes) -> str:
        text = Validator(self.schema).to_edn(encode({0: raw}), root_type="r")
        self.assertNotIn("<<", text)
        return text

    def test_float_wider_than_it_needs(self):
        with_double = encode({0: float_width(1.5, 64)})
        self.assertIn("h'" + with_double.hex() + "'", self.hex_member(with_double))
        # the shortest width is shown
        self.assertIn("<<", Validator(self.schema).to_edn(encode({0: encode({0: 1.5})}), root_type="r"))

    def test_indefinite_length(self):
        schema = "r = { &( a : 0 ) => bstr .cbor [* int] }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("9f0102ff")}), root_type="r")
        self.assertIn("h'9f0102ff'", text)

    def test_overlong_head(self):
        schema = "r = { &( a : 0 ) => bstr .cbor int }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("1801")}), root_type="r")
        self.assertIn("h'1801'", text)

    def test_not_cbor(self):
        schema = "r = { &( a : 0 ) => bstr .cbor int }"
        for raw in ("ff", "0102", ""):
            with self.subTest(raw=raw):
                text = Validator(schema).to_edn(encode({0: bytes.fromhex(raw)}), root_type="r")
                self.assertEqual(text, f"/ r / {{\n  / a / 0: h'{raw}'\n}}")

    def test_trailing_bytes(self):
        schema = "r = { &( a : 0 ) => bstr .cbor int }"
        text = Validator(schema).to_edn(encode({0: encode(1) + encode(2)}), root_type="r")
        self.assertIn("h'0102'", text)


class TestAliasToInlineChoice(unittest.TestCase):
    """``r = a / b`` is resolved to the alternative the data matches, as ``$socket`` choices are."""

    def test_root_alias_of_a_choice(self):
        schema = "r = m1 / m2\nm1 = { &( a : 0 ) => int }\nm2 = { &( b : 1 ) => tstr }\n"
        text = edn(schema, {1: "x"})
        self.assertIn("/ m2 /", text)
        self.assertIn("/ b / 1:", text)

    def test_member_alias_of_a_choice(self):
        schema = "r = { &( f : 0 ) => c }\nc = bstr .cbor inner / nil\n" + INNER
        self.assertIn("/ alg / 1: 1", edn(schema, {0: encode({1: 1})}))
        self.assertIn("null", edn(schema, {0: None}))


class TestSignedCoRIM(unittest.TestCase):
    """COSE-Sign1-corim: the protected header, corim-meta and the payload are decoded."""

    def test_signed_corim(self):
        payload = (ROOT / "test-data" / "minimal-corim.cbor").read_bytes()
        protected = encode({1: -7, 3: "application/rim+cbor",
                            8: encode({0: {0: "Example Signer"}})})
        signed = encode(CBORTag(18, [protected, {}, payload, b"\x01\x02"]))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the schema imports modules that are not supplied
            text = Validator(ROOT / "cddl-schemas" / "unified.cddl").to_edn(
                signed, root_type="signed-corim")
        for expected in ("/ protected-corim-header-map-inline / {", "/ alg / 1: -7",
                         "/ corim-meta / 8: <<", "/ corim-meta-map / {", "/ signer-name / 0:",
                         "/ tagged-unsigned-corim-map / 501(", "/ concise-mid-tag / {",
                         "h'0102'"):
            self.assertIn(expected, text)
        self.assertNotIn("bytes<", text)
        # the payload and the protected header are << >>, the signature is not
        self.assertEqual(text.count("<<"), text.count(">>"))
        self.assertGreaterEqual(text.count("<<"), 4)


if __name__ == "__main__":
    unittest.main()
