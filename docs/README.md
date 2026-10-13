# Surject documentation

Surject watches the tool calls your AI agent makes and tells you when one
breaks a rule you set: a write to your raw data, a read of `.env`, a call
to a tool it shouldn't use. It runs on your machine, needs no account, and
stays silent unless something is wrong.

## Start here

```bash
pip install biject-surject
surject init                      # writes surject.yaml with safe defaults
surject watch -- python agent.py  # run your agent with Surject attached
```

The default policy already protects secrets (`.env`, keys, `~/.ssh`,
`~/.aws`) and keeps `.git` read-only, for every tool your agent has,
whatever it's called. Edit `surject.yaml` to add your own rules, then run
`surject validate`.

## Pick your setup

| You use | Guide | How Surject attaches |
|---|---|---|
| LangChain / LangGraph | [guides/langgraph.md](guides/langgraph.md) | `surject watch` |
| CrewAI | [guides/crewai.md](guides/crewai.md) | `surject watch` |
| AutoGen | [guides/autogen.md](guides/autogen.md) | `surject watch` |
| OpenAI Agents SDK | [guides/openai-agents.md](guides/openai-agents.md) | `surject watch` |
| An MCP server (Claude Desktop, Cursor, any client) | [guides/mcp.md](guides/mcp.md) | `surject mcp` proxy |
| Claude Code | [guides/claude-code.md](guides/claude-code.md) | `PreToolUse` hook |
| Your own Python tools | [guides/python.md](guides/python.md) | `@surject.guard` |

## Reference

- [Policy reference](policy.md): every option in `surject.yaml`
- [Reports and sharing](reports.md): what a catch looks like and how to share it
- [Design](DESIGN.md): how Surject works and why

## Monitor vs. enforce

Every policy starts in `mode: monitor`. Surject reports what it catches and
lets the call run, so installing it can never break your agent. When you
trust your policy, switch to `mode: enforce`: blocked calls don't run, and
the agent is told why so it can choose another approach.

## Your data stays yours

Surject makes no network calls. Everything it writes goes to `.surject/` in
your project. Secrets (API keys, tokens, passwords, private keys, emails)
are redacted from everything it prints or writes, and this can't be turned
off.
