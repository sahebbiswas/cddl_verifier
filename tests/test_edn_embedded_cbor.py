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


class TestNonPreferredEmbedded(unittest.TestCase):
    """Embedded CBOR that is not in preferred serialization is shown with encoding indicators (#237)."""

    schema = "r = { &( a : 0 ) => bstr .cbor inner }\ninner = { &( v : 0 ) => float }\n"

    def test_float_wider_than_it_needs(self):
        text = Validator(self.schema).to_edn(encode({0: encode({0: float_width(1.5, 64)})}), root_type="r")
        self.assertIn("<<", text)
        self.assertIn("/ v / 0: 1.5_3", text)
        # the shortest width needs no indicator
        short = Validator(self.schema).to_edn(encode({0: encode({0: 1.5})}), root_type="r")
        self.assertIn("/ v / 0: 1.5\n", short)

    def test_indefinite_length(self):
        schema = "r = { &( a : 0 ) => bstr .cbor [* int] }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("9f0102ff")}), root_type="r")
        self.assertIn("<<", text)
        self.assertIn("[_\n", text)
        self.assertNotIn("h'", text)

    def test_overlong_head(self):
        schema = "r = { &( a : 0 ) => bstr .cbor int }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("1801")}), root_type="r")
        self.assertIn("<< 1_0 >>", text)

    def test_the_notation_is_the_data_again(self):
        # what is written reads back, by RFC 8949 section 8.1, to the very bytes
        cases = ["1801", "190001", "3801", "9f0102ff", "bf0102ff", "9800", "a1" + "01" + "02", "fb3ff8000000000000",
                 "fa3fc00000", "5801aa", "780161", "5fff", "7fff", "7f6161ff", "5f4101ff", "9f9f01ffff",
                 "c11801", "82fb3ff800000000000018ff"]
        for hexed in cases:
            with self.subTest(hexed):
                raw = bytes.fromhex(hexed)
                text = Validator("r = { &( a : 0 ) => bstr .cbor any }").to_edn(encode({0: raw}), root_type="r")
                self.assertIn("<<", text)
                self.assertNotIn("h'" + hexed, text)

    def test_outer_byte_string_head_is_indicated_too(self):
        schema = "r = { &( a : 0 ) => bstr .cbor int }"
        raw = bytes.fromhex("a100580101")
        text = Validator(schema).to_edn(raw, root_type="r")
        self.assertIn(">>_0", text)

    def test_tag_with_embedded_cbor_and_indicators(self):
        schema = "r = #6.506(bytes .cbor inner)\ninner = { &( v : 0 ) => uint }"
        raw = bytes.fromhex("d901fa44" "a1001801")
        text = Validator(schema).to_edn(raw, root_type="r")
        self.assertIn("506(", text)
        self.assertIn("0: 1_0", text)

    def test_indefinite_string_in_embedded_cbor_is_one_chunk(self):
        schema = "r = { &( a : 0 ) => bstr .cbor [* tstr] }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("817f6161ff")}), root_type="r")
        self.assertIn('(_ "a")', text)
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("817fff")}), root_type="r")
        self.assertIn('""_', text)
        schema = "r = { &( a : 0 ) => bstr .cbor [* bstr] }"
        text = Validator(schema).to_edn(encode({0: bytes.fromhex("815f4101ff")}), root_type="r")
        self.assertIn("(_ h'01')", text)

    def test_top_level_items_carry_indicators(self):
        validator = Validator("r = [* any]")
        text = validator.to_edn(bytes.fromhex("9f1801fb3ff8000000000000190001ff"), root_type="r")
        self.assertEqual([line.strip().rstrip(",") for line in text.splitlines()],
                         ["/ r / [_", "1_0", "1.5_3", "1_1", "]"])
        self.assertEqual(validator.to_edn(bytes.fromhex("9f0102ff"), root_type="r").splitlines()[0], "/ r / [_")
        self.assertEqual(validator.to_edn(bytes.fromhex("9fff"), root_type="r"), "[_ ]")
        self.assertEqual(Validator("r = {* any => any}").to_edn(bytes.fromhex("bfff"), root_type="r"), "{_ }")
        self.assertEqual(Validator("r = [* any]").to_edn(bytes.fromhex("9800"), root_type="r"), "[_0 ]")
        self.assertEqual(Validator("r = any").to_edn(bytes.fromhex("7fff")), '""_')
        self.assertEqual(Validator("r = any").to_edn(bytes.fromhex("5fff")), "''_")

    def test_top_level_indefinite_string_has_no_chunk_structure_to_show(self):
        text = Validator("r = any").to_edn(bytes.fromhex("7f61616162ff"))
        self.assertEqual(text, '"ab"')  # the chunks are not recorded

    def test_plain_data_is_unchanged(self):
        text = Validator("r = [* any]").to_edn(bytes.fromhex("831864f93e0063616263"), root_type="r")
        self.assertNotIn("_", text)
        self.assertEqual(Validator("r = any").to_edn(encode(1.5)), "1.5")


class TestNotShownAsEmbedded(unittest.TestCase):
    """What no encoding indicator can say stays a byte string (#237)."""

    def hex_member(self, schema: str, raw: str) -> str:
        text = Validator(schema).to_edn(encode({0: bytes.fromhex(raw)}), root_type="r")
        self.assertNotIn("<<", text)
        return text

    def test_two_chunks_of_an_indefinite_string(self):
        self.assertIn("h'7f61616162ff'",
                      self.hex_member("r = { &( a : 0 ) => bstr .cbor tstr }", "7f61616162ff"))
        self.assertIn("h'5f41014102ff'",
                      self.hex_member("r = { &( a : 0 ) => bstr .cbor bstr }", "5f41014102ff"))

    def test_tag_number_with_a_longer_head(self):
        self.assertIn("h'd8" + "0101'", self.hex_member("r = { &( a : 0 ) => bstr .cbor any }", "d80101"))

    def test_nan_with_a_payload(self):
        self.assertIn("h'fb7ff8000000000001'",
                      self.hex_member("r = { &( a : 0 ) => bstr .cbor float }", "fb7ff8000000000001"))

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
