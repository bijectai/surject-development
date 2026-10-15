# Changelog

All notable changes to `biject-surject` are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]: planned as 1.0.0

The first real release. Everything below is new since the 0.0.1 placeholder.

### Policies
- `surject.yaml` policy file with `monitor` (flag only, the default) and
  `enforce` (block) modes. Every error names its exact location.
- **Path rules for every tool**, whatever it's called: `paths.protect` (no
  tool may touch these) and `paths.read_only` (readable, not changeable).
  Path arguments are found by name, including nested ones, and inside shell
  commands (`cat .env`, `rm -rf .git`).
- Per-tool rules: `required`, `type`, `equals`, `one_of`, `regex`,
  `not_regex`, `glob`, `not_glob`, `min`, `max`, `max_length`. Rule regexes
  use a simple dialect (no lookarounds or backreferences) that a verified
  checker can implement exactly; `allow` / `block` shorthands;
  `unknown_tools: allow | warn | block`.
- Per-tool and global rate limits.
- Paths are normalized before matching (`..`, `\`, `~`). Absolute paths
  inside the project match `./` rules. `dir/**` also matches `dir` itself.

### Checkers
- `checker: python | lean`: the Python reference checker, or an external
  verified checker (the Lean kernel) over a documented line protocol.
  If the kernel fails, monitor mode falls back to Python and enforce mode
  blocks.
- `docs/KERNEL.md` specifies the checker contract, and
  `tests/conformance/` holds 223 test cases every checker must pass.

### Connecting to agents
- `@surject.guard`, `surject.check` and `Monitor` for your own Python tools
  (sync, async, methods).
- `surject watch -- <command>`: attaches to LangChain/LangGraph, CrewAI,
  AutoGen, the OpenAI Agents SDK and Python MCP clients with no code
  changes. Tested in CI against the latest release of each.
- `surject mcp -- <server>`: stdio proxy in front of any MCP server, for any
  MCP client (Claude Desktop, Cursor, …).
- `surject hook claude-code`: Claude Code `PreToolUse` hook.

### Reports
- Silent unless something breaks a rule. On a catch: a terminal alert, a
  line in `.surject/events.jsonl`, and a one-page Markdown report.
- `surject report` lists catches. `surject report last --short --copy`
  copies a few plain lines that tell the story of the catch, ready to paste
  anywhere.

### Privacy and safety
- Secrets are redacted from everything Surject prints or writes. This
  can't be turned off. Secrets are recognized by name, by known credential
  shapes, and by randomness. Reports show only the policy's file name, not
  its full path.
- No network calls.
- Monitor mode never breaks the agent (fails open); enforce mode blocks
  calls it can't check (fails closed).

### CLI
- `surject init` writes a commented policy with safe defaults: protects
  `.env`, keys, `~/.ssh` and `~/.aws`, and keeps `.git` read-only.
- `surject validate` checks a policy and points at any error.

## [0.0.1] - 2026-10-12

- Placeholder release to reserve the package name.
