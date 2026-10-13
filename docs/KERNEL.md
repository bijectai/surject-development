# Checker contract (kernel spec), version 1

This document defines the contract every Surject **checker** implements:
the Python reference checker that ships today, and the Lean kernel that
will replace it for users who need verified enforcement. Policies,
adapters and reports never change when the checker does; only this
contract is shared.

**Normative sources.** This document plus the conformance vectors in
`tests/conformance/`. Where prose and vectors disagree, the vectors win and
the prose is a bug. A checker conforms when it produces the expected
verdict for every vector.

**Out of scope.** Parsing `surject.yaml` (the Python front end does it and
emits the policy document below), redaction, reporting, history storage,
and natural-language rendering of verdicts.

## 1. The three documents

All three are JSON. Strings are Unicode. Numbers follow JSON (integers and
floats; `true` is not a number).

### 1.1 Policy (`surject-policy/1`)

Produced once per policy file by the front end. Raw patterns, no
pre-compiled anything: the checker owns all matching semantics.

```json
{
  "format": "surject-policy/1",
  "mode": "monitor",
  "unknown_tools": "allow",
  "root": "/home/ada/project",
  "tools": {
    "write_file": {
      "blocked": false,
      "params": [
        {
          "name": "path",
          "required": false, "type": null,
          "equals": null, "one_of": null,
          "regex": null, "not_regex": null,
          "glob": [], "not_glob": ["./data/raw/**"],
          "min": null, "max": null, "max_length": null
        }
      ],
      "rate_limit": {"max": 30, "per_seconds": 60.0}
    },
    "rm": {"blocked": true, "params": [], "rate_limit": null}
  },
  "rate_limit": null,
  "paths": {
    "protect": ["**/.env", "~/.ssh/**"],
    "read_only": ["./.git/**"],
    "extra_params": []
  },
  "vocabulary": { "...": "see section 6" }
}
```

| Field | Meaning |
|---|---|
| `mode` | `monitor` or `enforce`. |
| `unknown_tools` | `allow`, `warn` or `block`. |
| `root` | Absolute project folder, already normalized (section 3), or `null`. |
| `tools.<name>.blocked` | `true` for `tool: block`. |
| `tools.<name>.params` | A **list**, in policy order. Order matters because it sets the order violations are reported in, and JSON objects don't guarantee order. Each entry has a `name`. |
| `params.<p>.equals` | `null`, or `{"value": <any JSON>}`, because the expected value may itself be `null`. |
| `params.<p>.one_of` | `null` or a list of JSON values. |
| `params.<p>.regex`, `not_regex` | `null` or `{"pattern": "...", "ignore_case": bool}`. The pattern excludes the leading `(?i)`, which becomes `ignore_case`. Dialect in section 5. |
| `rate_limit` | `null` or `{"max": int ≥ 1, "per_seconds": float > 0}`. |
| `vocabulary` | Word lists used by sections 4.3 to 4.5. Part of the version: a checker must reject a policy whose vocabulary differs from the one it implements. |

### 1.2 Request (`surject-request/1`)

One per tool call.

```json
{
  "format": "surject-request/1",
  "tool": "Write",
  "params": {"file_path": "/home/ada/project/.env", "content": "x"},
  "home": "/home/ada",
  "recent": {
    "tool": {"60.0": 3},
    "all": {"60.0": 41}
  }
}
```

- `params` are the **raw** arguments. They are never redacted, since the
  checker needs real values. They are never logged by the checker.
- `home` is used for `~` expansion (section 3).
- `recent` holds rate-limit counts, so the checker stays stateless.
  `recent.tool[w]` is the number of earlier calls **to this tool** within
  the last `w` seconds; `recent.all[w]` is the same across all tools. The
  front end supplies a key for every window the policy uses for this
  tool, and for the global limit. Keys are `per_seconds` written as a
  JSON number string with at least one decimal (`"60.0"`). If a count is
  missing, that limit is not checked.

### 1.3 Verdict (`surject-verdict/1`)

```json
{
  "format": "surject-verdict/1",
  "decision": "flag",
  "checker": "lean-kernel@1.0.0",
  "violations": [
    {"rule": "paths.protect[0]", "severity": "block", "param": "file_path",
     "label": "file_path", "message": "optional human text"}
  ],
  "evidence": null
}
```

- `decision` is `allow`, `flag` or `block` (section 2.6).
- `violations` are listed in the order section 2 produces them. Each has
  a `rule` id, a `severity` (`warn` or `block`), the top-level `param`
  (or `null`), and the `label` (the argument path, e.g. `edits[0].path`,
  or `null`). `message` is optional; the front end renders its own if
  absent.
- `evidence` is reserved for the kernel (a proof term hash, certificate,
  or ledger reference). The reference checker emits `null`.

Conformance compares `decision` and the ordered list of
`(rule, severity, param, label)`; it doesn't compare `message`,
`checker` or `evidence`.

## 2. Checking a request

Given policy **P** and request **R**, with **T** = `P.tools[R.tool]` (may
be absent):

### 2.1 Tool rule
1. If T is absent and `P.unknown_tools` is `warn` or `block`, emit
   `unknown_tools` with severity `warn` or `block` respectively, `param`
   and `label` null.
2. If T is present and `T.blocked`, emit `tools.<tool>` with severity
   `block`. **Skip 2.2 and 2.3.**
3. If T is present and not blocked, check each listed param (2.2), in the
   order the policy lists them.

### 2.2 Parameter constraints
For param `p` with rule `C`, `where` = `tools.<tool>.params.<p>`; every
violation has severity `block`, `param` = `p`, `label` = `p`. Let `v` =
`R.params[p]` if present.

- **Missing:** if `v` is absent: emit `<where>.required` if
  `C.required`; then stop checking `p`.
- Then, in this order, each independently:
  1. `type`: `<where>.type` if `v` isn't of the type. `string`, `boolean`
     and `null` are as in JSON. `integer` means a JSON number with no
     fractional part that isn't a boolean. `number` means any JSON number
     that isn't a boolean. `array` and `object` are as in JSON.
  2. `equals`: `<where>.equals` if `v` ≠ `equals.value` (JSON equality;
     `1 == 1.0`).
  3. `one_of`: `<where>.one_of` if `v` equals no element.
  4. `regex`: `<where>.regex` if `v` isn't a string, or the pattern
     matches **nowhere** in `v` (search semantics).
  5. `not_regex`: `<where>.not_regex` if `v` isn't a string, or the
     pattern matches **anywhere** in `v`.
  6. `glob` / `not_glob`: if either is non-empty and `v` isn't a string,
     emit `<where>.glob` (or `<where>.not_glob` if `glob` is empty) once.
     Otherwise let `n` = normalize(v) (section 3). Emit `<where>.glob` if
     `glob` is non-empty and no pattern matches `n`. Then, for each
     `not_glob[i]` that matches `n`, emit `<where>.not_glob[i]`.
  7. `min` / `max`: if either is set and `v` isn't a number, emit
     `<where>.min` (or `.max` if `min` is unset) once. Otherwise emit
     `<where>.min` if `v < min`, then `<where>.max` if `v > max`.
  8. `max_length`: `<where>.max_length` if `v` isn't a string, array or
     object, or its length (Unicode code points / elements / keys) exceeds
     it.

### 2.3 Path rules
Skipped if T is blocked, or if `protect` and `read_only` are both empty.

1. **Collect targets**, in order:
   - Every path argument from section 4.1, as `(label, top, value)`, with
     `writes` = *undecided*.
   - Then, for every command argument from section 4.4, the words from
     section 4.4 as `(label, top, word)`, with `writes` =
     command_changes_files (section 4.5).
2. For each target, in order, let `n` = normalize(value):
   - For each `protect[i]` matching `n`, unless `(label, protect, i)` was
     already emitted: emit `paths.protect[i]`, severity `block`.
   - For each `read_only[i]` matching `n`, unless `(label, read_only, i)`
     was already emitted: if `writes` is *undecided*, use
     tool_changes_files(R.tool) (section 4.3). If it's true, emit
     `paths.read_only[i]`, severity `block`.

   `param` = `top`, `label` = `label`.

### 2.4 Rate limits
After 2.1 to 2.3, if T is present with a `rate_limit` `L` and
`R.recent.tool` has `L.per_seconds`: emit `tools.<tool>.rate_limit` if
`count + 1 > L.max`. Then the same for `P.rate_limit` using
`R.recent.all`, emitting `rate_limit`. Severity `block`; param and label
null.

### 2.5 Ordering
Violations are emitted in exactly the order above: 2.1, then 2.2 (params
in policy order, constraints in listed order), then 2.3, then 2.4.

### 2.6 Decision
- No violations: `allow`.
- `P.mode` is `enforce` and any violation has severity `block`: `block`.
- Otherwise: `flag`.

## 3. Path normalization

`normalize(value)`, applied to argument values and to every glob pattern
(patterns before compiling):

1. **Home:** if `value` is exactly `~`, or starts with `~/` or `~\`,
   replace the `~` with `R.home`. (`~user` is not expanded.)
2. **Separators:** replace every `\` with `/`.
3. **Segments:** POSIX `normpath`:
   - Keep a leading `/`; keep exactly two leading slashes if there are
     exactly two (`//server/share`); collapse three or more to one.
   - Split the rest on `/`. Drop empty and `.` segments. A `..` removes
     the previous segment if there is one and it isn't `..`; at the start
     of an absolute path it is dropped; at the start of a relative path it
     is kept.
   - If nothing is left: `/` (or `//`) for absolute paths, and the empty
     string for relative paths.
4. **Root-relative:** if `P.root` is set (already normalized; a root of
   `/` never applies), let `a`,
   `b` = the value and the root, both case-folded if the root starts with
   a drive (`[A-Za-z]:/`). If `a == b`, the result is the empty string; if
   `a` starts with `b + "/"`, the result is the value with the root and
   that `/` removed (original case kept).

## 4. Finding paths, commands and intent

### 4.1 Path arguments
Walk `R.params`. For each top-level key `k` with value `x`: visit
`(x, label=k, top=k, is_path=is_path_param(k), depth=0)`. Visiting:

- depth > 4: stop.
- string: if `is_path`, yield it after URI handling. Values starting with
  `file://` have that prefix removed; any other value containing `://` is
  skipped.
- object: for each key `j`, visit
  `(value, label + "." + j, top, is_path_param(j), depth + 1)`.
- array: for each index `i`, visit
  `(value, label + "[" + i + "]", top, is_path, depth + 1)`; arrays
  inherit `is_path`.
- anything else: ignore.

`is_path_param(name)`: let `s` = words(name) joined with `_` (4.2). True
if `s` is in `vocabulary.path_param_names`, ends with any of
`vocabulary.path_param_suffixes`, or `name` or `s` is in
`P.paths.extra_params`.

### 4.2 Words
`words(name)`: split `name` on runs of non-ASCII-alphanumerics; in each
part, take matches of `[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+` in order;
lowercase each. So `NotebookEdit` → `notebook`, `edit`;
`mcp__fs__read_file` → `mcp`, `fs`, `read`, `file`; `LS` → `ls`.

### 4.3 Does the tool change files?
`tool_changes_files(tool)`: let `W` = the set of words(tool). True if `W`
meets `vocabulary.write_words`; otherwise false if `W` meets
`vocabulary.read_words`; **otherwise true**.

### 4.4 Commands
Command arguments are top-level keys only, where `words(k)` joined with
`_` is in `vocabulary.command_param_names`. A string value is the command.
An array value uses its string elements, each POSIX-shell-quoted and
joined with spaces; other values are ignored.

**Splitting a command into words:**
- If the command contains no `\`: POSIX mode. Split on unquoted
  whitespace. `'...'` and `"..."` group text, and the quotes are removed.
  Adjacent quoted and unquoted parts join into one word (`a"b"c` → `abc`).
  `#` is an ordinary character.
- If it contains `\`: non-POSIX mode. Split on whitespace outside quotes,
  keeping quote characters, then strip `"` and `'` from both ends of each
  word.
- If quotes are unbalanced, split on whitespace.

**Words that could be paths**, from the split result, in order:
1. Remove a leading redirect operator matching `^(\d*>>?|&>>?|<)`.
2. Strip `; | & ( )` from both ends.
3. Skip empty words and the separators `; && || | & ( ) { }`.
4. A word starting with `-`: if it contains `=`, keep the part after the
   first `=`; otherwise skip it.

### 4.5 Does the command change files?
`command_changes_files(cmd)` is true if any of these hold:
- an **output redirect** matching
  `(?<![<>=!-])\d*>>?(?!=)\s*(&?)([^\s;|&]*)` whose `&` group is empty and
  whose target, lowercased, isn't in `vocabulary.null_targets`;
- a split word whose last `/`-segment, lowercased, is in
  `vocabulary.write_commands`;
- a word `git` followed directly by a word in `vocabulary.write_git`;
- a word in `vocabulary.in_place_programs` with a word starting `-i`, or
  equal to `--in-place`, among the next three words.

## 5. Globs and the regex dialect

### 5.1 Globs
On the normalized pattern (section 3, with `R.home`), anchored at both
ends:
- A trailing `/**` matches the folder itself or anything inside it:
  `(?:/.*)?`.
- `**/` elsewhere matches zero or more whole folders: `(?:.*/)?`.
- `**` elsewhere matches anything: `.*`.
- `*` matches within one segment: `[^/]*`. `?` matches one non-`/`
  character.
- Every other character is literal.
- Case-insensitive if the pattern, normalized *without* the root step,
  starts with a drive (`[A-Za-z]:/`), or if `P.root` starts with a drive
  (a project on Windows).
- Patterns go through the full normalization, *including* the
  root-relative step, so an absolute pattern inside the project matches
  project-relative values.
- `*` matches leading dots: `**/*.env` matches `.env`.

Globs are matched against the normalized value (full match).

### 5.2 Regex dialect
Rule regexes (`regex`, `not_regex`) are limited to what a finite automaton
can do:
- Allowed: literals, escapes, `.`, character classes (including `\d \w \s`
  and their negations, using Unicode semantics), alternation, groups
  (capturing, non-capturing or named, all just for grouping), `* + ? {m,n}`
  (greedy or lazy; laziness doesn't change search results), anchors
  `^ $ \A \Z`, and word boundaries `\b \B`. `.` does not match `\n`.
  `^` and `\A` match only at the start; `$` matches at the end or just
  before a final `\n`; `\Z` only at the very end.
  `ignore_case` is Unicode simple case folding.
- Not allowed (the front end rejects them): lookarounds, backreferences,
  conditional groups, atomic groups, possessive repeats, and every flag
  except a leading `(?i)`.

## 6. Vocabulary

The word lists in `policy.vocabulary`. The reference values are in
`src/biject_surject/paths.py` and are emitted verbatim. Keys:
`path_param_names`, `path_param_suffixes`, `write_words`, `read_words`,
`command_param_names`, `write_commands`, `write_git`, `in_place_programs`,
`null_targets`. Every list is sorted.

## 7. Talking to an external checker

An external checker (the Lean kernel) is a program that reads and writes
**one JSON object per line** on stdin and stdout. Surject starts it once
and keeps it running.

```
→ {"type": "hello", "contract": 1}
← {"type": "hello", "checker": "lean-kernel@1.0.0", "contract": 1}
→ {"type": "load", "policy_id": "p1", "policy": {...surject-policy/1...}}
← {"type": "loaded", "policy_id": "p1"}
→ {"type": "check", "policy_id": "p1", "request": {...surject-request/1...}}
← {"type": "verdict", "verdict": {...surject-verdict/1...}}
```

Any request may instead be answered with
`{"type": "error", "message": "..."}`. Messages are processed in order, one
at a time.

**Failure handling** (front end): if the checker can't start, answers
with `error`, sends a malformed reply, or doesn't answer within 5
seconds, then:
- in `monitor` mode, Surject warns once and uses the Python reference;
- in `enforce` mode, it blocks the call.

## 8. Versioning
Any change to sections 1 to 6 that can change a verdict bumps the format
versions (`surject-policy/2`, …) and the conformance vectors. Additive
fields that a checker can safely ignore don't.
