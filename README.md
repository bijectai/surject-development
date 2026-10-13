# biject-surject

A one-command, lightweight monitor that catches when an AI agent does
something you didn't intend. No dashboard, no login, no telemetry. It only
speaks when something is wrong.

> **Status:** early development, not yet released beyond a placeholder.

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
  rule    tools.write_file.params.path.not_glob[0]
  why     'path' must not be inside ./data/raw/**
  params  {"path": "data/raw/experiment_07.csv", "content": "overwritten"}
  report  .surject/reports/20261013T215433018Z-write_file.md
```

Each catch also produces a one-page report you can share
(`surject report` lists them). Secrets are always redacted from everything
Surject prints or writes.

`surject watch` attaches automatically to **LangChain / LangGraph**,
**CrewAI**, **AutoGen**, the **OpenAI Agents SDK**, and any **MCP** client
built on the Python SDK. No code changes needed.

## Policy

```yaml
mode: monitor          # or enforce: block the call instead of just flagging it
unknown_tools: allow   # or warn / block for tools not listed below
tools:
  write_file:
    params:
      path: { not_glob: ["./data/raw/**", "**/.env"] }
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
