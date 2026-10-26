"""Configuration is read at call time, making tests and client environments predictable."""

import os
from pathlib import Path

from gtasks_mcp.errors import TasksError

PROJECT_DIR = Path(__file__).resolve().parent.parent
READ_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/tasks"


def credentials_path() -> Path:
    return (
        Path(os.environ.get("GTASKS_CREDENTIALS_PATH", PROJECT_DIR / "credentials.json"))
        .expanduser()
        .resolve()
    )


def token_path() -> Path:
    return Path(os.environ.get("GTASKS_TOKEN_PATH", PROJECT_DIR / "token.json")).expanduser().resolve()


def read_only() -> bool:
    value = os.environ.get("GTASKS_READ_ONLY", "false").lower().strip()
    if value not in {"true", "false", "1", "0"}:
        raise TasksError("CONFIG_ERROR", "GTASKS_READ_ONLY must be true or false.")
    return value in {"true", "1"}


def default_timezone() -> str:
    return os.environ.get("GTASKS_TIMEZONE", "UTC")
