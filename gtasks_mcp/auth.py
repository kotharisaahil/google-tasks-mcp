"""Google OAuth is interactive only through the explicit authentication command."""

import argparse
import json
import os
import tempfile
import threading
from functools import partial

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from gtasks_mcp import config
from gtasks_mcp.errors import TasksError

_token_lock = threading.RLock()
AUTH_COMMAND = "python auth.py"


def _save_token(credentials: Credentials) -> None:
    try:
        _write_token(credentials)
    except OSError:
        raise TasksError(
            "TOKEN_STORAGE_ERROR",
            "Could not save authorization. Check the token directory's permissions and free space.",
        ) from None


def _write_token(credentials: Credentials) -> None:
    """Replace the token atomically; temporary and destination files have mode 0600."""
    destination = config.token_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".gtasks-token-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
            token_file.write(credentials.to_json())
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_token() -> Credentials | None:
    path = config.token_path()
    if not path.exists():
        return None
    try:
        # Preserve the scopes actually stored by Google instead of assigning broader scopes.
        return Credentials.from_authorized_user_file(str(path))
    except (ValueError, OSError, TypeError):
        raise TasksError(
            "AUTH_REQUIRED", f"Stored authorization is unreadable. Run: {AUTH_COMMAND} --force"
        ) from None


def _has_access(credentials: Credentials, write: bool) -> bool:
    if write:
        return credentials.has_scopes([config.WRITE_SCOPE])
    return credentials.has_scopes([config.READ_SCOPE]) or credentials.has_scopes([config.WRITE_SCOPE])


def get_credentials(*, write: bool = False, interactive: bool = False, force: bool = False) -> Credentials:
    if write and config.read_only():
        raise TasksError("READ_ONLY_MODE", "Writes are disabled by GTASKS_READ_ONLY.")
    with _token_lock:
        try:
            credentials = _load_token()
        except TasksError:
            if not interactive:
                raise
            credentials = None

        if force:
            if not interactive:
                raise TasksError("AUTH_REQUIRED", f"Forced sign-in requires running: {AUTH_COMMAND} --force")
            credentials = None

        if credentials and not _has_access(credentials, write):
            if not interactive:
                command = AUTH_COMMAND + (" --write" if write else " --force")
                raise TasksError(
                    "SCOPE_REQUIRED", f"Google has not granted the required permission. Run: {command}"
                )
            credentials = None

        if credentials and not credentials.valid and credentials.refresh_token:
            try:
                credentials.refresh(partial(Request(), timeout=15))
                _save_token(credentials)
            except RefreshError:
                if not interactive:
                    command = AUTH_COMMAND + (" --write" if write else "")
                    raise TasksError(
                        "AUTH_REQUIRED", f"Google authorization expired or was revoked. Run: {command}"
                    ) from None
                credentials = None
            except (TransportError, OSError):
                raise TasksError(
                    "AUTH_NETWORK_ERROR",
                    "Could not contact Google to refresh authorization. Check your connection.",
                ) from None

        if not credentials or not credentials.valid:
            if not interactive:
                command = AUTH_COMMAND + (" --write" if write else "")
                raise TasksError("AUTH_REQUIRED", f"Sign in first by running: {command}")
            path = config.credentials_path()
            try:
                client_config = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(client_config, dict) or "installed" not in client_config:
                    raise ValueError("Desktop client required")
                scope = config.WRITE_SCOPE if write else config.READ_SCOPE
                flow = InstalledAppFlow.from_client_config(
                    client_config, [scope], autogenerate_code_verifier=True
                )
                credentials = flow.run_local_server(
                    host="127.0.0.1",
                    bind_addr="127.0.0.1",
                    port=0,
                    timeout_seconds=180,
                    access_type="offline",
                    prompt="consent",
                )
            except WSGITimeoutError:
                raise TasksError(
                    "AUTH_TIMEOUT", "Google sign-in timed out. Run the authentication command again."
                ) from None
            except (OSError, ValueError, KeyError, TypeError, OAuth2Error, RequestException):
                raise TasksError(
                    "AUTH_SETUP_ERROR",
                    "Check your Desktop app credentials file and complete Google sign-in, then retry.",
                ) from None
            if not credentials.refresh_token or not _has_access(credentials, write):
                raise TasksError(
                    "AUTH_INCOMPLETE",
                    "Google did not grant offline access and the requested scope. Sign in again and approve access.",
                )
            _save_token(credentials)
        return credentials


def get_tasks_service(*, write: bool = False, allow_interactive: bool = False):
    credentials = get_credentials(write=write, interactive=allow_interactive)
    # A fresh HTTP transport per operation avoids sharing httplib2 across tool threads.
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=15))
    return build("tasks", "v1", http=http, cache_discovery=False, static_discovery=True)


def authorization_status() -> tuple[bool, bool]:
    """Inspect local authorization without refreshing it or opening a browser."""
    try:
        credentials = _load_token()
        if credentials is None:
            return False, False
        usable = bool(credentials.valid or credentials.refresh_token)
        return usable and _has_access(credentials, False), usable and _has_access(credentials, True)
    except TasksError:
        return False, False


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize your local Google Tasks MCP server.")
    parser.add_argument(
        "--write", action="store_true", help="Request permission to create, edit, complete, and delete tasks."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Open Google sign-in again, for example to select a different account.",
    )
    arguments = parser.parse_args()
    try:
        get_credentials(write=arguments.write, interactive=True, force=arguments.force)
    except TasksError as error:
        parser.exit(1, f"{error}\n")
    print("Google Tasks authorization saved. You can now start the MCP server.")
    return 0
