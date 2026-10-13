# Surject with the OpenAI Agents SDK

Surject attaches to every `FunctionTool` the SDK creates, including tools
made with `@function_tool`, and checks each call before it runs. MCP
servers you connect to an agent are covered too, through the MCP client.
You don't change any code.

Tested against openai-agents 0.20 and 0.23.

## 1. Install and create a policy

```bash
pip install biject-surject      # in the same environment as your agent
surject init
```

## 2. Run your agent through Surject

```bash
surject watch -- python agent.py
```

## 3. Write rules for your tools

Rules use the tool's **name**: the function name for `@function_tool`, or
the `name_override` you pass.

```python
from agents import function_tool

@function_tool
def refund_order(order_id: str, amount_cents: int) -> str:
    """Refund part or all of an order."""
    ...
```

```yaml
mode: enforce

tools:
  refund_order:
    params:
      order_id: { regex: "^ord_[A-Za-z0-9]+$", required: true }
      amount_cents: { type: integer, min: 1, max: 5000 }
    rate_limit: { max: 10, per: 1h }
```

## What your agent sees in enforce mode

In this SDK, an exception raised outside your tool function propagates
out of the agent run instead of going back to the model, so Surject
doesn't raise here. A blocked call instead **returns the reason as the
tool's output**, which the model reads and can act on:

```
surject blocked tool 'refund_order': 'amount_cents' must be <= 5000
```

The tool function never runs.

## Troubleshooting

- **No `[surject] watching` line:** Surject isn't installed in the Python
  that runs your agent.
- **A rule never fires:** check the tool name with `unknown_tools: warn`.
