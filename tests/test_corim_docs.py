"""The CoRIM tag numbers in the bundled sample schema and the docs match the draft (#230).

``cddl-schemas/unified.cddl`` follows draft-ietf-rats-corim; ``corim_test.cddl`` is a
simplified copy for parser tests, and the docs show CoRIM snippets. Both once had 505 and
506 swapped and unwrapped, 556 for a COSE key, and ``.within`` in ``non-empty``.
"""

import re
import unittest
from pathlib import Path

from cddl_verifier import validate
from cddl_verifier._cddl.parser import parse_cddl
from cddl_verifier.cbor import CBORTag, encode

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / 'cddl-schemas'
DOCS = ROOT / 'docs'


def read(path):
    return path.read_text(encoding='utf-8')


def tag_rules(text):
    """``{rule name: (tag number, source of the content type)}`` for each ``name = #6.N(type)``."""
    return {name: (int(number), content.strip())
            for name, number, content in re.findall(
                r'^([\w$-]+)\s*=\s*#6\.(\d+)\((.*?)\)\s*(?:;.*)?$', text, re.M)}


class TestSampleSchema(unittest.TestCase):
    TEST = read(SCHEMAS / 'corim_test.cddl')
    UNIFIED = read(SCHEMAS / 'unified.cddl')

    def test_it_still_parses(self):
        parse_cddl(self.TEST, source_name='corim_test.cddl')

    def test_tag_numbers_and_wrapping_are_the_drafts(self):
        sample, draft = tag_rules(self.TEST), tag_rules(self.UNIFIED)
        for name in ('tagged-concise-swid-tag', 'tagged-concise-mid-tag',
                     'tagged-pkix-base64-key-type', 'tagged-pkix-base64-cert-type',
                     'tagged-pkix-base64-cert-path-type', 'tagged-cose-key-type'):
            self.assertIn(name, sample, name)
            self.assertEqual(sample[name][0], draft[name][0], name)
        self.assertEqual((sample['tagged-concise-swid-tag'][0], sample['tagged-concise-mid-tag'][0]),
                         (505, 506))
        for name in ('tagged-concise-swid-tag', 'tagged-concise-mid-tag'):
            self.assertTrue(sample[name][1].startswith('bytes .cbor '), name)

    def test_non_empty_uses_and_like_the_draft(self):
        self.assertIn('.and ({ + any => any })', self.TEST)
        self.assertNotIn('.within', self.TEST)

    def test_the_tags_wrap_encoded_cbor(self):
        text = self.TEST
        self.assertTrue(validate(text, encode(CBORTag(505, encode({0: 'x'}))),
                                 root_type='tagged-concise-swid-tag').valid)
        self.assertFalse(validate(text, encode(CBORTag(505, {0: 'x'})),
                                  root_type='tagged-concise-swid-tag').valid)
        self.assertFalse(validate(text, encode(CBORTag(506, {0: 'x'})),
                                  root_type='tagged-concise-mid-tag').valid)
        self.assertFalse(validate(text, encode(CBORTag(505, encode({0: 'x'}))),
                                  root_type='tagged-concise-mid-tag').valid)
        self.assertTrue(validate(text, encode(CBORTag(558, 1)),
                                 root_type='tagged-cose-key-type').valid)
        self.assertFalse(validate(text, encode(CBORTag(556, 1)),
                                  root_type='tagged-cose-key-type').valid)


class TestDocs(unittest.TestCase):
    def test_support_doc_snippet_is_valid_cddl_with_the_drafts_tags(self):
        text = read(DOCS / 'CORIM_SUPPORT.md')
        section = text[text.index('### ✓ CBOR Tags'):]
        block = re.search(r'```cddl\n(.*?)```', section, re.S).group(1)
        parse_cddl(block + '\ncomid-map = any\ncoswid-map = any\nuuid-type = bstr .size 16\n')
        rules = tag_rules(block)
        draft = tag_rules(read(SCHEMAS / 'unified.cddl'))
        for name, (number, content) in rules.items():
            self.assertEqual(number, draft[name][0], name)
        self.assertEqual(rules['tagged-concise-swid-tag'][0], 505)
        self.assertEqual(rules['tagged-concise-mid-tag'][0], 506)
        self.assertTrue(rules['tagged-concise-mid-tag'][1].startswith('bytes .cbor'))

    def test_canonical_doc_example_runs_and_follows_the_draft(self):
        text = read(DOCS / 'CANONICAL_AND_JSON.md')
        section = text[text.index('### Complete Example: CoRIM Workflow'):]
        code = re.search(r'```python\n(.*?)```', section, re.S).group(1)
        from cddl_verifier.cbor import cbor_decode, cbor_encode
        from cddl_verifier.json_codec import cbor_to_json
        import os
        import tempfile
        scope = {'CBORTag': CBORTag, 'cbor_encode': cbor_encode, 'cbor_to_json': cbor_to_json,
                 'sign_data': lambda data, key: b'sig', 'private_key': None}
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                exec(code, scope)  # noqa: S102 - the documentation example itself
                signed = cbor_decode(Path('corim.cbor').read_bytes())
            finally:
                os.chdir(here)
        self.assertEqual(signed.tag, 18)                 # signed-corim is a COSE_Sign1
        protected = cbor_decode(signed.value[0])
        self.assertEqual(protected[1], -7)                              # alg
        self.assertEqual(protected[3], 'application/rim+cbor')          # content type
        self.assertEqual(cbor_decode(protected[8]), {0: {0: 'Example Signer'}})  # corim-meta
        unsigned = cbor_decode(signed.value[2])
        self.assertEqual(unsigned.tag, 501)
        member = unsigned.value[1][0]
        self.assertEqual(member.tag, 506)
        self.assertIsInstance(member.value, bytes)       # bytes .cbor
        comid = cbor_decode(member.value)
        self.assertIn(1, comid)                          # tag-identity
        self.assertEqual(comid[1][0], 'comid-001')
        self.assertTrue(comid[4][0])                     # triples is not empty
        self.assertEqual(sorted(unsigned.value), [0, 1])  # integer keys


if __name__ == '__main__':
    unittest.main()
