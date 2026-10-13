# Surject with Claude Code

Surject runs as a Claude Code `PreToolUse` hook. Claude Code asks it about
every tool call before running it: file edits, shell commands, web
fetches, MCP tools.

## 1. Install and create a policy

```bash
pip install biject-surject
cd your-project && surject init
```

## 2. Add the hook

Print the settings snippet:

```bash
surject hook claude-code --print-config
```

Add it to `.claude/settings.json` in your project, or `~/.claude/settings.json`
to cover every project:

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

The hook uses the `surject.yaml` nearest to the folder Claude Code is
working in. Projects without one aren't checked, and the hook stays silent.

## 3. Write rules

The default `paths:` rules already cover Claude Code's file tools and the
paths inside `Bash` commands, so `cat .env` and `rm -rf .git` are caught
out of the box. For tool-specific rules, use Claude Code's tool names:
`Write`, `Edit`, `Read`, `Bash`, `WebFetch`, and `mcp__<server>__<tool>` for
MCP tools.

```yaml
mode: enforce

paths:
  read_only: ["./data/raw/**", "./migrations/**"]

tools:
  WebFetch:
    params:
      url: { regex: "^https://(docs\\.python\\.org|developer\\.mozilla\\.org)/" }
  mcp__prod_db__execute_sql: block
  Bash:
    rate_limit: { max: 200, per: 1h }
```

## What happens

| Verdict | Claude Code |
|---|---|
| Allowed | Nothing; the call goes ahead. |
| Flagged (monitor mode) | You see a warning; Claude Code's normal permission prompts still apply. |
| Blocked (enforce mode) | The call is denied, and Claude is told why so it can choose another approach. |

Rate limits work across calls, even though each hook call is a separate
process: Surject keeps recent call times in `.surject/history.json`.

## Troubleshooting

- **Nothing happens:** run `surject hook claude-code --print-config` to
  check that `surject` is on your `PATH`. Then make sure the project has a
  `surject.yaml` (`surject validate`).
- **A broken policy:** you'll see a warning that calls aren't being
  checked. Run `surject validate` to find the problem.
