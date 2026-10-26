"""Errors safe to show to an MCP client without leaking request contents."""


class TasksError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
