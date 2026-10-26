# Google Tasks MCP

A local MCP server that lets an AI assistant read and manage Google Tasks through Google's official API. Built in Python with the official MCP SDK; no third-party backend or model API key is needed by the server.

**Version 0.2.0 · Python 3.11+ · MCP SDK 2.3.0 · stdio transport**

## What it does

| Tools | Purpose |
| --- | --- |
| `get_server_info` | Local configuration and stored permission status, without secrets |
| `list_task_lists`, `get_task_list` | Read task lists and exact IDs |
| `list_tasks`, `get_task` | Read tasks, notes, completion state, and calendar dates |
| `search_tasks` | Search titles and notes across one or multiple lists |
| `get_task_digest` | Group open tasks into overdue, today, upcoming, and undated |
| `create_task`, `update_task` | Create tasks and patch selected fields |
| `complete_task`, `reopen_task` | Change completion status |
| `move_task` | Reorder, reparent, or move between lists using Google's native API |
| `delete_task` | Delete an exact task after confirmation and a title check |
| `create_task_list`, `rename_task_list`, `delete_task_list` | Manage lists |
| `clear_completed_tasks` | Hide completed tasks; this does not permanently delete them |

## Setup

Create and activate a virtual environment from this folder:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Python 3.11+ works. The commands above use the Python 3.13 installation used during development. A tested development dependency snapshot is provided in `requirements.lock`; install it first if you want those exact dependency versions, then install this project with `python -m pip install -e . --no-deps`.

In [Google Cloud Console](https://console.cloud.google.com/):

1. Create a project under an account you control and enable **Google Tasks API**.
2. Configure **Google Auth Platform → Branding**, with an **External** audience for a personal Gmail app.
3. Add the Google account containing your tasks under **Audience → Test users**.
4. Under **Data Access**, add `https://www.googleapis.com/auth/tasks.readonly` for reads. Add `https://www.googleapis.com/auth/tasks` when enabling writes.
5. Create an OAuth client with type **Desktop app** and select **Use this client for an AI-powered agent**.
6. Download its JSON and save it as `credentials.json` in this folder. Both credentials and tokens are ignored by Git.

The Cloud project owner and the Google Tasks user can be different accounts. School/work accounts may require administrator approval. See [Google's quickstart](https://developers.google.com/workspace/tasks/quickstart/python).

Authorize read access:

```bash
python auth.py
```

Existing read-only authorization continues to work. To enable the write tools, explicitly grant Google's write scope:

```bash
python auth.py --write
```

To choose another account or replace revoked authorization, add `--force`; use `--write --force` to retain write access. Authentication has a three-minute browser timeout, uses PKCE and a loopback callback, and replaces the token atomically with owner-only permissions. The MCP server never opens a sign-in browser.

## Run and test in Inspector

Install Node.js if needed for Inspector (the development machine uses Node 24). From this folder:

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

Connect, list tools, then:

1. Call `list_task_lists` and copy a list's `id`.
2. Call `list_tasks` with that value as `task_list_id`.
3. Call `get_task_digest` with `timezone="America/Los_Angeles"`, or your own IANA timezone.

Keep Terminal running while using Inspector. **Control+C** stops the process; **Control+X** exits nano only.

To expose only the seven read tools, even if Google granted write access:

```bash
python server.py --read-only
```

After installing the project, `gtasks-mcp` and `gtasks-auth` are also available as commands. For a wheel installation outside this checkout, set absolute `GTASKS_CREDENTIALS_PATH` and `GTASKS_TOKEN_PATH` paths to a private configuration directory.

## Connect an MCP client

`examples/mcp-client.json` shows the conventional configuration shape. Replace `/absolute/path/to/google-tasks-mcp` with this checkout's actual path. Clients have different configuration locations; consult the chosen client's documentation. The server speaks standard stdio MCP and does not run an HTTP listener for tools.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `GTASKS_CREDENTIALS_PATH` | Checkout's `credentials.json` | Desktop OAuth credentials |
| `GTASKS_TOKEN_PATH` | Checkout's `token.json` | Private stored user authorization |
| `GTASKS_READ_ONLY` | `false` | Hide all write tools and reject writes in the API client |
| `GTASKS_TIMEZONE` | `UTC` | Default digest timezone |

Set these in your shell or MCP client's environment. `.env.example` documents them; `.env` files are **not** automatically loaded. `get_server_info` inspects local token metadata; it does not prove a token is still accepted by Google.

## Behavior that matters

- **Use IDs.** Two lists or tasks can have the same title. Mutations never guess which title match you meant.
- **Pages are explicit.** `list_tasks` and `list_task_lists` return `next_page_token`. Pass it back with the same filters; an empty filtered page can still have another page.
- **Search is bounded.** Search/digest scan at most `max_tasks` (default 1,000, max 5,000), `max_lists` (default/max 100), and 100 task pages. `truncated=true` means incomplete results. Search returns at most `limit` matches (default 20, max 100); restrict to a list or raise the budget when needed. Never interpret an incomplete empty search as proof that no matching task exists.
- **Dates have no time of day.** Supply `YYYY-MM-DD`, including for inclusive date filters. Returned `due` values use that same format. No timed reminders or recurrence editing are claimed.
- **Digests stay compact.** Each group returns up to `limit_per_group` tasks (default 20, max 100), plus counts from the scanned data. `scan_truncated` distinguishes a partial scan from output shortened to the group limit. Counts from a partial scan are lower bounds.
- **Notes stay intact.** Updates use PATCH; omitted fields are preserved. `notes=""` clears notes; `clear_due=true` removes a date. `due` and `clear_due` cannot be combined.
- **Completed tasks can be hidden.** `include_completed=true` requests both completed and hidden tasks, including tasks finished in Google's own apps.
- **Writes aren't replayed automatically.** Transient reads retry at most twice with backoff. A timeout or server failure during a write reports an unknown outcome; read the affected list before retrying. This cannot guarantee deduplication if a caller repeats a successful creation.
- **Deletion is deliberate.** Deletion requires `confirm=true` and an exact `expected_title`. Nonempty lists require `allow_nonempty=true`. Confirmation flags are execution guardrails, not evidence of human consent: your MCP host must obtain approval. Other clients can edit data between the title check and deletion; this is not a transaction.
- **Recurrence isn't detectable.** Google's API does not expose recurrence rules. This implementation does not guess from duplicate titles and cannot guarantee deletion affects only one occurrence. Native cross-list moves of repeating tasks may be rejected by Google.
- **Assigned tasks are limited.** Default task listings exclude tasks assigned from Docs/Chat. Such tasks have additional API restrictions; this project focuses on ordinary personal tasks.

See the [Google Task resource](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks), [list parameters](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks/list), and [native move API](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks/move).

## Development and verification

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests use synthetic credentials and fake Google responses. They never read your token or change Google Tasks. The protocol integration tests launch a real MCP subprocess with a fake Google service and cover reads, writes, validation, and read-only mode. GitHub Actions runs the checks on Python 3.11 and 3.13 without Google credentials.

For an optional real-account **read-only** smoke test after authorization:

```bash
python scripts/smoke_read.py
```

It prints tool names and counts, not task contents or credentials. Real write behavior should be checked in a dedicated sandbox list after write authorization. Automated fake-service tests do not establish all live Google API behavior.

## Code map

```text
server.py / auth.py       familiar launch commands
gtasks_mcp/server.py      MCP descriptions, schemas, annotations, error boundary
gtasks_mcp/client.py      Google operations, pagination, search, digest, retry policy
gtasks_mcp/auth.py        scope checks, explicit sign-in, refresh, atomic token storage
gtasks_mcp/models.py      typed outputs
gtasks_mcp/validation.py  calendar dates, IDs, titles, and input limits
gtasks_mcp/config.py      environment configuration
tests/                   behavior and real-protocol tests using fake Google data
```

## Design references

This is an independently written implementation inspired by patterns studied in [oskarengl/google-tasks-mcp](https://github.com/oskarengl/google-tasks-mcp), [scottie-will/google-tasks-mcp](https://github.com/scottie-will/google-tasks-mcp), [girmmy/google-tasks-mcp-server](https://github.com/girmmy/google-tasks-mcp-server), and [ebmurha/google-tasks-mcp](https://github.com/ebmurha/google-tasks-mcp). See `docs/design.md` for the comparison and decisions. Inspiration is credited; this repository does not claim those projects' work as its own.

This project is for one user's local machine. Hosted multi-user OAuth, HTTP serving, recurrence management, background scheduling, offline synchronization, and distributed idempotency are outside its scope. Publishing code on GitHub is separate from deploying a public service.

## Troubleshooting

| Error | Action |
| --- | --- |
| `AUTH_REQUIRED` | Run `python auth.py`; add `--force` to select an account again |
| `SCOPE_REQUIRED` | Add the Tasks write scope in Cloud, then run `python auth.py --write` |
| `READ_ONLY_MODE` | Review `GTASKS_READ_ONLY` or the `--read-only` launch option |
| `ACCESS_DENIED` | Check the API, Google account permissions, and administrator policies |
| `RATE_LIMITED` | Wait before retrying; avoid repeatedly scanning all lists |
| `WRITE_OUTCOME_UNKNOWN` | Read the list before repeating the write |
| `TITLE_MISMATCH` / `LIST_NOT_EMPTY` | Read the exact target again and confirm the intended deletion |

OAuth apps left in Google's External **Testing** state can receive refresh tokens that expire after seven days for Tasks scopes. Reauthorize when needed and review [Google's token-expiration guidance](https://developers.google.com/identity/protocols/oauth2#expiration) before changing publishing status. A public multi-user OAuth app may have additional verification requirements.

## License

MIT. Dependencies retain their respective licenses.
