# Surject in front of an MCP server

`surject mcp` is a small proxy that sits between any MCP client (Claude
Desktop, Claude Code, Cursor, your own app) and any stdio MCP server,
written in any language. Every `tools/call` is checked before it reaches
the server. Everything else passes through untouched.

Tested with the official MCP Python SDK, 1.x and 2.x, on both ends.

## 1. Install and create a policy

```bash
pip install biject-surject
cd ~/surject-policies/files && surject init   # any folder you like
```

MCP clients usually start servers from their own working folder, so give
Surject the policy's full path with `--policy`.

## 2. Put Surject in front of the server

Prefix the server's command with `surject mcp --policy <file> --`. For
example, the filesystem server in Claude Desktop's
`claude_desktop_config.json`:

**Before**

```json
{
  "mcpServers": {
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/research"]
    }
  }
}
```

**After**

```json
{
  "mcpServers": {
    "files": {
      "command": "surject",
      "args": ["mcp", "--policy", "/Users/you/surject-policies/files/surject.yaml", "--",
               "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/research"]
    }
  }
}
```

Other clients (Cursor's `mcp.json`, Claude Code's `.mcp.json`) use the same
`command` / `args` shape. If `surject` isn't on the client's `PATH`, use
its full path (`which surject` / `where surject`).

## 3. Write rules for the server's tools

Rules use the MCP tool names exactly as the server reports them.

```yaml
mode: enforce

paths:
  read_only: ["/Users/you/research/raw/**"]

tools:
  move_file: block
```

Path rules work here too. Absolute paths match absolute patterns, and
paths inside the policy's folder also match `./` patterns.

## What the client sees in enforce mode

A blocked call never reaches the server. Surject answers the client itself
with a tool error result (`isError: true`) that explains why. The model
reads it like any other tool error:

```
surject blocked tool 'write_file': 'path' is inside /Users/you/research/raw/**, which is read-only, and 'write_file' looks like it changes files
```

In monitor mode the call is forwarded and the catch is reported.

## Where the alerts go

stdout carries the MCP protocol, so Surject never prints to it. Alerts go
to stderr, which most clients write to their MCP log. Every catch is also
in `.surject/` next to the policy file. `surject report` there lists them.

## Troubleshooting

- **The server won't start:** run the same command in a terminal,
  `surject mcp --policy ... -- npx ...`, and read the error.
- **`no surject.yaml found`:** pass `--policy` with a full path.
