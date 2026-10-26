import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import WSGITimeoutError

from gtasks_mcp import auth, config
from gtasks_mcp.errors import TasksError


def fake_credentials(*, write=False, expired=False):
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return Credentials(
        token="FAKE_ACCESS_TOKEN",
        refresh_token="FAKE_REFRESH_TOKEN",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="fake-client",
        client_secret="fake-secret",
        scopes=[config.WRITE_SCOPE if write else config.READ_SCOPE],
        expiry=expiry,
    )


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.token = self.directory / "token.json"
        self.credentials_file = self.directory / "credentials.json"
        self.credentials_file.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "fake-client",
                        "client_secret": "fake-secret",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
            )
        )
        environment = patch.dict(
            os.environ,
            {
                "GTASKS_TOKEN_PATH": str(self.token),
                "GTASKS_CREDENTIALS_PATH": str(self.credentials_file),
                "GTASKS_READ_ONLY": "false",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        flow_patch = patch("gtasks_mcp.auth.InstalledAppFlow.from_client_config")
        self.flow = flow_patch.start()
        self.addCleanup(flow_patch.stop)

    def save(self, credentials):
        self.token.write_text(credentials.to_json())

    def test_missing_token_never_opens_browser_from_tool(self):
        with self.assertRaisesRegex(TasksError, "AUTH_REQUIRED"):
            auth.get_credentials()
        self.flow.assert_not_called()

    def test_read_token_cannot_be_relabelled_as_write_permission(self):
        self.save(fake_credentials())
        with self.assertRaisesRegex(TasksError, "python auth.py --write"):
            auth.get_credentials(write=True)
        self.flow.assert_not_called()
        self.assertEqual(auth._load_token().scopes, [config.READ_SCOPE])

    def test_full_scope_allows_read_operations(self):
        self.save(fake_credentials(write=True))
        self.assertTrue(auth.get_credentials().has_scopes([config.WRITE_SCOPE]))

    def test_read_only_environment_rejects_writes_before_token_loading(self):
        with patch.dict(os.environ, {"GTASKS_READ_ONLY": "true"}):
            with patch("gtasks_mcp.auth._load_token") as load:
                with self.assertRaisesRegex(TasksError, "READ_ONLY_MODE"):
                    auth.get_credentials(write=True)
        load.assert_not_called()

    def test_invalid_token_gives_actionable_error(self):
        self.token.write_text("not JSON")
        with self.assertRaisesRegex(TasksError, "AUTH_REQUIRED"):
            auth.get_credentials()
        self.flow.assert_not_called()

    def test_revoked_refresh_returns_sign_in_instruction_without_browser(self):
        self.save(fake_credentials(expired=True))
        with patch.object(Credentials, "refresh", side_effect=RefreshError("FAKE_SENSITIVE_MESSAGE")):
            with self.assertRaises(TasksError) as caught:
                auth.get_credentials()
        self.assertEqual(caught.exception.code, "AUTH_REQUIRED")
        self.assertNotIn("FAKE_SENSITIVE_MESSAGE", str(caught.exception))
        self.flow.assert_not_called()

    def test_refresh_network_failure_does_not_erase_token(self):
        self.save(fake_credentials(expired=True))
        original = self.token.read_bytes()
        with patch.object(Credentials, "refresh", side_effect=TransportError("offline")):
            with self.assertRaisesRegex(TasksError, "AUTH_NETWORK_ERROR"):
                auth.get_credentials()
        self.assertEqual(self.token.read_bytes(), original)

    def test_interactive_write_upgrade_uses_pkce_loopback_and_new_grant(self):
        self.save(fake_credentials())
        self.flow.return_value.run_local_server.return_value = fake_credentials(write=True)
        result = auth.get_credentials(write=True, interactive=True)
        self.assertTrue(result.has_scopes([config.WRITE_SCOPE]))
        self.assertTrue(self.flow.call_args.kwargs["autogenerate_code_verifier"])
        self.assertEqual(self.flow.call_args.args[1], [config.WRITE_SCOPE])
        callback = self.flow.return_value.run_local_server.call_args.kwargs
        self.assertEqual(callback["bind_addr"], "127.0.0.1")
        self.assertEqual(callback["port"], 0)
        self.assertEqual(callback["timeout_seconds"], 180)

    def test_browser_timeout_preserves_existing_token(self):
        self.save(fake_credentials())
        before = self.token.read_bytes()
        self.flow.return_value.run_local_server.side_effect = WSGITimeoutError("timed out")
        with self.assertRaisesRegex(TasksError, "AUTH_TIMEOUT"):
            auth.get_credentials(interactive=True, force=True)
        self.assertEqual(self.token.read_bytes(), before)

    def test_rejected_scope_upgrade_preserves_previous_token(self):
        self.save(fake_credentials())
        before = self.token.read_bytes()
        self.flow.return_value.run_local_server.return_value = fake_credentials()
        with self.assertRaisesRegex(TasksError, "AUTH_INCOMPLETE"):
            auth.get_credentials(write=True, interactive=True)
        self.assertEqual(self.token.read_bytes(), before)

    def test_manual_auth_can_recover_from_revoked_or_corrupt_token(self):
        for corrupt in [False, True]:
            with self.subTest(corrupt=corrupt):
                self.token.write_text("corrupt") if corrupt else self.save(fake_credentials(expired=True))
                self.flow.return_value.run_local_server.return_value = fake_credentials()
                with patch.object(Credentials, "refresh", side_effect=RefreshError("revoked")):
                    result = auth.get_credentials(interactive=True)
                self.assertTrue(result.valid)

    def test_valid_cached_token_does_not_trigger_refresh_or_browser(self):
        self.save(fake_credentials())
        with patch.object(Credentials, "refresh") as refresh:
            self.assertTrue(auth.get_credentials().valid)
        refresh.assert_not_called()
        self.flow.assert_not_called()

    def test_external_reauthorization_is_loaded_on_next_call(self):
        self.save(fake_credentials())
        self.assertFalse(auth.get_credentials().has_scopes([config.WRITE_SCOPE]))
        self.save(fake_credentials(write=True))
        self.assertTrue(auth.get_credentials(write=True).has_scopes([config.WRITE_SCOPE]))

    def test_atomic_token_save_and_restrictive_permissions(self):
        auth._save_token(fake_credentials())
        self.assertTrue(auth._load_token().valid)
        if os.name != "nt":
            self.assertEqual(self.token.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob(".gtasks-token-*.tmp")), [])

    def test_failed_replacement_preserves_old_token_and_cleans_temp_file(self):
        self.save(fake_credentials())
        before = self.token.read_bytes()
        with patch("gtasks_mcp.auth.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(TasksError, "TOKEN_STORAGE_ERROR"):
                auth._save_token(fake_credentials(write=True))
        self.assertEqual(self.token.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".gtasks-token-*.tmp")), [])

    def test_concurrent_requests_refresh_only_once_within_process(self):
        self.save(fake_credentials(expired=True))
        refreshes = []

        def refresh(credentials, request):
            refreshes.append(True)
            credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

        with patch.object(Credentials, "refresh", refresh):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: auth.get_credentials(), range(4)))
        self.assertTrue(all(credentials.valid for credentials in results))
        self.assertEqual(len(refreshes), 1)

    def test_status_does_not_refresh_or_expose_credentials(self):
        self.save(fake_credentials(write=True, expired=True))
        with patch.object(Credentials, "refresh") as refresh:
            self.assertEqual(auth.authorization_status(), (True, True))
        refresh.assert_not_called()

    def test_web_credentials_are_rejected_for_desktop_flow(self):
        self.credentials_file.write_text('{"web": {}}')
        with self.assertRaisesRegex(TasksError, "AUTH_SETUP_ERROR"):
            auth.get_credentials(interactive=True)
        self.flow.assert_not_called()

    def test_service_uses_timed_transport_and_static_discovery(self):
        self.save(fake_credentials())
        with patch("gtasks_mcp.auth.build") as build:
            with patch("gtasks_mcp.auth.AuthorizedHttp") as authorized:
                with patch("gtasks_mcp.auth.httplib2.Http") as http:
                    auth.get_tasks_service()
        http.assert_called_once_with(timeout=15)
        build.assert_called_once_with(
            "tasks", "v1", http=authorized.return_value, cache_discovery=False, static_discovery=True
        )


if __name__ == "__main__":
    unittest.main()
