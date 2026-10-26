"""Google Tasks operations, independent of MCP and interactive authentication."""

import json
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from gtasks_mcp import validation as validate
from gtasks_mcp.auth import get_tasks_service
from gtasks_mcp.errors import TasksError
from gtasks_mcp.models import (
    ClearResult,
    DeleteResult,
    DigestResult,
    MutationResult,
    SearchResult,
    Task,
    TaskList,
    TaskListPage,
    TaskMatch,
    TaskPage,
)


def _reason(error: HttpError) -> str:
    try:
        return json.loads(error.content).get("error", {}).get("errors", [{}])[0].get("reason", "")
    except (ValueError, TypeError, AttributeError, IndexError):
        return ""


def _retry_after(value) -> float:
    try:
        delay = float(value)
        return max(0, delay) if math.isfinite(delay) else float("inf")
    except (ValueError, TypeError):
        try:
            retry_date = parsedate_to_datetime(str(value))
            if retry_date.tzinfo is None:
                retry_date = retry_date.replace(tzinfo=timezone.utc)
            return max(0, (retry_date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


def _execute(request, *, write: bool = False) -> dict[str, Any]:
    """Retry transient reads twice. Never automatically replay a mutation."""
    for attempt in range(3):
        try:
            return request.execute(num_retries=0) or {}
        except HttpError as error:
            status = error.resp.status
            throttled = status == 429 or (
                status == 403 and _reason(error) in {"rateLimitExceeded", "userRateLimitExceeded"}
            )
            transient = throttled or status in {500, 502, 503, 504}
            if not write and transient and attempt < 2:
                delay = _retry_after(error.resp.get("retry-after", 0))
                # Long server-directed waits are reported rather than ignored or slept through.
                if 0 <= delay <= 8:
                    time.sleep(max(delay, 2**attempt + random.uniform(0, 0.25)))
                    continue
            if write and status >= 500:
                raise TasksError(
                    "WRITE_OUTCOME_UNKNOWN",
                    "Google returned a server error during a write. Read the affected list before retrying; the change may have succeeded.",
                ) from None
            if throttled:
                raise TasksError(
                    "RATE_LIMITED", "Google's request limit was reached. Wait before trying again."
                ) from None
            messages = {
                400: (
                    "GOOGLE_BAD_REQUEST",
                    "Google rejected the operation. Check the IDs, dates, and task hierarchy; assigned or repeating tasks have additional restrictions.",
                ),
                401: (
                    "AUTH_REQUIRED",
                    "Google rejected the authorization. Run python auth.py --force, adding --write if needed.",
                ),
                403: (
                    "ACCESS_DENIED",
                    "Google denied access. Check the Tasks API is enabled, OAuth permissions were granted, and your account administrator permits access.",
                ),
                404: (
                    "NOT_FOUND",
                    "The task or task list no longer exists or is not accessible to this account.",
                ),
                409: ("CONFLICT", "Google reported a conflict. Read the task again before retrying."),
                412: ("CONFLICT", "The resource changed. Read it again before retrying."),
            }
            code, message = messages.get(
                status, ("GOOGLE_UNAVAILABLE", "Google Tasks is unavailable. Try again later.")
            )
            raise TasksError(code, message) from None
        except RefreshError:
            raise TasksError(
                "AUTH_REQUIRED",
                "Google could not renew authorization. Run python auth.py --force, adding --write if needed.",
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            if not write and attempt < 2:
                time.sleep(2**attempt + random.uniform(0, 0.25))
                continue
            if write:
                raise TasksError(
                    "WRITE_OUTCOME_UNKNOWN",
                    "The connection failed during a write. Read the affected list before retrying; the change may have succeeded.",
                ) from None
            raise TasksError(
                "NETWORK_ERROR", "Could not reach Google Tasks. Check your connection and retry."
            ) from None
    raise AssertionError("unreachable")


def _task(data: dict, *, include_notes: bool = True) -> Task:
    return Task(
        id=data["id"],
        title=data.get("title", ""),
        status=data.get("status", "needsAction"),
        due=data["due"][:10] if data.get("due") else None,
        notes=data.get("notes") if include_notes else None,
        parent=data.get("parent"),
        web_url=data.get("webViewLink"),
        updated=data.get("updated"),
        completed=data.get("completed"),
    )


def _task_list(data: dict) -> TaskList:
    return TaskList(id=data["id"], title=data.get("title", ""), updated=data.get("updated"))


def _page_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise TasksError("INVALID_INPUT", "page_size must be an integer between 1 and 100.")
    return value


def _confirmation(confirm: bool) -> None:
    if confirm is not True:
        raise TasksError(
            "CONFIRMATION_REQUIRED", "Confirm the exact operation with the user, then pass confirm=true."
        )


def _expected_title(actual: str, expected: str) -> None:
    if actual != expected:
        raise TasksError(
            "TITLE_MISMATCH",
            "The title does not match the target. Read it again and confirm the correct resource.",
        )


class TasksClient:
    """One instance per tool call. HTTP clients are never shared between threads."""

    def __init__(self, service_factory=None):
        self._factory = service_factory or get_tasks_service
        self._services = {}

    def _service(self, *, write: bool = False):
        if write not in self._services:
            self._services[write] = self._factory(write=write)
        return self._services[write]

    def list_task_lists(self, page_token: str | None = None, page_size: int = 100) -> TaskListPage:
        page_size = _page_size(page_size)
        result = _execute(self._service().tasklists().list(maxResults=page_size, pageToken=page_token))
        return TaskListPage(
            task_lists=[_task_list(item) for item in result.get("items", [])],
            next_page_token=result.get("nextPageToken"),
        )

    def get_task_list(self, task_list_id: str) -> TaskList:
        task_list_id = validate.identifier(task_list_id)
        return _task_list(_execute(self._service().tasklists().get(tasklist=task_list_id)))

    def list_tasks(
        self,
        task_list_id: str,
        page_token: str | None = None,
        include_completed: bool = False,
        page_size: int = 100,
        due_after: str | None = None,
        due_before: str | None = None,
        include_notes: bool = False,
    ) -> TaskPage:
        task_list_id = validate.identifier(task_list_id)
        page_size = _page_size(page_size)
        after, before = validate.date_range(due_after, due_before)
        parameters = dict(
            tasklist=task_list_id,
            maxResults=page_size,
            pageToken=page_token,
            showCompleted=include_completed,
            showHidden=include_completed,
            showDeleted=False,
        )
        if after:
            parameters["dueMin"] = after + "T00:00:00Z"
        if before:
            upper_day = date.fromisoformat(before)
            # Live Google reads excluded the entire dueMax date, even at 23:59:59.
            # Request the following day, then enforce our inclusive date bound below.
            if upper_day < date.max:
                parameters["dueMax"] = (upper_day + timedelta(days=1)).isoformat() + "T00:00:00Z"
        result = _execute(self._service().tasks().list(**parameters))
        tasks = [_task(item, include_notes=include_notes) for item in result.get("items", [])]
        # Enforce inclusive calendar-date boundaries independently of timestamp comparisons.
        if after or before:
            tasks = [
                task
                for task in tasks
                if task.due and (not after or task.due >= after) and (not before or task.due <= before)
            ]
        return TaskPage(task_list_id=task_list_id, tasks=tasks, next_page_token=result.get("nextPageToken"))

    def get_task(self, task_list_id: str, task_id: str) -> Task:
        task_list_id = validate.identifier(task_list_id)
        task_id = validate.identifier(task_id, "task_id")
        return _task(_execute(self._service().tasks().get(tasklist=task_list_id, task=task_id)))

    def _scan(
        self,
        task_list_id: str | None,
        include_completed: bool,
        max_tasks: int,
        max_lists: int,
        *,
        include_notes: bool = False,
    ) -> tuple[list[TaskMatch], int, bool]:
        if type(max_tasks) is not int or not 1 <= max_tasks <= 5000:
            raise TasksError("INVALID_INPUT", "max_tasks must be between 1 and 5000.")
        if type(max_lists) is not int or not 1 <= max_lists <= 100:
            raise TasksError("INVALID_INPUT", "max_lists must be between 1 and 100.")
        if task_list_id is not None:
            lists = [self.get_task_list(task_list_id)]
            more_lists = False
        else:
            page = self.list_task_lists(page_size=max_lists)
            lists, more_lists = page.task_lists, bool(page.next_page_token)
        records = []
        scanned_lists = 0
        scanned_pages = 0
        for index, task_list in enumerate(lists):
            if scanned_pages >= 100:
                return records, scanned_lists, True
            scanned_lists += 1
            token = None
            seen_tokens = set()
            while True:
                if scanned_pages >= 100:
                    return records, scanned_lists, True
                page = self.list_tasks(
                    task_list.id,
                    page_token=token,
                    include_completed=include_completed,
                    include_notes=include_notes,
                    page_size=min(100, max_tasks - len(records)),
                )
                scanned_pages += 1
                records.extend(
                    TaskMatch(task_list_id=task_list.id, task_list_title=task_list.title, task=task)
                    for task in page.tasks
                )
                token = page.next_page_token
                if len(records) >= max_tasks:
                    return records, scanned_lists, bool(token or more_lists or index < len(lists) - 1)
                if not token:
                    break
                if token in seen_tokens:
                    raise TasksError(
                        "PAGINATION_ERROR",
                        "Google repeated a page token. Retry the scan rather than treating it as complete.",
                    )
                seen_tokens.add(token)
        return records, scanned_lists, more_lists

    def search_tasks(
        self,
        query: str,
        task_list_id: str | None = None,
        include_completed: bool = False,
        limit: int = 20,
        max_tasks: int = 1000,
        max_lists: int = 100,
    ) -> SearchResult:
        if not isinstance(query, str) or not query.strip() or len(query) > 1024:
            raise TasksError("INVALID_INPUT", "query must contain 1–1024 characters and cannot be blank.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TasksError("INVALID_INPUT", "limit must be between 1 and 100.")
        records, scanned_lists, incomplete = self._scan(
            task_list_id, include_completed, max_tasks, max_lists, include_notes=True
        )
        needle = query.strip().casefold()
        matches = [
            record
            for record in records
            if needle in f"{record.task.title}\n{record.task.notes or ''}".casefold()
        ]
        # Search notes internally, but return compact summaries; get_task exposes full notes.
        compact = [
            match.model_copy(update={"task": match.task.model_copy(update={"notes": None})})
            for match in matches[:limit]
        ]
        return SearchResult(
            matches=compact,
            scanned_tasks=len(records),
            scanned_lists=scanned_lists,
            truncated=incomplete or len(matches) > limit,
            limit_reached=len(matches) >= limit,
        )

    def get_task_digest(
        self,
        timezone: str,
        days: int = 7,
        task_list_id: str | None = None,
        max_tasks: int = 1000,
        max_lists: int = 100,
        limit_per_group: int = 20,
    ) -> DigestResult:
        zone = validate.timezone(timezone)
        if type(limit_per_group) is not int or not 1 <= limit_per_group <= 100:
            raise TasksError("INVALID_INPUT", "limit_per_group must be between 1 and 100.")
        if type(days) is not int or not 1 <= days <= 365:
            raise TasksError("INVALID_INPUT", "days must be between 1 and 365.")
        today = datetime.now(zone).date()
        end = (today + timedelta(days=days)).isoformat()
        today_string = today.isoformat()
        records, scanned_lists, incomplete = self._scan(task_list_id, False, max_tasks, max_lists)
        groups = {"overdue": [], "due_today": [], "upcoming": [], "undated": []}
        for record in records:
            if record.task.status == "completed":
                continue
            due = record.task.due
            if not due:
                groups["undated"].append(record)
            elif due < today_string:
                groups["overdue"].append(record)
            elif due == today_string:
                groups["due_today"].append(record)
            elif due <= end:
                groups["upcoming"].append(record)
        for values in groups.values():
            values.sort(
                key=lambda record: (record.task.due or "", record.task.title.casefold(), record.task.id)
            )
        counts = {name: len(values) for name, values in groups.items()}
        output_truncated = any(count > limit_per_group for count in counts.values())
        return DigestResult(
            today=today_string,
            timezone=timezone,
            upcoming_through=end,
            scanned_tasks=len(records),
            scanned_lists=scanned_lists,
            truncated=incomplete or output_truncated,
            scan_truncated=incomplete,
            counts=counts,
            **{name: values[:limit_per_group] for name, values in groups.items()},
        )

    def create_task(
        self,
        task_list_id: str,
        title: str,
        notes: str | None = None,
        due: str | None = None,
        parent_id: str | None = None,
        previous_id: str | None = None,
    ) -> MutationResult:
        task_list_id = validate.identifier(task_list_id)
        body = {"title": validate.title(title)}
        if notes is not None:
            body["notes"] = validate.notes(notes)
        if due is not None:
            body["due"] = validate.due_timestamp(due)
        parameters = dict(tasklist=task_list_id, body=body)
        if parent_id is not None:
            parameters["parent"] = validate.identifier(parent_id, "parent_id")
        if previous_id is not None:
            parameters["previous"] = validate.identifier(previous_id, "previous_id")
        task = _task(_execute(self._service(write=True).tasks().insert(**parameters), write=True))
        return MutationResult(operation="created", task_list_id=task_list_id, task=task)

    def update_task(
        self,
        task_list_id: str,
        task_id: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        clear_due: bool = False,
    ) -> MutationResult:
        task_list_id, task_id = validate.identifier(task_list_id), validate.identifier(task_id, "task_id")
        if due is not None and clear_due:
            raise TasksError("INVALID_INPUT", "Pass due or clear_due=true, not both.")
        body = {}
        if title is not None:
            body["title"] = validate.title(title)
        if notes is not None:
            body["notes"] = validate.notes(notes)
        if due is not None:
            body["due"] = validate.due_timestamp(due)
        elif clear_due:
            body["due"] = None
        if not body:
            raise TasksError("INVALID_INPUT", "Supply at least one field to update.")
        task = _task(
            _execute(
                self._service(write=True).tasks().patch(tasklist=task_list_id, task=task_id, body=body),
                write=True,
            )
        )
        return MutationResult(operation="updated", task_list_id=task_list_id, task=task)

    def set_completed(self, task_list_id: str, task_id: str, completed: bool) -> MutationResult:
        task_list_id, task_id = validate.identifier(task_list_id), validate.identifier(task_id, "task_id")
        body = {"status": "completed" if completed else "needsAction"}
        if not completed:
            body["completed"] = None
        task = _task(
            _execute(
                self._service(write=True).tasks().patch(tasklist=task_list_id, task=task_id, body=body),
                write=True,
            )
        )
        return MutationResult(
            operation="completed" if completed else "reopened", task_list_id=task_list_id, task=task
        )

    def move_task(
        self,
        task_list_id: str,
        task_id: str,
        destination_list_id: str | None = None,
        parent_id: str | None = None,
        previous_id: str | None = None,
    ) -> MutationResult:
        task_list_id, task_id = validate.identifier(task_list_id), validate.identifier(task_id, "task_id")
        parameters = dict(tasklist=task_list_id, task=task_id)
        for name, value in [
            ("destinationTasklist", destination_list_id),
            ("parent", parent_id),
            ("previous", previous_id),
        ]:
            if value is not None:
                parameters[name] = validate.identifier(value, name)
        if parameters.get("parent") == task_id or parameters.get("previous") == task_id:
            raise TasksError("INVALID_INPUT", "A task cannot be its own parent or previous sibling.")
        task = _task(_execute(self._service(write=True).tasks().move(**parameters), write=True))
        return MutationResult(
            operation="moved", task_list_id=parameters.get("destinationTasklist", task_list_id), task=task
        )

    def delete_task(
        self, task_list_id: str, task_id: str, expected_title: str, confirm: bool = False
    ) -> DeleteResult:
        _confirmation(confirm)
        task_list_id, task_id = validate.identifier(task_list_id), validate.identifier(task_id, "task_id")
        service = self._service(write=True)
        task = _task(_execute(service.tasks().get(tasklist=task_list_id, task=task_id)))
        _expected_title(task.title, expected_title)
        _execute(service.tasks().delete(tasklist=task_list_id, task=task_id), write=True)
        return DeleteResult(deleted=True, task_list_id=task_list_id, task_id=task_id)

    def create_task_list(self, title: str) -> TaskList:
        title = validate.title(title)
        return _task_list(
            _execute(self._service(write=True).tasklists().insert(body={"title": title}), write=True)
        )

    def rename_task_list(self, task_list_id: str, title: str) -> TaskList:
        task_list_id, title = validate.identifier(task_list_id), validate.title(title)
        return _task_list(
            _execute(
                self._service(write=True).tasklists().patch(tasklist=task_list_id, body={"title": title}),
                write=True,
            )
        )

    def delete_task_list(
        self, task_list_id: str, expected_title: str, confirm: bool = False, allow_nonempty: bool = False
    ) -> DeleteResult:
        _confirmation(confirm)
        task_list_id = validate.identifier(task_list_id)
        service = self._service(write=True)
        task_list = _task_list(_execute(service.tasklists().get(tasklist=task_list_id)))
        _expected_title(task_list.title, expected_title)
        if not allow_nonempty:
            existing = _execute(
                service.tasks().list(
                    tasklist=task_list_id,
                    maxResults=1,
                    showCompleted=True,
                    showHidden=True,
                    showDeleted=False,
                )
            )
            if existing.get("items") or existing.get("nextPageToken"):
                raise TasksError(
                    "LIST_NOT_EMPTY",
                    "The list contains tasks. Confirm deletion of its contents with the user before setting allow_nonempty=true.",
                )
        _execute(service.tasklists().delete(tasklist=task_list_id), write=True)
        return DeleteResult(deleted=True, task_list_id=task_list_id)

    def clear_completed_tasks(self, task_list_id: str, confirm: bool = False) -> ClearResult:
        _confirmation(confirm)
        task_list_id = validate.identifier(task_list_id)
        _execute(self._service(write=True).tasks().clear(tasklist=task_list_id), write=True)
        return ClearResult(cleared=True, task_list_id=task_list_id)
