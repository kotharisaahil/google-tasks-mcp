import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError

from gtasks_mcp.client import TasksClient, _execute
from gtasks_mcp.errors import TasksError
from tests.fake_google import FakeService, http_error


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.factory = Mock(return_value=self.service)
        self.client = TasksClient(self.factory)

    def test_list_pagination_does_not_drop_duplicate_names(self):
        first = self.client.list_task_lists(page_size=1)
        second = self.client.list_task_lists(page_size=1, page_token=first.next_page_token)
        self.assertEqual([first.task_lists[0].id, second.task_lists[0].id], ["L1", "L2"])
        self.assertIsNone(second.next_page_token)

    def test_task_pagination(self):
        first = self.client.list_tasks("L1", page_size=1)
        second = self.client.list_tasks("L1", page_size=1, page_token=first.next_page_token)
        self.assertEqual([first.tasks[0].id, second.tasks[0].id], ["T1", "T3"])
        self.assertIsNone(second.next_page_token)

    def test_completed_tasks_include_hidden_items(self):
        result = self.client.list_tasks("L1", include_completed=True)
        self.assertIn("T2", [task.id for task in result.tasks])
        parameters = self.service.task_resource.list.call_args.kwargs
        self.assertTrue(parameters["showCompleted"])
        self.assertTrue(parameters["showHidden"])

    def test_list_omits_notes_by_default_get_includes_them(self):
        self.assertIsNone(self.client.list_tasks("L1").tasks[0].notes)
        self.assertEqual(self.client.get_task("L1", "T1").notes, "Project ALPHA")

    def test_empty_list(self):
        self.service.data["L1"] = []
        result = self.client.list_tasks("L1")
        self.assertEqual(result.tasks, [])
        self.assertIsNone(result.next_page_token)

    def test_date_bounds_are_inclusive_and_output_is_date_only(self):
        result = self.client.list_tasks("L1", due_after="2026-10-26", due_before="2026-10-26")
        self.assertEqual([task.id for task in result.tasks], ["T1"])
        self.assertEqual(result.tasks[0].due, "2026-10-26")

    def test_upper_date_padding_handles_leap_day_and_maximum_date(self):
        self.client.list_tasks("L1", due_before="2028-02-29")
        self.assertEqual(self.service.task_resource.list.call_args.kwargs["dueMax"], "2028-03-01T00:00:00Z")
        result = self.client.list_tasks("L1", due_before="9999-12-31")
        self.assertNotIn("dueMax", self.service.task_resource.list.call_args.kwargs)
        self.assertEqual([task.id for task in result.tasks], ["T1"])

    def test_empty_filtered_page_keeps_continuation(self):
        self.service.task_resource.list.return_value = None
        self.service.task_resource.list.side_effect = None
        self.service.task_resource.list.return_value = Mock()
        self.service.task_resource.list.return_value.execute.return_value = {
            "items": [{"id": "X", "title": "Outside range", "due": "2026-10-27T00:00:00Z"}],
            "nextPageToken": "more",
        }
        result = self.client.list_tasks("L1", due_before="2026-10-26")
        self.assertEqual(result.tasks, [])
        self.assertEqual(result.next_page_token, "more")

    def test_bad_ids_and_dates_fail_before_authorization(self):
        for operation in [
            lambda: self.client.list_task_lists(page_size=0),
            lambda: self.client.list_tasks("   "),
            lambda: self.client.list_tasks("L1", page_size=0),
            lambda: self.client.list_tasks("L1", due_after="2026-02-30"),
            lambda: self.client.list_tasks("L1", due_after="2026-11-01", due_before="2026-10-01"),
            lambda: self.client.get_task("L1", "\n"),
        ]:
            with self.subTest(operation=operation):
                with self.assertRaises(TasksError):
                    operation()
        self.factory.assert_not_called()

    def test_search_checks_notes_case_insensitively_and_returns_compact_results(self):
        result = self.client.search_tasks("alpha")
        self.assertEqual([match.task.id for match in result.matches], ["T1"])
        self.assertIsNone(result.matches[0].task.notes)
        self.assertFalse(result.truncated)
        self.assertEqual(result.scanned_lists, 2)

    def test_search_follows_pages(self):
        self.service.data["L1"] = [
            {"id": f"T{i}", "title": "Match", "status": "needsAction"} for i in range(101)
        ]
        result = self.client.search_tasks("Match", task_list_id="L1", limit=100)
        self.assertEqual(result.scanned_tasks, 101)
        self.assertEqual(len(result.matches), 100)
        self.assertTrue(result.truncated)
        self.assertEqual(self.service.task_resource.list.call_count, 2)

    def test_search_reports_task_and_list_budgets(self):
        result = self.client.search_tasks("travel", max_tasks=1)
        self.assertEqual(result.scanned_tasks, 1)
        self.assertTrue(result.truncated)
        result = self.client.search_tasks("travel", max_lists=1)
        self.assertTrue(result.truncated)
        self.assertEqual(result.matches, [])

    def test_exact_scan_budget_is_complete_when_no_more_tasks_exist(self):
        result = self.client.search_tasks("proposal", task_list_id="L1", max_tasks=2)
        self.assertFalse(result.truncated)

    def test_empty_pages_are_bounded_and_repeated_tokens_fail(self):
        request = Mock()
        self.service.task_resource.list.side_effect = lambda **kwargs: request
        request.execute.side_effect = [{"nextPageToken": str(index)} for index in range(101)]
        result = self.client.search_tasks("anything", task_list_id="L1")
        self.assertTrue(result.truncated)
        self.assertEqual(request.execute.call_count, 100)
        request.execute.reset_mock(side_effect=True)
        request.execute.return_value = {"nextPageToken": "same"}
        with self.assertRaisesRegex(TasksError, "PAGINATION_ERROR"):
            self.client.search_tasks("anything", task_list_id="L1")

    def test_digest_uses_supplied_timezone_and_calendar_boundaries(self):
        self.service.data["L1"] = [
            {"id": key, "title": key, "status": "needsAction", **({"due": due + "T00:00:00Z"} if due else {})}
            for key, due in [
                ("past", "2026-10-24"),
                ("today", "2026-10-25"),
                ("next", "2026-10-26"),
                ("last", "2026-11-01"),
                ("outside", "2026-11-02"),
                ("undated", None),
            ]
        ]
        # 02:00 UTC is still the previous calendar day in Los Angeles.
        frozen = datetime(2026, 10, 26, 2, tzinfo=timezone.utc)
        with patch("gtasks_mcp.client.datetime") as clock:
            clock.now.side_effect = lambda zone: frozen.astimezone(zone)
            result = self.client.get_task_digest("America/Los_Angeles", task_list_id="L1")
        self.assertEqual(result.today, "2026-10-25")
        self.assertEqual([match.task.id for match in result.overdue], ["past"])
        self.assertEqual([match.task.id for match in result.due_today], ["today"])
        self.assertEqual([match.task.id for match in result.upcoming], ["next", "last"])
        self.assertEqual([match.task.id for match in result.undated], ["undated"])

    def test_create_normalizes_date_and_uses_query_parameters_for_parent(self):
        result = self.client.create_task("L1", " New task ", notes="", due="2028-02-29", parent_id="T1")
        parameters = self.service.task_resource.insert.call_args.kwargs
        self.assertEqual(parameters["body"]["due"], "2028-02-29T00:00:00.000Z")
        self.assertEqual(parameters["body"]["notes"], "")
        self.assertEqual(parameters["parent"], "T1")
        self.assertNotIn("parent", parameters["body"])
        self.assertEqual(result.task.title, "New task")
        self.factory.assert_called_once_with(write=True)

    def test_patch_preserves_notes_and_clears_only_requested_fields(self):
        self.client.update_task("L1", "T1", title="Changed")
        self.assertEqual(self.service.data["L1"][0]["notes"], "Project ALPHA")
        self.assertEqual(self.service.task_resource.patch.call_args.kwargs["body"], {"title": "Changed"})
        result = self.client.update_task("L1", "T1", notes="", clear_due=True)
        self.assertIsNone(result.task.due)
        self.assertEqual(result.task.notes, "")

    def test_invalid_updates_fail_before_google(self):
        for arguments in [
            {},
            {"title": " "},
            {"due": "2026-02-30"},
            {"due": "2026-10-26", "clear_due": True},
        ]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(TasksError):
                    self.client.update_task("L1", "T1", **arguments)
        self.factory.assert_not_called()

    def test_completion_and_reopening_preserve_other_fields(self):
        self.client.set_completed("L1", "T1", True)
        self.assertEqual(self.service.task_resource.patch.call_args.kwargs["body"], {"status": "completed"})
        self.service.data["L1"][0]["completed"] = "2026-10-26T00:00:00Z"
        result = self.client.set_completed("L1", "T1", False)
        self.assertEqual(result.task.notes, "Project ALPHA")
        self.assertIsNone(result.task.completed)

    def test_cross_list_move_is_native_without_copy_or_delete(self):
        result = self.client.move_task("L1", "T1", destination_list_id="L2")
        self.assertEqual(result.task_list_id, "L2")
        self.service.task_resource.insert.assert_not_called()
        self.service.task_resource.delete.assert_not_called()
        self.assertEqual(self.service.task_resource.move.call_args.kwargs["destinationTasklist"], "L2")

    def test_task_cannot_be_its_own_parent_or_previous(self):
        for arguments in [{"parent_id": "T1"}, {"previous_id": "T1"}]:
            with self.assertRaises(TasksError):
                self.client.move_task("L1", "T1", **arguments)
        self.factory.assert_not_called()

    def test_deletion_needs_confirmation_and_exact_title(self):
        with self.assertRaisesRegex(TasksError, "CONFIRMATION_REQUIRED"):
            self.client.delete_task("L1", "T1", "Write proposal")
        self.factory.assert_not_called()
        with self.assertRaisesRegex(TasksError, "TITLE_MISMATCH"):
            self.client.delete_task("L1", "T1", "Wrong", confirm=True)
        self.service.task_resource.delete.assert_not_called()
        self.assertTrue(self.client.delete_task("L1", "T1", "Write proposal", confirm=True).deleted)

    def test_delete_nonempty_list_includes_hidden_completed_tasks(self):
        self.service.data["L1"] = [self.service.data["L1"][1]]
        with self.assertRaisesRegex(TasksError, "LIST_NOT_EMPTY"):
            self.client.delete_task_list("L1", "Inbox", confirm=True)
        self.service.list_resource.delete.assert_not_called()
        self.assertTrue(
            self.client.delete_task_list("L1", "Inbox", confirm=True, allow_nonempty=True).deleted
        )

    def test_create_rename_and_delete_empty_list(self):
        created = self.client.create_task_list("Sandbox")
        renamed = self.client.rename_task_list(created.id, "Renamed")
        self.assertEqual(renamed.title, "Renamed")
        self.assertTrue(self.client.delete_task_list(created.id, "Renamed", confirm=True).deleted)

    def test_clear_hides_completed_tasks_without_deleting(self):
        before = len(self.service.data["L1"])
        with self.assertRaisesRegex(TasksError, "CONFIRMATION_REQUIRED"):
            self.client.clear_completed_tasks("L1")
        result = self.client.clear_completed_tasks("L1", confirm=True)
        self.assertTrue(result.cleared)
        self.assertEqual(len(self.service.data["L1"]), before)
        self.service.task_resource.delete.assert_not_called()


class RequestFailureTests(unittest.TestCase):
    def test_google_rejected_refresh_during_request_returns_auth_instruction(self):
        request = Mock()
        request.execute.side_effect = RefreshError("FAKE_PRIVATE_OAUTH_DETAILS")
        with self.assertRaisesRegex(TasksError, "AUTH_REQUIRED") as caught:
            _execute(request)
        self.assertNotIn("FAKE_PRIVATE_OAUTH_DETAILS", str(caught.exception))
        self.assertEqual(request.execute.call_count, 1)

    def test_retry_after_http_date_is_respected(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        request = Mock()
        request.execute.side_effect = http_error(
            429, retry_after=format_datetime(now + timedelta(seconds=120), usegmt=True)
        )
        with patch("gtasks_mcp.client.datetime") as clock:
            clock.now.return_value = now
            with patch("gtasks_mcp.client.time.sleep") as sleep:
                with self.assertRaisesRegex(TasksError, "RATE_LIMITED"):
                    _execute(request)
        sleep.assert_not_called()
        self.assertEqual(request.execute.call_count, 1)

    def test_transient_network_reads_are_bounded(self):
        request = Mock()
        request.execute.side_effect = OSError("FAKE_PRIVATE_URL")
        with patch("gtasks_mcp.client.time.sleep"):
            with self.assertRaisesRegex(TasksError, "NETWORK_ERROR") as caught:
                _execute(request)
        self.assertEqual(request.execute.call_count, 3)
        self.assertNotIn("FAKE_PRIVATE_URL", str(caught.exception))

    def test_transient_reads_retry_twice(self):
        request = Mock()
        request.execute.side_effect = [http_error(503), http_error(429), {"items": []}]
        with patch("gtasks_mcp.client.time.sleep") as sleep:
            self.assertEqual(_execute(request), {"items": []})
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(request.execute.call_count, 3)
        request.execute.assert_called_with(num_retries=0)

    def test_writes_are_not_replayed_after_server_or_network_failure(self):
        for failure in [http_error(503), TimeoutError("SENSITIVE_RESPONSE_BODY")]:
            with self.subTest(failure=type(failure).__name__):
                request = Mock()
                request.execute.side_effect = failure
                with self.assertRaisesRegex(TasksError, "WRITE_OUTCOME_UNKNOWN"):
                    _execute(request, write=True)
                self.assertEqual(request.execute.call_count, 1)

    def test_http_errors_are_actionable_and_sanitized(self):
        for status, code in [
            (400, "GOOGLE_BAD_REQUEST"),
            (401, "AUTH_REQUIRED"),
            (403, "ACCESS_DENIED"),
            (404, "NOT_FOUND"),
            (409, "CONFLICT"),
            (412, "CONFLICT"),
            (429, "RATE_LIMITED"),
        ]:
            with self.subTest(status=status):
                request = Mock()
                request.execute.side_effect = http_error(status)
                with patch("gtasks_mcp.client.time.sleep"):
                    with self.assertRaises(TasksError) as caught:
                        _execute(request)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("SENSITIVE_RESPONSE_BODY", str(caught.exception))

    def test_permission_failures_do_not_retry(self):
        request = Mock()
        request.execute.side_effect = http_error(403, "insufficientPermissions")
        with self.assertRaisesRegex(TasksError, "ACCESS_DENIED"):
            _execute(request)
        self.assertEqual(request.execute.call_count, 1)

    def test_rate_limit_reason_on_403_can_retry(self):
        request = Mock()
        request.execute.side_effect = [http_error(403, "userRateLimitExceeded"), {}]
        with patch("gtasks_mcp.client.time.sleep"):
            self.assertEqual(_execute(request), {})
        self.assertEqual(request.execute.call_count, 2)

    def test_long_retry_after_is_reported_instead_of_ignored(self):
        request = Mock()
        request.execute.side_effect = http_error(429, retry_after=120)
        with patch("gtasks_mcp.client.time.sleep") as sleep:
            with self.assertRaisesRegex(TasksError, "RATE_LIMITED"):
                _execute(request)
        sleep.assert_not_called()
        self.assertEqual(request.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
