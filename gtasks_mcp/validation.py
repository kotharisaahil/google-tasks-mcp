"""Validate tool inputs before constructing any Google request."""

import re
from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StrictBool

from gtasks_mcp.errors import TasksError

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=2048,
        description="Exact Google ID from a previous tool result; names are not IDs.",
    ),
]
PageToken = Annotated[str, Field(min_length=1, max_length=4096)]
PageSize = Annotated[int, Field(strict=True, ge=1, le=100)]
Title = Annotated[str, Field(min_length=1, max_length=1024)]
ExpectedTitle = Annotated[str, Field(max_length=1024)]
TimezoneName = Annotated[str, Field(min_length=1, max_length=128)]
Notes = Annotated[str, Field(max_length=8192)]
DateOnly = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Calendar date YYYY-MM-DD; times and relative dates are not accepted.",
    ),
]
Flag = StrictBool
Limit = Annotated[int, Field(strict=True, ge=1, le=100)]
ScanLimit = Annotated[int, Field(strict=True, ge=1, le=5000)]
ListLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
DayCount = Annotated[int, Field(strict=True, ge=1, le=365)]


def identifier(value: str, name: str = "task_list_id") -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise TasksError("INVALID_INPUT", f"{name} must be a non-empty Google ID.")
    if any(ord(character) < 32 for character in value):
        raise TasksError("INVALID_INPUT", f"{name} contains a control character.")
    return value.strip()


def title(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 or "\x00" in value:
        raise TasksError("INVALID_INPUT", "title must contain 1–1024 characters and cannot be blank.")
    return value.strip()


def notes(value: str) -> str:
    if not isinstance(value, str) or len(value) > 8192 or "\x00" in value:
        raise TasksError("INVALID_INPUT", "notes must contain at most 8192 characters without null bytes.")
    return value


def calendar_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise TasksError("INVALID_DATE", "Use a calendar date in YYYY-MM-DD format, without a time.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise TasksError("INVALID_DATE", "The supplied calendar date does not exist.") from None


def due_timestamp(value: str) -> str:
    return calendar_date(value) + "T00:00:00.000Z"


def date_range(after: str | None, before: str | None) -> tuple[str | None, str | None]:
    after = calendar_date(after) if after is not None else None
    before = calendar_date(before) if before is not None else None
    if after and before and after > before:
        raise TasksError("INVALID_DATE_RANGE", "due_after must be on or before due_before.")
    return after, before


def timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise TasksError(
            "INVALID_TIMEZONE", "Use an IANA timezone such as America/Los_Angeles or UTC."
        ) from None
