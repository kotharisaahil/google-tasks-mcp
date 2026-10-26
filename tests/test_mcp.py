import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from gtasks_mcp.server import create_server
from tests.fake_google import FakeService


class ToolInterfaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"GTASKS_READ_ONLY": "false"})
        environment.start()
        self.addCleanup(environment.stop)
        self.service = FakeService()
        factory = patch("gtasks_mcp.client.get_tasks_service", return_value=self.service)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.server = create_server()

    async def test_tools_have_output_schemas_and_truthful_annotations(self):
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        self.assertEqual(len(tools), 17)
        for tool in tools.values():
            self.assertIsNotNone(tool.output_schema, tool.name)
        self.assertTrue(tools["list_tasks"].annotations.read_only_hint)
        self.assertTrue(tools["delete_task"].annotations.destructive_hint)
        self.assertFalse(tools["create_task"].annotations.idempotent_hint)
        self.assertEqual(tools["list_tasks"].input_schema["required"], ["task_list_id"])

    async def test_strict_inputs_are_rejected_before_contacting_google(self):
        for arguments in [
            {},
            {"task_list_id": "L1", "include_completed": "false"},
            {"task_list_id": "L1", "page_size": 101},
            {"task_list_id": "L1", "page_size": True},
            {"task_list_id": "L1", "due_after": "tomorrow"},
        ]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError):
                    await self.server.call_tool("list_tasks", arguments)
        self.factory.assert_not_called()

    async def test_calendar_validation_reports_useful_error(self):
        with self.assertRaisesRegex(ToolError, "INVALID_DATE"):
            await self.server.call_tool(
                "create_task", {"task_list_id": "L1", "title": "Test", "due": "2026-02-30"}
            )
        self.factory.assert_not_called()

    async def test_digest_caps_output_and_preserves_counts(self):
        self.service.data["L1"] = [
            {"id": str(index), "title": f"Task {index}", "status": "needsAction"} for index in range(25)
        ]
        result = await self.server.call_tool(
            "get_task_digest",
            {
                "task_list_id": "L1",
                "timezone": "America/Los_Angeles",
                "limit_per_group": 3,
            },
        )
        self.assertEqual(len(result.structured_content["undated"]), 3)
        self.assertEqual(result.structured_content["counts"]["undated"], 25)
        self.assertTrue(result.structured_content["truncated"])
        self.assertFalse(result.structured_content["scan_truncated"])

    async def test_digest_rejects_invalid_timezone_before_google_access(self):
        with self.assertRaisesRegex(ToolError, "INVALID_TIMEZONE"):
            await self.server.call_tool("get_task_digest", {"timezone": "not/a/zone"})
        self.factory.assert_not_called()

    async def test_read_only_mode_hides_all_mutation_tools(self):
        tools = await create_server(read_only=True).list_tools()
        self.assertEqual(len(tools), 7)
        self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))
        self.assertNotIn("create_task", [tool.name for tool in tools])

    async def test_tool_errors_do_not_include_raw_google_response(self):
        with self.assertRaises(ToolError) as caught:
            await self.server.call_tool("get_task", {"task_list_id": "L1", "task_id": "missing"})
        self.assertIn("NOT_FOUND", str(caught.exception))
        self.assertNotIn("SENSITIVE_RESPONSE_BODY", str(caught.exception))

    async def test_unexpected_errors_are_sanitized(self):
        self.factory.side_effect = RuntimeError("FAKE_SECRET_DO_NOT_LEAK")
        with self.assertLogs("gtasks_mcp.server", level="ERROR") as logs:
            with self.assertRaises(ToolError) as caught:
                await self.server.call_tool("list_task_lists", {})
        self.assertNotIn("FAKE_SECRET", str(caught.exception))
        self.assertNotIn("FAKE_SECRET", " ".join(logs.output))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_read_write_and_error_round_trip(self):
        environment = {**os.environ, "GTASKS_READ_ONLY": "false"}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.protocol_fixture"],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=environment,
        )
        with tempfile.TemporaryFile(mode="w+") as logs:
            async with stdio_client(parameters, errlog=logs) as streams:
                async with ClientSession(*streams, read_timeout_seconds=15) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 17)
                    listed = await session.call_tool("list_task_lists", {"page_size": 1})
                    self.assertFalse(listed.is_error)
                    self.assertIsNotNone(listed.structured_content["next_page_token"])
                    created = await session.call_tool(
                        "create_task",
                        {
                            "task_list_id": "L1",
                            "title": "Protocol test",
                            "notes": "Preserve me",
                            "due": "2028-02-29",
                        },
                    )
                    self.assertFalse(created.is_error)
                    task_id = created.structured_content["task"]["id"]
                    updated = await session.call_tool(
                        "update_task", {"task_list_id": "L1", "task_id": task_id, "title": "Changed"}
                    )
                    self.assertEqual(updated.structured_content["task"]["notes"], "Preserve me")
                    rejected = await session.call_tool(
                        "delete_task",
                        {
                            "task_list_id": "L1",
                            "task_id": task_id,
                            "expected_title": "Changed",
                        },
                    )
                    self.assertTrue(rejected.is_error)
                    deleted = await session.call_tool(
                        "delete_task",
                        {
                            "task_list_id": "L1",
                            "task_id": task_id,
                            "expected_title": "Changed",
                            "confirm": True,
                        },
                    )
                    self.assertFalse(deleted.is_error)
                    invalid = await session.call_tool("list_tasks", {})
                    self.assertTrue(invalid.is_error)

    async def test_real_stdio_read_only_mode(self):
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.protocol_fixture"],
            cwd=str(Path(__file__).resolve().parent.parent),
            env={**os.environ, "GTASKS_READ_ONLY": "true"},
        )
        with tempfile.TemporaryFile(mode="w+") as logs:
            async with stdio_client(parameters, errlog=logs) as streams:
                async with ClientSession(*streams, read_timeout_seconds=15) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 7)
                    result = await session.call_tool("list_tasks", {"task_list_id": "L1"})
                    self.assertFalse(result.is_error)
                    denied = await session.call_tool("create_task", {"task_list_id": "L1", "title": "No"})
                    self.assertTrue(denied.is_error)


if __name__ == "__main__":
    unittest.main()
