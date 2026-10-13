# Reports and sharing

Surject stays silent until a tool call breaks your policy. Then three
things happen.

**1. A short alert in your terminal** (stderr):

```
[surject] FLAGGED write_file (monitor mode, call allowed)
  rule    paths.read_only[1]
  why     'path' is inside ./data/raw/**, which is read-only, and 'write_file' looks like it changes files
  params  {"path": "data/raw/experiment_07.csv", "content": "overwritten"}
  report  .surject/reports/20261013T215433018Z-write_file.md
```

**2. A line in `.surject/events.jsonl`**: one JSON object per catch, for
scripts and dashboards.

**3. A one-page report in `.surject/reports/`**: what the agent tried,
which rule it broke, what Surject did about it, when, and which checker
decided. It's written to make sense to someone who has never heard of your
project.

`surject init` adds `.surject/` to your `.gitignore` if you have one.

## Looking at catches

```bash
surject report              # list recent catches
surject report 20261013T21  # print one (any unique prefix of its id)
surject report last         # print the most recent
```

## Sharing a catch

```bash
surject report last --short --copy
```

This copies a few lines that work anywhere: Slack, an issue, a post.

```
My agent tried to call `write_file` on data/raw/experiment_07.csv, and Surject caught it.
Why: 'path' is inside ./data/raw/**, which is read-only, and 'write_file' looks like it changes files
Verdict: flagged (monitor mode, so the call still ran) (2026-10-13 21:54 UTC)
Caught by Surject: pip install biject-surject
```

Drop `--short` to copy the full Markdown report instead. Without `--copy`,
either one is printed.

## What's safe to share

Everything in a report has already been through redaction. API keys,
tokens, passwords, private keys and emails show as `[REDACTED:<reason>]`.
Reports show only the policy's file name, never its full path, so your
username and folder layout stay private. File paths your agent used *do*
appear, since they're usually the point of the story; check them before
posting if they're sensitive.
