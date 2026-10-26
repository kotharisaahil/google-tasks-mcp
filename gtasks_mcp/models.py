"""Typed results publish useful output schemas and validate data returned to clients."""

from typing import Literal

from pydantic import BaseModel


class TaskList(BaseModel):
    id: str
    title: str
    updated: str | None = None


class TaskListPage(BaseModel):
    task_lists: list[TaskList]
    next_page_token: str | None = None


class Task(BaseModel):
    id: str
    title: str
    status: Literal["needsAction", "completed"]
    due: str | None = None
    notes: str | None = None
    parent: str | None = None
    web_url: str | None = None
    updated: str | None = None
    completed: str | None = None


class TaskPage(BaseModel):
    task_list_id: str
    tasks: list[Task]
    next_page_token: str | None = None


class TaskMatch(BaseModel):
    task_list_id: str
    task_list_title: str
    task: Task


class SearchResult(BaseModel):
    matches: list[TaskMatch]
    scanned_tasks: int
    scanned_lists: int
    truncated: bool
    limit_reached: bool


class DigestResult(BaseModel):
    today: str
    timezone: str
    upcoming_through: str
    overdue: list[TaskMatch]
    due_today: list[TaskMatch]
    upcoming: list[TaskMatch]
    undated: list[TaskMatch]
    scanned_tasks: int
    scanned_lists: int
    truncated: bool
    scan_truncated: bool
    counts: dict[str, int]


class MutationResult(BaseModel):
    operation: str
    task_list_id: str
    task: Task


class DeleteResult(BaseModel):
    deleted: bool
    task_list_id: str
    task_id: str | None = None


class ClearResult(BaseModel):
    cleared: bool
    task_list_id: str
    effect: str = "Completed tasks were hidden, not permanently deleted."


class ServerInfo(BaseModel):
    name: str
    version: str
    read_only_mode: bool
    credentials_available: bool
    write_scope_stored: bool
    timezone: str
    transport: str = "stdio"
