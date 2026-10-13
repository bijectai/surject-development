# Surject with CrewAI

Surject attaches to CrewAI's tool execution (`CrewStructuredTool.invoke` /
`ainvoke`, which crews call, plus `BaseTool.run` / `arun`). Tools made with
`@tool`, `BaseTool` subclasses and CrewAI's built-in tools are all covered,
including subclasses that override `run`. You don't change any code.

Tested against crewai 1.6 and 1.15.

## 1. Install and create a policy

```bash
pip install biject-surject      # in the same environment as your crew
surject init
```

## 2. Run your crew through Surject

```bash
surject watch -- python crew.py
```

`surject watch -- crewai run` works too, as long as Surject is installed
in the environment that command uses.

## 3. Write rules for your tools

Rules use the tool's **name**. For `@tool("...")` that's the string you
pass; for a `BaseTool` subclass, it's the class's `name` field.

```python
from crewai.tools import tool

@tool("publish_post")
def publish_post(channel: str, text: str) -> str:
    """Publish a post to a channel."""
    ...
```

```yaml
paths:
  read_only: ["./research/sources/**"]

tools:
  publish_post:
    params:
      channel: { one_of: ["#drafts"] }   # only drafts until a human reviews
      text: { max_length: 2000 }
    rate_limit: { max: 3, per: 1h }
```

Built-in tools such as `FileWriterTool` use their own names and argument
names. With `unknown_tools: warn`, Surject will flag every unlisted tool by
its exact name so you can write rules for it. Path rules cover them
without knowing their names.

## What your crew sees in enforce mode

A blocked call raises `biject_surject.PolicyViolation` before the tool
runs. CrewAI treats it like any other tool error: the agent sees the
message and usually picks another approach.

```
surject blocked tool 'publish_post': 'channel' must be one of ['#drafts']
```

## Troubleshooting

- **No `[surject] watching` line:** Surject isn't installed in the Python
  that runs your crew.
- **A rule never fires:** check the tool name with `unknown_tools: warn`.
