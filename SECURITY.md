# Security

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's
private vulnerability reporting (**Security → Report a vulnerability** on
this repository), and include steps to reproduce if you can. We'll
acknowledge reports within a few working days.

Useful reports include:
- a tool call that should break a policy but isn't caught (a bypass);
- a secret that reaches Surject's output without being redacted;
- Surject breaking or changing an agent's behaviour in `monitor` mode.

## What Surject promises

Surject is a **monitor**. It helps you notice and stop unintended agent
actions. It is not a sandbox, and it doesn't defend against code that is
deliberately trying to evade it.

1. **Secrets don't leave through Surject.** Everything Surject prints or
   writes (terminal alerts, `.surject/events.jsonl`, reports, exception
   messages, hook and proxy replies) is built from one redacted view of
   each call. Violation messages describe the rule that failed, never the
   value. Redaction can't be turned off.
2. **No network calls.** Surject reads your policy and writes to `.surject/`
   in your project. Nothing else.
3. **Monitor mode never changes agent behaviour.** If Surject fails
   internally, the call proceeds and a one-line warning is printed.
4. **Enforce mode fails closed.** A call Surject can't check is blocked.

## Known limits

These are design boundaries, not bugs. Reports that move them are still
welcome.

- **Same-process attachment.** `surject watch` and `@surject.guard` run
  inside the agent's own Python process. Code in that process can undo the
  hooks or call the underlying API directly. For a boundary the agent's
  code can't touch, use `surject mcp` (a separate process) or the Claude
  Code hook.
- **Framework hooks follow framework internals.** `surject watch` patches
  each framework's tool-execution method. A framework release that moves
  tool execution elsewhere would bypass it until Surject is updated. CI
  runs Surject against the latest release of every supported framework to
  catch this early.
- **Path arguments are found by name.** A tool that passes a path under an
  unusual argument name isn't checked by `paths:` rules unless you add the
  name with `paths.params`.
- **Shell commands are split into words, not run.** Quoting tricks
  (`cat .e""nv`) are resolved, but anything the shell computes isn't:
  variables (`cat $FILE`), wildcards (`cat .en?`), `eval`, or decoding at
  runtime.
- **"Changes files?" is a guess from names.** `read_only` decides from the
  tool's name, or for commands from the program. If the name doesn't make
  it clear, Surject treats the tool as changing files.
- **Redaction relies on recognizing secrets.** Secrets are recognized by
  argument name, known credential shapes, and randomness. An unusual
  low-randomness secret under an innocent name (`note: "hunter2"`) isn't
  recognized. Add names and patterns with `redact:`.
- **Paths in reports.** File paths the agent used appear in reports, since
  they're usually the point. Review them before sharing.

## Scope for a security review

The security-relevant code is small:

| File | What to look at |
|---|---|
| `src/biject_surject/redact.py` | Every secret shape that could get through |
| `src/biject_surject/reporter.py` | That every output path uses the redacted view |
| `src/biject_surject/checker.py`, `paths.py`, `policy.py` | Policy bypasses: path normalization, globbing, command tokenizing, write detection |
| `src/biject_surject/autopatch.py`, `_bootstrap/sitecustomize.py` | Attachment gaps, and anything that could break the host agent |
| `src/biject_surject/mcp_proxy.py` | Messages that could reach the server unchecked (batches, malformed JSON) |
| `src/biject_surject/hooks.py` | Claude Code hook output; history file handling |
