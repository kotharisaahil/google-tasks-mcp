"""Launch a real stdio MCP server backed only by in-memory fake Google data."""

from unittest.mock import patch

from gtasks_mcp.server import create_server
from tests.fake_google import FakeService

if __name__ == "__main__":
    service = FakeService()
    with patch("gtasks_mcp.client.get_tasks_service", return_value=service):
        with patch("gtasks_mcp.server.authorization_status", return_value=(True, True)):
            create_server().run(transport="stdio")
