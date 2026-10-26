"""Compatibility entry point: python server.py or MCP Inspector."""

from gtasks_mcp.server import create_server, main

mcp = create_server()


if __name__ == "__main__":
    raise SystemExit(main())
