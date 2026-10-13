# Surject design (v0 proposal)

Status: **proposal, needs sign-off**. This turns `IDEAS.md` into concrete
decisions for the first usable release. Open questions are marked **Q**.

## Shape of the system

```
agent tool call ──► adapter ──► checker(policy) ──► verdict ──► reporter
                      │                                 │
                      └──── (enforce mode) block ◄──────┘
```

- **Adapter**: the only part that knows about a specific agent runtime. It
  turns "a tool is about to be called" into a `ToolCall(name, params)` and
  acts on the verdict.
- **Checker**: pure function `check(policy, call, history) -> Verdict`. No
  I/O. This is the piece the Lean kernel can replace later without changing
  policies or adapters.
- **Reporter**: silent on allow; on a violation, prints a short alert and
  writes a shareable report file. Never touches the network.

Everything in the core is framework-agnostic; adapters are thin.

## Policy file (`surject.yaml`)

```yaml
version: 1
mode: monitor          # monitor = flag only; enforce = block the call too

unknown_tools: warn    # tools not listed below: allow | warn | block

tools:
  read_file:
    params:
      path: { glob: ["./data/**", "./src/**"] }

  write_file:
    params:
      path: { not_glob: ["./data/raw/**"] }   # never overwrite raw data

  http_get:
    params:
      url: { regex: "^https://api\\.example\\.com/" }
    rate_limit: { max: 30, per: 60s }

  delete_file: block   # shorthand: always a violation

rate_limit: { max: 120, per: 60s }   # across all tools
```

Parameter constraints (small on purpose; extensible later):
`equals`, `one_of`, `regex`, `glob`, `not_glob`, `min`, `max`,
`max_length`, `type`, `required`.

A call is a **violation** if: the tool is blocked; the tool is unknown and
`unknown_tools` is `warn`/`block`; any listed param fails its constraint; or
a rate limit is exceeded. Each violation names the exact rule that failed
(e.g. `tools.write_file.params.path.not_glob[0]`), which is what makes the
report readable without context.

`surject init` writes this file with every option commented.

## How `surject watch` attaches (the big decision)

"Attach to the agent's runtime" has no single universal mechanism. Options:

| Adapter | How it works | Covers | Effort |
|---|---|---|---|
| **A. Launcher** `surject watch -- python agent.py` | Runs the agent with an injected `sitecustomize` that patches known frameworks' tool-call entry points before user code runs | Python agents on LangChain/LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, plus anything using our decorator | Medium; each framework is a small patch module |
| **B. MCP proxy** `surject watch -- <mcp server cmd>` | Sits between an MCP client and server over stdio and inspects `tools/call` | Any MCP client (Claude Desktop/Code, Cursor, …), any language | Medium |
| **C. Claude Code hook** | `PreToolUse` hook calls `surject check` | Claude Code only | Small |
| **D. Library** `@surject.guard` / `surject.check(...)` | User wraps their own tools | Anything in Python, but needs code changes | Small, and A/B/C build on it anyway |

**Proposal**: build D first (it is the core API the others call), then **A**
as the headline `surject watch` experience, then B, then C.

**Q1.** Agree with D → A → B → C? Or should MCP (B) come first, since it is
language-agnostic and matches "one command" most literally?

## Monitor vs. enforce

`IDEAS.md` says Surject "flags" and "surfaces", but also talks about
"blocked tool calls".

**Proposal**: default `mode: monitor` (never changes agent behaviour, so it
is safe to install and forget); `mode: enforce` blocks by raising/returning
an error to the agent in place of running the tool.

**Q2.** Is monitor-by-default right?

## Reports

- Every violation appends one JSON line to `.surject/events.jsonl`.
- Every violation writes `.surject/reports/<timestamp>-<tool>.md`: what the
  agent tried, the params, the rule it broke, the verdict, the timestamp,
  and the checker that decided (`python-reference@<version>`, later
  `lean-kernel@…`).
- `surject report` lists recent violations; `surject report <id>` prints one
  ready to paste.
- Params are recorded as-is by default. **Q3.** Should reports redact
  secret-looking values (tokens, keys, emails) by default, given the
  researchers-on-sensitive-data audience? Proposal: yes, with
  `redact: false` to opt out.

## Out of scope for v0

Lean kernel, signed ledger, audit chain, shareable links (anything that
needs a network call), dashboards, accounts, telemetry.

## Engineering constraints

- Python ≥ 3.9, one runtime dependency (`pyyaml`).
- The checker must add negligible latency (no I/O, compiled regex/globs).
- Adapters must fail open in monitor mode: a Surject bug never breaks the
  user's agent.
