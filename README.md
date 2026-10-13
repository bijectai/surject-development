# biject-surject

A one-command, lightweight monitor that catches when an AI agent does
something you didn't intend. No dashboard, no login, no telemetry. It only
speaks when something is wrong.

> **Status:** early development, not yet released beyond a placeholder.

**Docs:** [getting started and guides](docs/README.md) · [policy reference](docs/policy.md) · [reports and sharing](docs/reports.md)

## Quickstart

```bash
pip install biject-surject
surject init                      # writes a commented surject.yaml
surject watch -- python agent.py  # run your agent with Surject attached
```

Your agent runs normally. When it calls a tool in a way your policy doesn't
allow, Surject says so:

```
[surject] FLAGGED write_file (monitor mode, call allowed)
  rule    paths.read_only[1]
  why     'path' is inside ./data/raw/**, which is read-only, and 'write_file' looks like it changes files
  params  {"path": "data/raw/experiment_07.csv", "content": "overwritten"}
  report  .surject/reports/20261013T215433018Z-write_file.md
```

Each catch also produces a one-page report you can share
(`surject report` lists them). Secrets are always redacted from everything
Surject prints or writes.

`surject watch` attaches automatically to **LangChain / LangGraph**,
**CrewAI**, **AutoGen**, the **OpenAI Agents SDK**, and any **MCP** client
built on the Python SDK. No code changes needed.

## MCP servers (Claude Desktop, Claude Code, Cursor, ...)

Put Surject in front of any stdio MCP server, in any language. In your MCP
client's config, prefix the server command with `surject mcp`:

```json
{
  "mcpServers": {
    "files": {
      "command": "surject",
      "args": ["mcp", "--policy", "/path/to/surject.yaml", "--",
               "npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"]
    }
  }
}
```

Every `tools/call` is checked before it reaches the server. In enforce mode
a blocked call never reaches the server; the model gets an error result
explaining why. Pass `--policy`, since MCP clients usually start servers
from a different folder.

## Claude Code

Run Surject as a Claude Code `PreToolUse` hook. `surject hook claude-code
--print-config` prints the snippet for `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*",
        "hooks": [{ "type": "command", "command": "surject hook claude-code" }] }
    ]
  }
}
```

Rules use Claude Code's tool names (`Write`, `Edit`, `Bash`, `Read`,
`mcp__<server>__<tool>`). In enforce mode a blocked call is denied and Claude
is told why; in monitor mode you get a warning and Claude Code's normal
permission flow continues.

## Policy

```yaml
mode: monitor          # or enforce: block the call instead of just flagging it
unknown_tools: allow   # or warn / block for tools not listed below

paths:                 # applies to every tool, whatever it's called
  protect: ["**/.env", "**/*.pem", "~/.ssh/**"]   # no tool may touch these
  read_only: ["./.git/**", "./data/raw/**"]       # readable, not changeable

tools:                 # rules for specific tools by name
  http_get:
    params:
      url: { regex: "^https://api\\.example\\.com/" }
    rate_limit: { max: 30, per: 60s }
  drop_table: block
```

`surject init` writes a version with every option explained, and
`surject validate` checks your edits.

## Using it from code

```python
import biject_surject as surject

@surject.guard
def write_file(path: str, content: str) -> None:
    ...
```

`surject.guard` uses the nearest `surject.yaml`. In enforce mode a blocked
call raises `surject.PolicyViolation` instead of running.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
.venv/bin/pytest
```

Integration tests against the real frameworks live in `tests/integration`
and skip when a framework isn't installed. See `docs/DESIGN.md` for the
architecture.
