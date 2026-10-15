# Policy reference

Surject reads `surject.yaml` from the current folder or the nearest parent
folder. Commands that start from elsewhere (MCP clients, hooks) take
`--policy /path/to/surject.yaml`. Check your edits with `surject validate`.
Every error names the exact spot, e.g.
`tools.http_get.params.url.regex: invalid regular expression`.

```yaml
version: 1
mode: monitor
unknown_tools: allow

paths:
  protect: ["**/.env", "~/.ssh/**"]
  read_only: ["./.git/**", "./data/raw/**"]

tools:
  http_get:
    params:
      url: { regex: "^https://api\\.example\\.com/" }
    rate_limit: { max: 30, per: 60s }
  drop_table: block

rate_limit: { max: 120, per: 60s }

redact:
  names: [patient_id]
  patterns: ["MRN-\\d+"]
```

## `mode`

| Value | What happens on a violation |
|---|---|
| `monitor` (default) | Reported; the call runs. Surject never changes what your agent does. |
| `enforce` | Reported, and the call is stopped. The agent gets an error explaining why. |

If Surject itself fails while checking, monitor mode lets the call run and
enforce mode blocks it.

## `unknown_tools`

What to do with tools not listed under `tools:`.

| Value | Meaning |
|---|---|
| `allow` (default) | Ignore them. Path rules still apply. |
| `warn` | Flag every call to an unlisted tool, even in enforce mode. Good for finding out what your agent actually calls. |
| `block` | Treat them as violations: a strict allowlist. |

## `paths`: rules for every tool

Path rules apply to every tool, whatever your framework calls it
(`write_file`, `Write`, `fs_write`, `save`, …).

| Key | Meaning |
|---|---|
| `protect` | No tool may touch these, whether reading or writing. |
| `read_only` | Tools may read these but not change them. |
| `params` | Extra argument names to treat as paths. |

**Which arguments count as paths.** Arguments are matched by name:
`path`, `file_path`, `filePath`, `filename`, `file`, `dest`, `destination`,
`source`, `directory`, `folder`, `cwd`, and anything ending in `_path`,
`_file`, `_dir`, `_directory`, `_filename` or `_folder`. Nested arguments
count too (`edits[0].path`). `file://` URIs count; `http(s)://` URLs don't.

**Shell commands.** Arguments named `command`, `cmd`, `script`, `shell` (and
similar) are split into words, and each word is checked as a path, so
`cat .env` is caught. This splits the text into words; it doesn't run a
shell, so paths built at runtime (`cat $FILE`) aren't resolved.

**Does a tool change files?** `read_only` needs to know, and Surject decides
like this:
- For path arguments, from the tool's name. `Write`, `Edit`,
  `delete_file`, `move`, `apply_patch` and `create_or_update_file` change
  files; `Read`, `Glob`, `list_directory` and `search_files` don't. **If the
  name doesn't make it clear, Surject assumes the tool changes files**, so a
  wrong guess means an extra flag, never a miss.
- For commands, from the command itself: `rm`, `mv`, `cp`, `tee`, `touch`,
  `chmod`, `sed -i`, `git reset` / `checkout` / `clean` / …, PowerShell
  `Remove-Item` / `Set-Content` / …, and output redirects. `2>&1` and
  `> /dev/null` don't count.

## Patterns

Used by `paths`, `glob` and `not_glob`.

| Pattern | Matches |
|---|---|
| `./data/**` | `data` itself and everything under it |
| `./data/*` | Files directly in `data` |
| `**/.env` | `.env` in any folder |
| `**/*.pem` | Any `.pem` file, anywhere |
| `~/.ssh/**` | Your home folder's `.ssh` |
| `data/?.csv` | `data/a.csv`, not `data/ab.csv` |

- Relative patterns are relative to the folder `surject.yaml` is in. An
  absolute path inside that folder matches them too, so
  `/home/you/proj/data/raw/x` matches `./data/raw/**`.
- `..` and `.` segments are resolved before matching, so
  `out/../data/raw/x` still matches `./data/raw/**`.
- Unlike in a shell, `*` matches dotfiles (`**/*.env` matches `.env`), which
  is the safer default for deny rules.
- Windows drive paths match case-insensitively.

## `tools`: rules for specific tools

Keyed by the tool's name as your framework reports it. Each tool is
`allow`, `block`, or a mapping:

```yaml
tools:
  delete_database: block
  list_files: allow
  query:
    params:
      table: { one_of: [users, orders], required: true }
      limit: { type: integer, max: 1000 }
    rate_limit: { max: 10, per: 1m }
```

### Parameter constraints

| Constraint | Meaning |
|---|---|
| `required: true` | Must be present. |
| `type` | `string`, `integer`, `number`, `boolean`, `array`, `object` or `null`. `true` isn't a number. |
| `equals` | Exactly this value. |
| `one_of` | One of a list of values. |
| `regex` | Must match. The pattern can match anywhere in the value; anchor with `^` and `$` to require the whole value. |
| `not_regex` | Must not match anywhere in the value. Use it for "never contains …" rules. |
| `glob` | Path must be inside one of these patterns. |
| `not_glob` | Path must not be inside any of these patterns. |
| `min`, `max` | Numeric bounds, inclusive. |
| `max_length` | Max length of a string, list or mapping. |

Rule regexes use a deliberately simple dialect, so a verified checker can
implement them exactly: character classes, alternation, repetition,
anchors, `` and a leading `(?i)` for case-insensitive matching all work.
Lookarounds, backreferences, conditional groups and other flags are
rejected by `surject validate`. Write "must not contain X" as
`not_regex: X` instead of a negative lookahead. (`redact.patterns` has no
such limits.)

Parameters you don't list aren't checked. A violation names the exact rule
that failed, e.g. `tools.query.params.limit.max`.

## Rate limits

```yaml
tools:
  send_email:
    rate_limit: { max: 5, per: 1h }   # per tool
rate_limit: { max: 120, per: 60s }     # all tools combined
```

`per` takes `500ms`, `30s`, `5m`, `1h` or `1d`. Every attempt counts toward
the limit, including calls that were flagged or blocked.

## `redact`

Secrets are always hidden from everything Surject prints or writes. This
can't be turned off. Surject recognizes them by argument name (`token`,
`password`, `api_key`, `authorization`, …), by shape (OpenAI/Anthropic
`sk-…`, GitHub `ghp_…`, AWS `AKIA…`, JWTs, private keys, `user:pass@` in
URLs, emails, …), and by randomness (long mixed-case strings with high
entropy).

Add your own:

```yaml
redact:
  names: [patient_id, ssn]      # argument names to always hide
  patterns: ["MRN-\\d+"]        # regexes to hide anywhere in text
```

Hidden values show as `[REDACTED:<reason>]`, so a report still says that
something was there and why it was hidden.
