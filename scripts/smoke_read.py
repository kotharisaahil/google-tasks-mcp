"""Optional real-account read-only protocol check. Only metadata/counts are printed."""

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify() -> bool:
    directory = Path(__file__).resolve().parent.parent
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(directory / "server.py"), "--read-only"], cwd=str(directory)
    )
    with tempfile.TemporaryFile(mode="w+") as server_logs:
        async with stdio_client(parameters, errlog=server_logs) as streams:
            async with ClientSession(*streams, read_timeout_seconds=30) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("Read tools:", ", ".join(sorted(tool.name for tool in tools.tools)))
                info = await session.call_tool("get_server_info", {})
                if info.is_error:
                    return False
                print("Write scope stored:", info.structured_content["write_scope_stored"])
                result = await session.call_tool("list_task_lists", {"page_size": 1})
                if result.is_error:
                    print("Google read failed. Check authorization with python auth.py.")
                    return False
                lists = result.structured_content["task_lists"]
                print("Task-list page succeeded; items:", len(lists))
                if result.structured_content["next_page_token"]:
                    next_page = await session.call_tool(
                        "list_task_lists",
                        {
                            "page_size": 1,
                            "page_token": result.structured_content["next_page_token"],
                        },
                    )
                    if next_page.is_error:
                        return False
                    print("Task-list pagination succeeded.")
                if lists:
                    list_id = lists[0]["id"]
                    result = await session.call_tool("list_tasks", {"task_list_id": list_id, "page_size": 5})
                    if result.is_error:
                        print("Task read failed. Inspect the tool error locally.")
                        return False
                    tasks = result.structured_content["tasks"]
                    print("Tasks on checked page:", len(tasks))
                    if tasks:
                        detail = await session.call_tool(
                            "get_task", {"task_list_id": list_id, "task_id": tasks[0]["id"]}
                        )
                        if detail.is_error or detail.structured_content["id"] != tasks[0]["id"]:
                            return False
                        print("Individual task read succeeded.")
                        if tasks[0]["title"].strip():
                            search = await session.call_tool(
                                "search_tasks",
                                {
                                    "query": tasks[0]["title"],
                                    "task_list_id": list_id,
                                    "max_tasks": 20,
                                    "limit": 1,
                                },
                            )
                            if search.is_error or not search.structured_content["matches"]:
                                return False
                            print("Bounded search succeeded.")
                        dated = next((task for task in tasks if task.get("due")), None)
                        if dated:
                            filtered = await session.call_tool(
                                "list_tasks",
                                {
                                    "task_list_id": list_id,
                                    "due_after": dated["due"],
                                    "due_before": dated["due"],
                                },
                            )
                            if filtered.is_error:
                                return False
                            filtered_tasks = filtered.structured_content["tasks"]
                            if dated["id"] not in {task["id"] for task in filtered_tasks} or any(
                                task["due"] != dated["due"] for task in filtered_tasks
                            ):
                                return False
                            print("Calendar-date filter succeeded.")
                    digest = await session.call_tool(
                        "get_task_digest",
                        {
                            "task_list_id": list_id,
                            "timezone": "America/Los_Angeles",
                            "max_tasks": 20,
                            "limit_per_group": 2,
                        },
                    )
                    if digest.is_error:
                        return False
                    print("Bounded timezone-aware digest succeeded.")
                print("Live MCP read passed. No task data was changed.")
                return True


if __name__ == "__main__":
    try:
        passed = asyncio.run(asyncio.wait_for(verify(), timeout=60))
    except Exception as error:
        print("Live MCP check failed:", type(error).__name__)
        passed = False
    raise SystemExit(0 if passed else 1)
