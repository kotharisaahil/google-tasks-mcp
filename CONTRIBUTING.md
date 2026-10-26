# Contributing

Use Python 3.11+ and install `python -m pip install -e ".[dev]"` inside a virtual environment.

Run before submitting a change:

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Use `tests/fake_google.py` for behavior tests. Tests and CI must not require a Google account, open an authentication browser, or modify real tasks. `scripts/smoke_read.py` is an explicit optional real-account read check.

Keep MCP schemas/descriptions in `gtasks_mcp/server.py` and Google behavior in `gtasks_mcp/client.py`. Add a behavior test when fixing pagination, dates, auth, retry policy, or mutation safety. Preserve the root `server.py` and `auth.py` entry points.

Never commit account credentials, tokens, task exports, or logs containing personal data. Do not print arbitrary Google exceptions or task contents to server logs. Standard output belongs to the stdio MCP protocol.

Describe the problem, final behavior, relevant limitations, and verification in a pull request. Avoid claiming live write behavior was tested when only fake-service tests were run.
