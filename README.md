# biject-surject

A one-command, lightweight monitor that catches when an AI agent does
something you didn't intend. No dashboard, no login, no telemetry. It only
speaks when something is wrong.

> **Status:** early development. The commands below are the target
> experience and are being built now.

```bash
pip install biject-surject
surject init     # write a default, commented policy file
surject watch    # attach to your agent's tool calls
```

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
.venv/bin/pytest
```
