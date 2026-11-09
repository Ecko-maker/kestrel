# kestrel-traces-mcp

Kestrel's traces and approval audit log, read-only, over MCP. Debug the agent from your editor: "why did that
request take 20 s?", "which calls were rejected this week?", "what did the local student cost compared with
Groq?". Each request is a tree of spans (model calls, tool calls, approvals) with timings, tokens and cost, as the
console's trace view shows it.

| Tool | Arguments | Returns |
|---|---|---|
| `list_traces` | `db` (`traces`, `bench`, `distill`), `limit` (≤ 100), `since`, `provider`, `status` | Newest first: id, start, status, steps, tokens, latency (approval waits excluded), list-price and actual cost, providers, rating |
| `get_trace` | `trace_id`, `db` | The trace and its spans: name, parent, timing, status, whether it errored, and the metadata attributes (provider, model, tokens, cost, tool name, risk tier, approval decision, retries, route) |
| `trace_stats` | `db` | The totals `kestrel stats` prints: requests, latency p50/p95, tokens, cost, tool calls, approvals, error and fallback rates, ratings |
| `list_approvals` | `limit`, `since`, `tool`, `decision` | The approval audit log, newest first: time, tool, decision, and why an escalated card appeared (`escalated_by`) |

## Security stance

- **Read-only:** SQLite is opened with `mode=ro` and the audit log is only read (a test checks the files are byte-identical after every call).
- **No network:** no model calls, no web (a test runs every tool with sockets blocked).
- **Names, not paths:** `db` is one of three names in one logs folder (`KESTREL_TRACES_MCP_LOGS`, default the repo's `logs/`), resolved with links followed and refused if outside it. `.env` is never read.
- **Metadata only by default.** These are content, returned only with `KESTREL_TRACES_MCP_CONTENT=on`:
  - request and answer text;
  - tool arguments and results;
  - error text, approval arguments and reasons, rating notes;
  - any span attribute not on the metadata allowlist.
- **Content, when on, is redacted** again (Kestrel's `redact()`: API keys, secret assignments, secret env values) and comes back inside `<untrusted_data>`.

## Run it

From the repo, with its environment (`uv sync` once). It speaks MCP over stdin/stdout:

```powershell
uv run --directory C:\path\to\kestrel --no-sync python -m servers.traces_mcp
```

**Claude Code:** this repo's `.mcp.json` registers it as `kestrel-traces`, with content explicitly off (run from
the repo root, so no `--directory`). Claude Code asks once to approve a project's servers; after that its tools
appear as `mcp__kestrel-traces__*`.

**Checked with a real client (2026-11-08):**
- **Over stdio:** started exactly as `.mcp.json` starts it, with the MCP SDK's stdio client. It listed the 4 tools; `trace_stats()` on the development `logs/traces.db` returned 1,401 characters of metadata, no error.
- **From Claude Code:** `claude mcp list` sees it ("Pending approval"). One tool call from Claude Code waits for the owner's approval in an interactive `claude` session.

For another project, the same entry with the repo's path:

```json
{
  "mcpServers": {
    "kestrel-traces": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\kestrel", "--no-sync", "python", "-m", "servers.traces_mcp"]
    }
  }
}
```

or from a terminal: `claude mcp add kestrel-traces -- uv run --directory C:\path\to\kestrel --no-sync python -m servers.traces_mcp`

**Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop):

```json
{
  "mcpServers": {
    "kestrel-traces": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\kestrel", "--no-sync", "python", "-m", "servers.traces_mcp"],
      "env": {"KESTREL_TRACES_MCP_CONTENT": "off"}
    }
  }
}
```

Set `KESTREL_TRACES_MCP_CONTENT` to `on` only if you want the client (and its model provider) to see your prompts
and the tools' data.

## Example

**Question:** "Why did trace 3f2a9c1e take 21 seconds?"

**Tool call:** `get_trace(trace_id="3f2a9c1e")`

**Answer**, from a synthetic database: these traces aren't pinned anywhere, and your own traces never appear in an
example. Content is off, so the request text and the email's arguments, both stored in the trace, aren't in the
answer:

```json
{
 "db": "traces",
 "content": false,
 "trace": {"trace_id": "3f2a9c1e", "duration_ms": 21400.0, "wait_ms": 18200.0, "status": "ok", "stop_reason": "answered",
           "steps": 2, "input_tokens": 2900, "output_tokens": 160, "cost_usd": 0.0, "providers": ["ollama"],
           "start": "2026-11-03T10:00:00+00:00", "latency_ms": 3200.0},
 "spans": [
  {"span_id": "s1", "name": "agent_run", "duration_ms": 21400.0, "status": "ok",
   "attributes": {"kestrel.steps": 2, "kestrel.stop_reason": "answered"}},
  {"span_id": "s2", "parent_id": "s1", "name": "llm_call", "duration_ms": 2600.0, "status": "ok",
   "attributes": {"gen_ai.provider.name": "ollama", "gen_ai.request.model": "kestrel-student",
                  "gen_ai.usage.input_tokens": 1650, "gen_ai.usage.output_tokens": 70, "kestrel.tool_calls": ["send_message"]}},
  {"span_id": "s3", "parent_id": "s1", "name": "approval", "duration_ms": 18200.0, "status": "ok",
   "attributes": {"kestrel.approval.decision": "approved"}},
  {"span_id": "s4", "parent_id": "s1", "name": "tool_call", "duration_ms": 40.0, "status": "ok",
   "attributes": {"gen_ai.tool.name": "send_message", "kestrel.tool.risk": "confirm", "kestrel.tool.ran": true}}
 ]
}
```

What a client concludes from it: 18.2 of the 21.4 s were the person deciding on the approval card. Kestrel's own
latency was 3.2 s, with the local student's model call taking 2.6 s, at $0 cost. (Shortened here: each span also
carries `start` and `errored`.)

Tests: `tests/test_mcp_servers.py`.
