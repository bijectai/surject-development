# Surject with AutoGen

Surject attaches to AutoGen's tool execution (`BaseTool.run_json` in
`autogen-core`), which `AssistantAgent` and other AgentChat agents use to
run tools. `FunctionTool`, your own `BaseTool` subclasses and tools loaded
from MCP servers through `autogen-ext` are all covered. You don't change
any code.

Tested against autogen-core 0.7.

## 1. Install and create a policy

```bash
pip install biject-surject      # in the same environment as your agents
surject init
```

## 2. Run your agents through Surject

```bash
surject watch -- python team.py
```

## 3. Write rules for your tools

Rules use the tool's **name**. For a plain function passed to an agent,
that's the function name; for `FunctionTool(fn, name=...)` it's the
`name` you pass.

```python
from autogen_core.tools import FunctionTool

async def transfer_funds(account: str, amount: float) -> str:
    ...

transfer = FunctionTool(transfer_funds, description="Move money between accounts.")
```

```yaml
mode: enforce

tools:
  transfer_funds:
    params:
      account: { regex: "^TEST-" }      # sandbox accounts only
      amount: { type: number, min: 0, max: 100 }
    rate_limit: { max: 5, per: 1h }
```

## What your agents see in enforce mode

A blocked call raises `biject_surject.PolicyViolation` before the tool
runs. AgentChat agents report tool errors back to the model as the tool
result, so the model sees:

```
surject blocked tool 'transfer_funds': 'amount' must be <= 100
```

## Troubleshooting

- **No `[surject] watching` line:** Surject isn't installed in the Python
  that runs your agents.
- **A rule never fires:** check the tool name with `unknown_tools: warn`.
