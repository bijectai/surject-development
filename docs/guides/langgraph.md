# Surject with LangChain and LangGraph

Surject attaches to every LangChain tool (`BaseTool.run` / `arun`), which is
what LangGraph's `ToolNode` and LangChain agents call under the hood. Tools
made with `@tool`, `StructuredTool`, your own `BaseTool` subclasses, and
MCP tools loaded through `langchain-mcp-adapters` are all covered. You
don't change any code.

Tested against langchain-core 1.6.

## 1. Install and create a policy

```bash
pip install biject-surject      # in the same environment as your agent
surject init
```

## 2. Run your agent through Surject

```bash
surject watch -- python agent.py
```

Use whatever command normally starts your agent: `surject watch -- uv run
agent.py`, `surject watch -- python -m myproject.agent`, and so on.

## 3. Write rules for your tools

The default `paths:` rules already cover every tool. To add rules for a
specific tool, use the tool's **name**. For `@tool` that's the function
name, or the `name=` you passed:

```python
from langchain_core.tools import tool

@tool
def run_sql(query: str, database: str) -> str:
    """Run a read-only SQL query."""
    ...
```

```yaml
paths:
  read_only: ["./data/raw/**"]

tools:
  run_sql:
    params:
      database: { one_of: [analytics_replica] }
      query: { regex: "^\\s*select\\b" }   # (?i) at the start makes it case-insensitive
    rate_limit: { max: 60, per: 1m }
```

## What your agent sees in enforce mode

With `mode: enforce`, a blocked call raises `biject_surject.PolicyViolation`
(a `PermissionError`) before the tool runs. Its message says why:

```
surject blocked tool 'run_sql': 'database' must be one of ['analytics_replica']
```

How that reaches the model depends on your graph's tool error handling.
With `ToolNode`'s error handling on, the message goes back to the model as
the tool result, and it can try something else. Otherwise the exception
ends the run, as any tool error would.

## Without `surject watch`

If you start the agent some other way (a notebook, a web server, a test),
attach Surject in code before the agent runs:

```python
from biject_surject import autopatch
autopatch.install()   # uses the nearest surject.yaml
```

## Troubleshooting

- **No `[surject] watching` line:** Surject isn't installed in the Python
  that runs your agent. Install it in that environment.
- **A rule never fires:** check the tool name. Set `unknown_tools: warn`
  for a run, and Surject will flag every unlisted tool by its exact name.
- **Calls are reported twice:** they shouldn't be; a LangChain tool that
  wraps an MCP tool is reported once. Please open an issue if you see it.
