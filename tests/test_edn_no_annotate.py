"""``annotate=False`` prints the same items as the default, without the comments (#236).

The member types decide what is decoded (embedded CBOR in ``<< >>``, a choice resolved to
its alternative), so they are looked up with annotation off as well; only the
``/ name /`` comments, the type headers and the member names go.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator
from cddl_verifier.cbor import CBORTag, encode

SCHEMA = """
r = { &( hdr : 0 ) => bstr .cbor inner, ? &( opt : 1 ) => bstr .cbor inner / nil,
      ? &( list : 2 ) => [* bstr .cbor inner], ? &( tagged : 3 ) => wrapped,
      ? &( pick : 4 ) => shape, ? &( seq : 5 ) => bstr .cborseq [* uint] }
inner = { &( alg : 1 ) => int }
wrapped = #6.506(bytes .cbor inner)
shape = point / label
point = [x: uint, y: uint]
label = { &( text : 0 ) => tstr }
pair = [bstr .cbor inner, uint]
tagged-map = #6.501(r)
$choice /= #6.506(bytes .cbor inner)
$choice /= uint
chosen = [* $choice]
"""

COMMENT = re.compile(r"/ [^/\n]* /")


def plain(text):
    """*text* without its comments, whitespace collapsed."""
    return " ".join(COMMENT.sub("", text).split())


class NoAnnotate(unittest.TestCase):
    validator = Validator(SCHEMA)

    def same_without_comments(self, value, root, **options):
        data = encode(value)
        annotated = self.validator.to_edn(data, root_type=root, **options)
        bare = self.validator.to_edn(data, root_type=root, annotate=False, **options)
        self.assertEqual(plain(annotated), plain(bare), (annotated, bare))
        self.assertNotRegex(bare, r"/ [^/\n]* /")
        return annotated, bare

    def test_embedded_cbor_in_a_map_member(self):
        _, bare = self.same_without_comments({0: encode({1: -7})}, "r")
        self.assertIn("<<", bare)
        self.assertNotIn("h'", bare)

    def test_issue_example_unchanged(self):
        v = Validator("r = { &( hdr : 0 ) => bstr .cbor inner }\ninner = { &( alg : 1 ) => int }")
        raw = encode({0: encode({1: -7})})
        self.assertIn("<<", v.to_edn(raw, root_type="r", annotate=False))

    def test_optional_member_choice_with_nil(self):
        self.same_without_comments({0: encode({1: 1}), 1: encode({1: 2})}, "r")
        self.same_without_comments({0: encode({1: 1}), 1: None}, "r")

    def test_array_of_embedded_cbor(self):
        _, bare = self.same_without_comments({0: encode({1: 1}), 2: [encode({1: 2}), encode({1: 3})]}, "r")
        self.assertEqual(bare.count("<<"), 3)

    def test_tag_around_embedded_cbor(self):
        _, bare = self.same_without_comments({0: encode({1: 1}), 3: CBORTag(506, encode({1: 4}))}, "r")
        self.assertIn("506(", bare)
        self.assertEqual(bare.count("<<"), 2)

    def test_tagged_root(self):
        self.same_without_comments(CBORTag(501, {0: encode({1: 1})}), "tagged-map")

    def test_socket_choice_in_an_array(self):
        _, bare = self.same_without_comments([CBORTag(506, encode({1: 9})), 5], "chosen")
        self.assertIn("<<", bare)

    def test_structured_array_root(self):
        self.same_without_comments([encode({1: 1}), 7], "pair")

    def test_group_choice_member_resolves_by_shape(self):
        self.same_without_comments({0: encode({1: 1}), 4: [1, 2]}, "r")
        self.same_without_comments({0: encode({1: 1}), 4: {0: "t"}}, "r")

    def test_cborseq(self):
        self.same_without_comments({0: encode({1: 1}), 5: encode(1) + encode(2)}, "r")

    def test_every_edn_format_prints_the_keyindex_text_without_annotation(self):
        # keyname and both put the member names in the keys, which annotation off drops
        data = encode({0: encode({1: 1})})
        expected = self.validator.to_edn(data, root_type="r", annotate=False)
        for edn_format in ("keyname", "both"):
            with self.subTest(edn_format):
                bare = self.validator.to_edn(data, root_type="r", annotate=False, edn_format=edn_format)
                self.assertEqual(bare, expected)
                self.assertIn("0:", bare)
                self.assertNotIn("hdr", bare)

    def test_plain_data_without_a_type_is_unchanged(self):
        data = encode({0: [1, "a", b"\x01"]})
        self.assertEqual(self.validator.to_edn(data, root_type="r", annotate=False).count("h'01'"), 1)


if __name__ == "__main__":
    unittest.main()
