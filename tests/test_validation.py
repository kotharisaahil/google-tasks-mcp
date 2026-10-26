import unittest

from gtasks_mcp import validation
from gtasks_mcp.errors import TasksError


class ValidationTests(unittest.TestCase):
    def test_calendar_dates_reject_impossible_dates_and_times(self):
        for value in [
            "2026-02-29",
            "2026-02-30",
            "20261026",
            "2026-W44-1",
            "2026-10-26T14:00:00Z",
            "tomorrow",
        ]:
            with self.subTest(value=value):
                with self.assertRaises(TasksError):
                    validation.calendar_date(value)
        self.assertEqual(validation.due_timestamp("2028-02-29"), "2028-02-29T00:00:00.000Z")

    def test_date_range_validation(self):
        self.assertEqual(validation.date_range(None, None), (None, None))
        with self.assertRaisesRegex(TasksError, "INVALID_DATE_RANGE"):
            validation.date_range("2026-11-01", "2026-10-26")

    def test_titles_notes_and_identifiers(self):
        for operation in [
            lambda: validation.title(" "),
            lambda: validation.title("a" * 1025),
            lambda: validation.notes("a" * 8193),
            lambda: validation.identifier("a\nb"),
        ]:
            with self.assertRaises(TasksError):
                operation()
        self.assertEqual(validation.notes(""), "")
        self.assertEqual(validation.identifier(" L1 "), "L1")

    def test_bad_timezone_returns_safe_error(self):
        with self.assertRaisesRegex(TasksError, "INVALID_TIMEZONE"):
            validation.timezone("not/a/real/timezone")
        self.assertEqual(validation.timezone("America/Los_Angeles").key, "America/Los_Angeles")


if __name__ == "__main__":
    unittest.main()
