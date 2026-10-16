"""Tag 36 content must be a MIME message (#187)."""

import random
import sys
import time
import unittest
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cddl_verifier import validate
from cddl_verifier._cddl import mime
from cddl_verifier._cddl.tagcontent import CHECKED_TAGS, content_error
from cddl_verifier.cbor import CBORTag


def bad(text):
    return mime.message_error(text)


def multipart(boundary, parts, close=True, header="Content-Type: multipart/mixed; boundary=%s"):
    out = (header % (boundary if boundary.isalnum() else '"%s"' % boundary)) + "\r\n\r\n"
    for part in parts:
        out += f"--{boundary}\r\n{part}\r\n"
    return out + (f"--{boundary}--\r\n" if close else "")


class Accepted(unittest.TestCase):
    CASES = {
        "the example of the issue": "Content-Type: text/plain\r\n\r\nhi",
        "headers only, no blank line": "Subject: hi",
        "bare LF": "Subject: hi\n\nbody\n",
        "an empty body": "Subject: hi\r\n\r\n",
        "a folded field": "Subject: a long\r\n subject that\r\n\tcontinues\r\n\r\nbody",
        "whitespace before the colon (obsolete)": "Subject : hi\r\n\r\nx",
        "an empty field value": "Subject:\r\n\r\nx",
        "non-ASCII in a header (RFC 6532)": "Subject: café\r\n\r\nx",
        "MIME-Version": "MIME-Version: 1.0\r\n\r\nx",
        "MIME-Version with a comment": "MIME-Version: 1.0 (produced by hand)\r\n\r\nx",
        "MIME-Version in any case": "mime-version: 1.0\r\n\r\nx",
        "Content-Type with a parameter": 'Content-Type: text/plain; charset="utf-8"; format=flowed\r\n\r\nx',
        "Content-Type with a trailing semicolon": "Content-Type: text/plain;\r\n\r\nx",
        "Content-Type with a comment": "Content-Type: text/plain (the body) ; charset=us-ascii\r\n\r\nx",
        "Content-Type with a folded parameter": "Content-Type: text/plain;\r\n charset=us-ascii\r\n\r\nx",
        "a parenthesis in a quoted parameter": 'Content-Type: text/plain; name="a(b"\r\n\r\nx',
        "an extension type": "Content-Type: application/x-thing+json\r\n\r\nx",
        "Content-Transfer-Encoding": "Content-Transfer-Encoding: base64\r\n\r\nYQ==",
        "an unknown encoding token": "Content-Transfer-Encoding: x-uuencode\r\n\r\nx",
        "a body that looks like a header": "Subject: a\r\n\r\nNot-A-Header\r\nBad line without colon",
        "a full message": ("From: a@example.org\r\nTo: b@example.org\r\nDate: Thu, 15 Oct 2026 20:00:00 -0600\r\n"
                           "Subject: hi\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           "Content-Transfer-Encoding: 7bit\r\n\r\nHello\r\n"),
        "a multipart message": multipart("XX", ["Content-Type: text/plain\r\n\r\nhi", "\r\nno headers"]),
        "a quoted boundary": multipart("a:b", ["\r\nplain"]),
        "a preamble and an epilogue": ("Content-Type: multipart/mixed; boundary=XX\r\n\r\nthe preamble\r\n"
                                       "--XX\r\n\r\nx\r\n--XX--\r\nthe epilogue\r\n--XX\r\nbad: but ignored"),
        "transport padding after a delimiter": ("Content-Type: multipart/mixed; boundary=XX\r\n\r\n"
                                                "--XX  \t\r\n\r\nx\r\n--XX--  \r\n"),
        "a part that has only headers": multipart("XX", ["Content-Type: text/plain"]),
        "an empty part": multipart("XX", [""]),
        "a delimiter-like line of a longer boundary": ("Content-Type: multipart/mixed; boundary=XX\r\n\r\n"
                                                      "--XX\r\n\r\n--XXY is text, not a delimiter\r\n--XX--"),
    }

    def test_cases(self):
        for name, text in self.CASES.items():
            with self.subTest(name):
                self.assertIsNone(bad(text), text)
                self.assertIsNone(content_error(36, text))

    def test_nested_multiparts(self):
        inner = multipart("IN", ["Content-Type: text/html\r\n\r\n<p>"], header="Content-Type: multipart/alternative; boundary=%s")
        outer = multipart("OUT", [inner.rstrip("\r\n")])
        self.assertIsNone(bad(outer))


class Refused(unittest.TestCase):
    CASES = {
        "the example of the issue": ("x", "not a header field"),
        "empty": ("", "empty"),
        "only whitespace": (" \r\n\r\n", "empty"),
        "a blank line first": ("\r\nSubject: x", "no header field"),
        "a continuation first": (" x: y\r\n\r\nb", "continues a header field"),
        "no colon": ("Subject hi\r\n\r\nx", "not a header field"),
        "a space in the name": ("Sub ject: hi\r\n\r\nx", "not a header field"),
        "a bad line after a field": ("A: b\r\nnot a field\r\n\r\nx", "line 2 is not a header field"),
        "a NUL": ("Subject: a\x00b\r\n\r\nx", "NUL"),
        "a bare CR in a header": ("Subject: a\rb\r\n\r\nx", "bare CR"),
        "a line over 998 bytes": ("Subject: " + "a" * 990 + "\r\n\r\nx", "longer than 998"),
        "a line over 998 bytes (UTF-8)": ("Subject: " + "é" * 500 + "\r\n\r\nx", "longer than 998"),
        "MIME-Version 2.0": ("MIME-Version: 2.0\r\n\r\nx", "not 1.0"),
        "MIME-Version 1": ("MIME-Version: 1\r\n\r\nx", "not 1.0"),
        "MIME-Version twice": ("MIME-Version: 1.0\r\nMIME-Version: 1.0\r\n\r\nx", "appears twice"),
        "Content-Type without a subtype": ("Content-Type: text\r\n\r\nx", "not 'type/subtype'"),
        "Content-Type with a space in the type": ("Content-Type: te xt/plain\r\n\r\nx", "not 'type/subtype'"),
        "Content-Type with a bad parameter": ("Content-Type: text/plain; charset\r\n\r\nx", "not 'type/subtype'"),
        "Content-Type with an unquoted special": ("Content-Type: text/plain; a=b/c\r\n\r\nx", "not 'type/subtype'"),
        "Content-Type twice": ("Content-Type: text/plain\r\nContent-Type: text/html\r\n\r\nx", "appears twice"),
        "an unclosed comment": ("MIME-Version: 1.0 (oops\r\n\r\nx", "not closed"),
        "an unclosed quote": ('Content-Type: text/plain; a="x\r\n\r\nx', "not closed"),
        "two encodings": ("Content-Transfer-Encoding: 7bit\r\nContent-Transfer-Encoding: 8bit\r\n\r\nx", "appears twice"),
        "an encoding that is not a token": ("Content-Transfer-Encoding: 7 bit\r\n\r\nx", "not one token"),
        "a multipart without a boundary": ("Content-Type: multipart/mixed\r\n\r\nx", "needs a boundary"),
        "an empty boundary": ('Content-Type: multipart/mixed; boundary=""\r\n\r\nx', "boundary"),
        "a boundary of 71 characters": (multipart("a" * 71, ["\r\nx"]), "1 to 70"),
        "a boundary ending in a space": ('Content-Type: multipart/mixed; boundary="ab "\r\n\r\n--ab \r\n\r\nx\r\n--ab --', "space"),
        "a boundary with a bad character": ('Content-Type: multipart/mixed; boundary="a@b"\r\n\r\nx', "permitted set"),
        "a multipart without parts": ("Content-Type: multipart/mixed; boundary=XX\r\n\r\njust text", "no part"),
        "a multipart without the closing delimiter": (multipart("XX", ["\r\nx"], close=False), "closing delimiter"),
        "a bad part": (multipart("XX", ["Content-Type: text\r\n\r\nx"]), "part 1: Content-Type"),
        "the second part is bad": (multipart("XX", ["\r\nfine", "not a field\r\n\r\nx"]), "part 2: line 1 is not a header field"),
    }

    def test_cases(self):
        for name, (text, reason) in self.CASES.items():
            with self.subTest(name):
                problem = bad(text)
                self.assertIsNotNone(problem, text)
                self.assertIn(reason, problem)
                self.assertIn("tag 36", content_error(36, text))

    def test_nesting_is_limited(self):
        def nest(levels):
            text = "Content-Type: text/plain\r\n\r\nx"
            for i in range(levels):
                text = (f"Content-Type: multipart/mixed; boundary=B{i}\r\n\r\n--B{i}\r\n" + text +
                        f"\r\n--B{i}--\r\n")
            return text
        self.assertIsNone(bad(nest(mime.MAX_DEPTH)))
        self.assertIn("nested more than", bad(nest(mime.MAX_DEPTH + 1)))


class InTheTagCheck(unittest.TestCase):
    def test_tag_36_is_checked(self):
        self.assertIn(36, CHECKED_TAGS)
        self.assertIsNone(content_error(36, "Content-Type: text/plain\r\n\r\nhi"))
        self.assertIsNotNone(content_error(36, "x"))

    def test_content_of_another_type_is_left_to_the_type_check(self):
        for content in (5, b"Subject: x", None, ["Subject: x"]):
            self.assertIsNone(content_error(36, content))

    def test_a_long_message_is_cut_in_the_message(self):
        message = content_error(36, "Subject" + "x" * 5000)
        self.assertLess(len(message), 300)
        self.assertIn("...", message)

    def test_the_prelude_type_uses_it(self):
        good = CBORTag(36, "Content-Type: text/plain\r\n\r\nhi")
        for schema, data, valid in (
                ("r = mime-message", good, True),
                ("r = mime-message", CBORTag(36, "x"), False),
                ("r = mime-message", CBORTag(36, 5), False),
                ("r = mime-message", "x", False),
                ("r = { m: mime-message }", {"m": good}, True),
                ("r = { m: mime-message }", {"m": CBORTag(36, "x")}, False),
                ("r = [* mime-message]", [good, good], True),
                ("r = [* mime-message]", [good, CBORTag(36, "x")], False),
                ("r = #6.36(tstr)", CBORTag(36, "x"), False),
                ("r = mime-message / uint", 5, True)):
            with self.subTest(schema=schema, data=data):
                self.assertEqual(validate(schema, data).valid, valid)

    def test_the_error_names_the_tag_and_the_problem(self):
        result = validate("r = mime-message", CBORTag(36, "MIME-Version: 2.0\r\n\r\nx"))
        self.assertFalse(result.valid)
        self.assertIn("tag 36 content", str(result.errors[0]))
        self.assertIn("MIME-Version", str(result.errors[0]))


class Robustness(unittest.TestCase):
    def test_nothing_raises_and_the_answer_is_stable(self):
        rng = random.Random(187)
        pieces = ["Content-Type: ", "multipart/mixed", "; boundary=", "XX", "\r\n", "\n", "--XX", "--", " ", "\t",
                  "(", ")", '"', "\\", ":", "a", "text/plain", "MIME-Version: 1.0", "\x00", "\r", "é"]
        for _ in range(3000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
            first = mime.message_error(text)
            self.assertEqual(mime.message_error(text), first)

    def test_large_and_hostile_input_is_fast(self):
        started = time.monotonic()
        cases = [
            "Content-Type: a/b" + " " * 200000 + "!\n\nx",
            "Content-Type: a/b" + "; a=b" * 30000 + " x\n\nx",
            "Subject: x\n\n" + "line\n" * 200000,
            "".join(f"X-{i}: v\n" for i in range(50000)) + "\nbody",
            "Content-Type: multipart/mixed; boundary=B\n\n" + "--B\n\npart\n" * 20000 + "--B--\n",
            "MIME-Version: " + "(" * 100000 + ")" * 100000 + "1.0\n\nx",
            'Content-Type: a/b; c="' + "\\\\" * 100000 + "\n\nx",
        ]
        for text in cases:
            mime.message_error(text)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
