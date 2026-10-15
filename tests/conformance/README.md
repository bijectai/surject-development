# Checker conformance vectors

Test cases every Surject checker must pass, including the Lean kernel.
Together with [`docs/KERNEL.md`](../../docs/KERNEL.md), they define the
checker contract. Where the two disagree, these vectors win.

## Format

One JSON object per line:

```json
{
  "name": "commands/12",
  "policy":   { "format": "surject-policy/1",  "...": "..." },
  "request":  { "format": "surject-request/1", "...": "..." },
  "expected": {
    "decision": "flag",
    "violations": [["paths.read_only[0]", "block", "command", "command"]]
  }
}
```

`expected.violations` is the ordered list of
`[rule, severity, param, label]`. A checker conforms when, for every
vector, its verdict's `decision` and that list are exactly equal.
`message`, `checker` and `evidence` aren't compared.

## Files

| File | Covers |
|---|---|
| `params.jsonl` | Types, required, equals / one_of (JSON equality), regex dialect, bounds, param globs, violation order |
| `tools.jsonl` | Unknown tools, blocked tools, monitor vs. enforce decisions |
| `paths.jsonl` | Path cleanup, glob semantics, finding path arguments, read_only and tool intent |
| `commands.jsonl` | Splitting shell commands into words, redirects, write detection |
| `rates.jsonl` | Per-tool and global rate limits from `recent` counts |
| `windows.jsonl` | Drive paths, backslashes, case-insensitive projects |

POSIX vectors use root `/home/ada/proj` and home `/home/ada`. Windows
vectors use `C:/Users/ada/proj` and `C:/Users/ada`.

## Changing them

The vectors are generated from the Python reference checker:

```bash
python scripts/gen_conformance.py           # regenerate
python scripts/gen_conformance.py --check   # CI: fail if out of date
```

To add a case, add it to `scripts/gen_conformance.py` and regenerate.
**Review the expected verdict before committing:** a vector records what
the reference checker does, bugs included. Two path bypasses were found
exactly this way. A change that alters an existing expected verdict is a
contract change (KERNEL.md section 8).
