"""Verify request construction against Google's real bundled discovery schema, offline."""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import httplib2
from googleapiclient.discovery import build

from gtasks_mcp.client import TasksClient


class RecordingHttp:
    def __init__(self):
        self.calls = []
        self.payload = {"id": "T1", "title": "Example", "status": "needsAction"}

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        self.calls.append((uri, method, json.loads(body) if body else None))
        return httplib2.Response({"status": "200", "content-type": "application/json"}), json.dumps(
            self.payload
        ).encode()


class GoogleContractTests(unittest.TestCase):
    def setUp(self):
        self.http = RecordingHttp()
        service = build("tasks", "v1", http=self.http, cache_discovery=False, static_discovery=True)
        self.client = TasksClient(lambda **kwargs: service)

    def test_insert_uses_real_google_parameter_names_and_json_body(self):
        self.client.create_task("L1", "Example", due="2028-02-29", parent_id="P1", previous_id="P2")
        uri, method, body = self.http.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(parse_qs(urlparse(uri).query)["parent"], ["P1"])
        self.assertEqual(parse_qs(urlparse(uri).query)["previous"], ["P2"])
        self.assertEqual(body, {"title": "Example", "due": "2028-02-29T00:00:00.000Z"})

    def test_partial_update_serializes_null_without_other_fields(self):
        self.client.update_task("L1", "T1", clear_due=True)
        self.assertEqual(self.http.calls[-1][1:], ("PATCH", {"due": None}))

    def test_native_move_parameter_exists_in_real_google_schema(self):
        self.client.move_task("L1", "T1", destination_list_id="L2")
        self.assertEqual(len(self.http.calls), 1)
        uri, method, body = self.http.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(urlparse(uri).path.endswith("/move"))
        self.assertEqual(parse_qs(urlparse(uri).query)["destinationTasklist"], ["L2"])
        self.assertIsNone(body)

    def test_list_parameters_match_real_google_schema(self):
        self.http.payload = {"items": []}
        self.client.list_tasks("L1", include_completed=True, due_after="2026-10-26", due_before="2026-10-26")
        uri, method, body = self.http.calls[-1]
        query = parse_qs(urlparse(uri).query)
        self.assertEqual(query["showCompleted"], ["true"])
        self.assertEqual(query["showHidden"], ["true"])
        self.assertEqual(query["dueMin"], ["2026-10-26T00:00:00Z"])
        self.assertEqual(query["dueMax"], ["2026-10-27T00:00:00Z"])
        self.assertEqual(method, "GET")
        self.assertIsNone(body)

    def test_list_rename_and_clear_have_correct_http_methods(self):
        self.client.rename_task_list("L1", "Changed")
        self.assertEqual(self.http.calls[-1][1:], ("PATCH", {"title": "Changed"}))
        self.http.payload = {}
        self.client.clear_completed_tasks("L1", confirm=True)
        self.assertEqual(self.http.calls[-1][1:], ("POST", None))


if __name__ == "__main__":
    unittest.main()
