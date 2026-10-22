"""Valid data logs no errors for the ways of reading it that were only tried (#254).

A group choice, an optional group or a choice of types is tried an alternative at a time; the
ones that do not match are no error when another does. They are debug lines.
"""

import logging
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))

from cddl_verifier import Validator
from cddl_verifier._log import logger, trying
from cddl_verifier.cbor import encode


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class Logging(unittest.TestCase):
    def run_at(self, level, schema, value, root="r"):
        handler, old = Capture(), logger.level
        logger.addHandler(handler)
        logger.setLevel(level)
        try:
            result = Validator(schema).validate(encode(value), root_type=root)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)
        return result, handler.records

    def problems(self, records):
        return [r.getMessage() for r in records if r.levelno >= logging.WARNING]


class TestValidData(Logging):
    GROUP = "r = { a: int, ? (b: int, ? c: int) }"

    def test_an_optional_group_left_out_logs_nothing(self):
        result, records = self.run_at(logging.WARNING, self.GROUP, {"a": 1})
        self.assertTrue(result.valid)
        self.assertEqual(self.problems(records), [])

    def test_an_optional_group_taken_logs_nothing(self):
        for value in ({"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}):
            result, records = self.run_at(logging.WARNING, self.GROUP, value)
            self.assertTrue(result.valid)
            self.assertEqual(self.problems(records), [])

    def test_a_group_choice_logs_nothing_for_the_alternative_not_taken(self):
        schema = "r = { a: int, (b: int // c: tstr) }"
        for value in ({"a": 1, "b": 2}, {"a": 1, "c": "x"}):
            result, records = self.run_at(logging.WARNING, schema, value)
            self.assertTrue(result.valid)
            self.assertEqual(self.problems(records), [])

    def test_a_choice_of_types_logs_nothing_for_the_type_not_taken(self):
        result, records = self.run_at(logging.WARNING, "r = { x: int / tstr, y: [* (int / bool)] }",
                                      {"x": "s", "y": [1, True]})
        self.assertTrue(result.valid)
        self.assertEqual(self.problems(records), [])

    def test_the_rejected_alternatives_are_debug_lines(self):
        result, records = self.run_at(logging.DEBUG, self.GROUP, {"a": 1})
        self.assertTrue(result.valid)
        self.assertEqual(self.problems(records), [])
        missing = [r for r in records if "Missing required field 'b'" in r.getMessage()]
        self.assertTrue(missing)
        self.assertTrue(all(r.levelno == logging.DEBUG and r.levelname == "DEBUG" for r in missing))


class TestInvalidData(Logging):
    def test_the_failure_is_logged_and_reported_once(self):
        result, records = self.run_at(logging.WARNING, TestValidData.GROUP, {"a": "x"})
        self.assertFalse(result.valid)
        self.assertTrue(any("'a'" in str(e) and "expected int" in str(e) for e in result.errors))
        logged = self.problems(records)
        self.assertFalse(any("Missing required field 'b'" in m for m in logged))

    def test_a_real_missing_field_is_still_logged(self):
        result, records = self.run_at(logging.WARNING, "r = { a: int, b: int }", {"a": 1})
        self.assertFalse(result.valid)
        self.assertTrue(any("Missing required field 'b'" in m for m in self.problems(records)))

    def test_the_closest_alternative_reports_its_errors(self):
        result, records = self.run_at(logging.WARNING, "r = { a: int, (b: int // c: tstr) }", {"a": 1, "b": "x"})
        self.assertFalse(result.valid)
        self.assertTrue(result.errors)

    def test_a_failed_choice_of_types_is_reported(self):
        result, records = self.run_at(logging.WARNING, "r = { x: int / tstr }", {"x": 1.5})
        self.assertFalse(result.valid)
        self.assertTrue(any("matches none of int / tstr" in m for m in self.problems(records)))


class TestTrying(unittest.TestCase):
    def test_it_is_per_thread_and_ends_with_the_block(self):
        handler = Capture()
        logger.addHandler(handler)
        old = logger.level
        logger.setLevel(logging.WARNING)
        try:
            seen = []

            def other():
                logger.error("other thread")
                seen.append(True)

            with trying():
                thread = threading.Thread(target=other)
                thread.start()
                thread.join()
                logger.error("in the trial")
            logger.error("after")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)
        self.assertEqual([r.getMessage() for r in handler.records], ["other thread", "after"])

    def test_it_nests(self):
        handler = Capture()
        logger.addHandler(handler)
        old = logger.level
        logger.setLevel(logging.WARNING)
        try:
            with trying():
                with trying():
                    logger.error("inner")
                logger.error("outer")
            logger.error("none")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)
        self.assertEqual([r.getMessage() for r in handler.records], ["none"])


if __name__ == "__main__":
    unittest.main()
