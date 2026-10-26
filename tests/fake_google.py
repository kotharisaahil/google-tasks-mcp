"""Stateful in-memory Google API stand-in for behavior and protocol tests."""

import copy
import json
from datetime import datetime
from unittest.mock import Mock

import httplib2
from googleapiclient.errors import HttpError


def http_error(status, reason="backendError", retry_after=None):
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    return HttpError(
        httplib2.Response(headers),
        json.dumps(
            {"error": {"message": "SENSITIVE_RESPONSE_BODY", "errors": [{"reason": reason}]}}
        ).encode(),
    )


class FakeRequest:
    def __init__(self, action):
        self.action = action

    def execute(self, num_retries=0):
        assert num_retries == 0
        return copy.deepcopy(self.action())


class FakeService:
    def __init__(self):
        self.lists = {
            "L1": {"id": "L1", "title": "Inbox"},
            "L2": {"id": "L2", "title": "Inbox"},
        }
        self.data = {
            "L1": [
                {
                    "id": "T1",
                    "title": "Write proposal",
                    "notes": "Project ALPHA",
                    "status": "needsAction",
                    "due": "2026-10-26T00:00:00.000Z",
                },
                {
                    "id": "T2",
                    "title": "Finished",
                    "status": "completed",
                    "hidden": True,
                    "completed": "2026-10-25T10:00:00Z",
                },
                {"id": "T3", "title": "Buy milk", "status": "needsAction"},
            ],
            "L2": [
                {
                    "id": "T4",
                    "title": "Book travel",
                    "status": "needsAction",
                    "due": "2026-11-01T00:00:00.000Z",
                }
            ],
        }
        self.counter = 10
        self.list_resource, self.task_resource = Mock(), Mock()
        self.tasklists = Mock(return_value=self.list_resource)
        self.tasks = Mock(return_value=self.task_resource)
        for resource, name, action in [
            (self.list_resource, "list", self._lists),
            (self.list_resource, "get", self._get_list),
            (self.list_resource, "insert", self._insert_list),
            (self.list_resource, "patch", self._patch_list),
            (self.list_resource, "delete", self._delete_list),
            (self.task_resource, "list", self._tasks),
            (self.task_resource, "get", self._get_task),
            (self.task_resource, "insert", self._insert_task),
            (self.task_resource, "patch", self._patch_task),
            (self.task_resource, "move", self._move_task),
            (self.task_resource, "delete", self._delete_task),
            (self.task_resource, "clear", self._clear),
        ]:
            getattr(resource, name).side_effect = self._factory(action)

    @staticmethod
    def _factory(action):
        return lambda **kwargs: FakeRequest(lambda: action(**kwargs))

    @staticmethod
    def _page(items, maxResults=100, pageToken=None):
        start = int(pageToken or 0)
        result = {"items": items[start : start + maxResults]}
        if start + maxResults < len(items):
            result["nextPageToken"] = str(start + maxResults)
        return result

    def _lists(self, **kwargs):
        return self._page(list(self.lists.values()), **kwargs)

    def _get_list(self, tasklist):
        if tasklist not in self.lists:
            raise http_error(404)
        return self.lists[tasklist]

    def _tasks(
        self,
        tasklist,
        maxResults=100,
        pageToken=None,
        showCompleted=False,
        showHidden=False,
        showDeleted=False,
        dueMin=None,
        dueMax=None,
    ):
        self._get_list(tasklist)
        items = [
            task
            for task in self.data[tasklist]
            if (showCompleted or task["status"] != "completed")
            and (showHidden or not task.get("hidden"))
            and (showDeleted or not task.get("deleted"))
            and (
                not dueMin
                or (task.get("due") and datetime.fromisoformat(task["due"]) >= datetime.fromisoformat(dueMin))
            )
            and (
                not dueMax
                # Reproduce the calendar-day upper-bound behavior observed live.
                or (
                    task.get("due")
                    and datetime.fromisoformat(task["due"]).date() < datetime.fromisoformat(dueMax).date()
                )
            )
        ]
        return self._page(items, maxResults=maxResults, pageToken=pageToken)

    def _get_task(self, tasklist, task):
        self._get_list(tasklist)
        for item in self.data[tasklist]:
            if item["id"] == task:
                return item
        raise http_error(404)

    def _insert_task(self, tasklist, body, parent=None, previous=None):
        self._get_list(tasklist)
        self.counter += 1
        item = {"id": f"T{self.counter}", "status": "needsAction", **body}
        if parent:
            item["parent"] = parent
        self.data[tasklist].append(item)
        return item

    def _patch_task(self, tasklist, task, body):
        item = self._get_task(tasklist, task)
        for name, value in body.items():
            if value is None:
                item.pop(name, None)
            else:
                item[name] = value
        return item

    def _move_task(self, tasklist, task, destinationTasklist=None, parent=None, previous=None):
        item = self._get_task(tasklist, task)
        if destinationTasklist:
            self._get_list(destinationTasklist)
            self.data[tasklist].remove(item)
            self.data[destinationTasklist].append(item)
        item.pop("parent", None)
        if parent:
            item["parent"] = parent
        return item

    def _delete_task(self, tasklist, task):
        self.data[tasklist].remove(self._get_task(tasklist, task))
        return {}

    def _clear(self, tasklist):
        self._get_list(tasklist)
        for task in self.data[tasklist]:
            if task["status"] == "completed":
                task["hidden"] = True
        return {}

    def _insert_list(self, body):
        self.counter += 1
        item = {"id": f"L{self.counter}", **body}
        self.lists[item["id"]] = item
        self.data[item["id"]] = []
        return item

    def _patch_list(self, tasklist, body):
        self._get_list(tasklist).update(body)
        return self.lists[tasklist]

    def _delete_list(self, tasklist):
        self._get_list(tasklist)
        del self.lists[tasklist]
        del self.data[tasklist]
        return {}
