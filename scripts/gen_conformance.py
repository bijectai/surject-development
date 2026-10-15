"""Generate the checker conformance vectors (docs/KERNEL.md).

    python scripts/gen_conformance.py           # rewrite tests/conformance/*.jsonl
    python scripts/gen_conformance.py --check   # exit 1 if they're out of date

Each vector is one JSON line:
    {"name": ..., "policy": <surject-policy/1>, "request": <surject-request/1>,
     "expected": {"decision": ..., "violations": [[rule, severity, param, label], ...]}}

Expected verdicts come from the Python reference checker (ir.check_request).
A conforming checker must produce the same decision and violation list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from biject_surject import ir  # noqa: E402
from biject_surject.policy import parse_policy  # noqa: E402

OUT_DIR = ROOT_DIR / "tests" / "conformance"

POSIX = {"root": "/home/ada/proj", "home": "/home/ada"}
WINDOWS = {"root": "C:/Users/ada/proj", "home": "C:/Users/ada"}

Call = Tuple[str, Dict[str, Any]]  # (tool, params)


def suite(name: str, policy_yaml: str, calls: List[Any], env=POSIX) -> Iterator[Dict[str, Any]]:
    policy = parse_policy(policy_yaml, root=env["root"], home=env["home"])
    doc = ir.policy_to_ir(policy)
    for i, c in enumerate(calls):
        tool, params = c[0], c[1]
        recent = c[2] if len(c) > 2 else {"tool": {}, "all": {}}
        request = {"format": ir.REQUEST_FORMAT, "tool": tool, "params": params,
                   "home": env["home"], "recent": recent}
        yield {
            "name": f"{name}/{i:02d}",
            "policy": doc,
            "request": request,
            "expected": ir.comparable(ir.check_request(doc, request)),
        }


def params_vectors() -> Iterator[Dict[str, Any]]:
    yield from suite("types", """
tools:
  t:
    params:
      s: {type: string}
      i: {type: integer}
      n: {type: number}
      b: {type: boolean}
      a: {type: array}
      o: {type: object}
      z: {type: "null"}
""", [
        ("t", {"s": "x", "i": 3, "n": 1.5, "b": False, "a": [], "o": {}, "z": None}),
        ("t", {"i": 3.0, "n": 2}),
        ("t", {"s": 1, "i": 3.5, "n": True, "b": 0, "a": {}, "o": [], "z": 0}),
        ("t", {"i": True, "n": "1"}),
        ("t", {}),
    ])
    yield from suite("required", "tools: {t: {params: {x: {required: true}, y: {type: string}}}}", [
        ("t", {"x": 1}), ("t", {}), ("t", {"y": 2}), ("t", {"x": None}),
    ])
    yield from suite("equals_one_of", """
tools:
  t:
    params:
      e: {equals: 1}
      n: {equals: null}
      o: {one_of: [a, 2, true, null, {k: [1]}]}
""", [
        ("t", {"e": 1, "n": None, "o": "a"}),
        ("t", {"e": 1.0, "o": 2.0}),
        ("t", {"e": True, "n": 0, "o": 1}),
        ("t", {"e": "1", "o": {"k": [1.0]}}),
        ("t", {"o": None}), ("t", {"o": False}), ("t", {"o": {"k": [2]}}),
    ])
    yield from suite("regex", r"""
tools:
  t:
    params:
      u: {regex: "^https://api\\.example\\.com/"}
      c: {regex: "(?i)select\\b"}
      d: {not_regex: "rm\\s+-rf"}
      w: {regex: "^\\w+$"}
      e: {regex: "x$"}
""", [
        ("t", {"u": "https://api.example.com/v1"}),
        ("t", {"u": "http://api.example.com/"}),
        ("t", {"u": "https://api.example.com.evil/"}),
        ("t", {"u": "xhttps://api.example.com/"}),
        ("t", {"c": "SELECT * from t"}), ("t", {"c": "selection"}), ("t", {"c": "x; Select 1"}),
        ("t", {"d": "ls -la"}), ("t", {"d": "rm   -rf /"}), ("t", {"d": "rm -r"}),
        ("t", {"u": 3, "d": None}),
        ("t", {"w": "héllo_1"}), ("t", {"w": "a b"}),
        ("t", {"e": "abx\n"}), ("t", {"e": "abx\n\n"}),
    ])
    yield from suite("bounds", """
tools:
  t:
    params:
      n: {min: 1, max: 10}
      lo: {min: 0}
      hi: {max: 5}
      l: {max_length: 3}
""", [
        ("t", {"n": 1, "lo": 0, "hi": 5, "l": "abc"}),
        ("t", {"n": 0}), ("t", {"n": 10.5}), ("t", {"n": "5"}), ("t", {"n": True}),
        ("t", {"lo": -1, "hi": 6}), ("t", {"lo": "x"}), ("t", {"hi": None}),
        ("t", {"l": "abcd"}), ("t", {"l": [1, 2, 3, 4]}), ("t", {"l": {"a": 1, "b": 2, "c": 3, "d": 4}}),
        ("t", {"l": "héé"}), ("t", {"l": 12345}),
    ])
    yield from suite("param_globs", """
tools:
  w:
    params:
      p: {glob: ["./out/**", "./tmp/*.csv"], not_glob: ["./out/secret/**", "**/*.key"]}
""", [
        ("w", {"p": "out/a.txt"}), ("w", {"p": "out"}), ("w", {"p": "tmp/a.csv"}),
        ("w", {"p": "tmp/x/a.csv"}), ("w", {"p": "elsewhere/a"}),
        ("w", {"p": "out/secret/a"}), ("w", {"p": "out/k.key"}), ("w", {"p": "x/k.key"}),
        ("w", {"p": 7}), ("w", {}),
    ])
    yield from suite("param_order", """
tools:
  t:
    params:
      z: {required: true}
      a: {type: string, max_length: 1, regex: "^x"}
      m: {one_of: [1]}
""", [
        ("t", {"a": 5, "m": 2}), ("t", {"a": "yy"}),
    ])


def tool_vectors() -> Iterator[Dict[str, Any]]:
    for unknown in ("allow", "warn", "block"):
        for mode in ("monitor", "enforce"):
            yield from suite(f"unknown_{unknown}_{mode}", f"""
mode: {mode}
unknown_tools: {unknown}
tools: {{rm: block, ok: allow}}
""", [("rm", {}), ("ok", {}), ("other", {}), ("other", {"path": "x"})])
    yield from suite("blocked_skips_rest", """
mode: enforce
unknown_tools: allow
tools: {rm: block}
paths: {protect: ["**/.env"]}
""", [("rm", {"path": ".env"}), ("x", {"path": ".env"})])


def path_vectors() -> Iterator[Dict[str, Any]]:
    yield from suite("normalize", """
unknown_tools: allow
paths:
  protect: ["./data/**", "**/.env", "~/.ssh/**", "/etc/**", "/home/ada/proj/secrets/**"]
""", [
        ("r", {"path": "data"}), ("r", {"path": "./data/x"}), ("r", {"path": "data/../data/x"}),
        ("r", {"path": "out/../data/x"}), ("r", {"path": "data//x/./y"}), ("r", {"path": "database"}),
        ("r", {"path": "x/../../data/y"}), ("r", {"path": "../proj/data/x"}),
        ("r", {"path": "/home/ada/proj/data/x"}), ("r", {"path": "/home/ada/proj"}),
        ("r", {"path": "/home/ada/project/data/x"}), ("r", {"path": "/home/ada/proj/../proj/data"}),
        ("r", {"path": "data\\x"}), ("r", {"path": ".env"}), ("r", {"path": "a/b/.env"}),
        ("r", {"path": ".env.local"}), ("r", {"path": "~/.ssh/id_rsa"}), ("r", {"path": "~"}),
        ("r", {"path": "~\\.ssh\\config"}), ("r", {"path": "~ada/.ssh/id_rsa"}),
        ("r", {"path": "/home/ada/.ssh/id_rsa"}), ("r", {"path": "/etc/passwd"}),
        ("r", {"path": "/../etc/passwd"}), ("r", {"path": "//etc/passwd"}), ("r", {"path": "///etc/passwd"}),
        ("r", {"path": "secrets/k"}), ("r", {"path": "/home/ada/proj/secrets/k"}),
        ("r", {"path": ""}), ("r", {"path": "."}),
        ("r", {"path": "../.ssh/id_rsa"}), ("r", {"path": "../../ada/.ssh/k"}),
        ("r", {"path": "x/../../proj/data/y"}), ("r", {"path": "//etc//passwd"}),
        ("r", {"path": "../../../../../etc/passwd"}),
    ])
    yield from suite("globs", """
unknown_tools: allow
paths:
  protect: ["./a/*", "**/*.pem", "b/?.txt", "c/**/d", "**", "e/(x)+[y].txt"]
""", [
        ("r", {"path": "a/1"}), ("r", {"path": "a/1/2"}), ("r", {"path": "k.pem"}),
        ("r", {"path": ".pem"}), ("r", {"path": "x/y/z.pem"}), ("r", {"path": "b/1.txt"}),
        ("r", {"path": "b/12.txt"}), ("r", {"path": "c/d"}), ("r", {"path": "c/x/y/d"}),
        ("r", {"path": "e/(x)+[y].txt"}), ("r", {"path": "e/xx.txt"}),
    ])
    yield from suite("finding_paths", """
unknown_tools: allow
paths:
  protect: ["**/.env"]
  params: [notebook, weirdArg]
""", [
        ("t", {"path": ".env"}), ("t", {"file_path": ".env"}), ("t", {"filePath": ".env"}),
        ("t", {"FilePath": ".env"}), ("t", {"output_dir": ".env"}), ("t", {"backup_path": ".env"}),
        ("t", {"destination": ".env"}), ("t", {"content": ".env"}), ("t", {"url": ".env"}),
        ("t", {"pathological": ".env"}), ("t", {"notebook": ".env"}), ("t", {"weird_arg": ".env"}),
        ("t", {"weirdArg": ".env"}), ("t", {"uri": "file:///x/.env"}), ("t", {"uri": "https://x/.env"}),
        ("t", {"paths": ["ok", ".env"]}), ("t", {"edits": [{"path": "ok"}, {"path": "a/.env"}]}),
        ("t", {"o": {"p": {"q": {"r": {"path": ".env"}}}}}),
        ("t", {"o": {"p": {"q": {"r": {"s": {"path": ".env"}}}}}}),
        ("t", {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"path": ".env"}}}}}}}}),
        ("t", {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"path": ".env"}}}}}}}}}),
        ("t", {"WeirdArg": ".env"}), ("t", {"weirdarg": ".env"}),
        ("t", {"path": ".env", "file": ".env"}), ("t", {"path": 5}),
    ])
    yield from suite("read_only", """
unknown_tools: allow
paths:
  read_only: ["./data/raw/**", "./.git/**"]
""", [
        ("Read", {"file_path": "data/raw/x"}), ("Write", {"file_path": "data/raw/x"}),
        ("read_file", {"path": "data/raw/x"}), ("write_file", {"path": "data/raw/x"}),
        ("NotebookEdit", {"notebook_path": "data/raw/n.ipynb"}), ("Glob", {"path": ".git"}),
        ("mcp__fs__read_text_file", {"path": ".git/HEAD"}),
        ("mcp__github__create_or_update_file", {"path": ".git/HEAD"}),
        ("frobnicate", {"path": ".git/HEAD"}), ("read_and_write", {"path": ".git/HEAD"}),
        ("LS", {"path": ".git"}), ("apply_patch", {"path": ".git/config"}),
        ("Write", {"file_path": "data/rawish/x"}),
    ])
    yield from suite("both_rules", """
unknown_tools: allow
paths:
  protect: ["**/.env"]
  read_only: ["./cfg/**"]
tools:
  w:
    params:
      path: {glob: ["./out/**"]}
""", [("w", {"path": "cfg/.env"}), ("w", {"path": "out/x"}), ("x", {"path": "cfg/.env"})])


def command_vectors() -> Iterator[Dict[str, Any]]:
    yield from suite("commands", """
unknown_tools: allow
paths:
  protect: ["**/.env", "~/.ssh/**"]
  read_only: ["./.git/**", "./data/raw/**"]
""", [
        ("Bash", {"command": "cat .env"}), ("Bash", {"command": "ls -la && git status"}),
        ("Bash", {"command": "cat 'my dir/.env'"}), ("Bash", {"command": 'cat "a b/.env"'}),
        ("Bash", {"command": 'cat .e""nv'}), ("Bash", {"command": "cat $(echo .env)"}),
        ("Bash", {"command": "cat .en?"}), ("Bash", {"command": "cat $F"}),
        ("Bash", {"command": "python run.py --config=.env"}), ("Bash", {"command": "x --env .env"}),
        ("Bash", {"command": "echo hi >.env"}), ("Bash", {"command": "ls;cat ~/.ssh/id_rsa"}),
        ("Bash", {"command": "rm -rf .git"}), ("Bash", {"command": "grep -r TODO .git"}),
        ("Bash", {"command": "git checkout -- .git/config"}), ("Bash", {"command": "git log .git"}),
        ("Bash", {"command": "echo x > data/raw/a"}), ("Bash", {"command": "head data/raw/a"}),
        ("Bash", {"command": "cat data/raw/a 2>&1"}), ("Bash", {"command": "cat data/raw/a > /dev/null"}),
        ("Bash", {"command": "cat data/raw/a >> out.txt"}), ("Bash", {"command": "sed -i s/a/b/ data/raw/a"}),
        ("Bash", {"command": "sed s/a/b/ data/raw/a"}), ("Bash", {"command": "/bin/rm data/raw/a"}),
        ("Bash", {"command": "cp data/raw/a out/"}), ("Bash", {"command": "test 1 -ge 0 && ls data/raw"}),
        ("Bash", {"command": 'type "C:\\proj\\.env"'}), ("Bash", {"command": "del data\\raw\\a"}),
        ("Bash", {"command": "Remove-Item data/raw/a"}), ("Bash", {"command": "cat \"unbalanced .env"}),
        ("shell", {"cmd": ["cat", "/home/ada/proj/.env"]}), ("run", {"script": "rm data/raw/a"}),
        ("x", {"commandLine": "cat .env"}), ("x", {"description": "cat .env"}),
        ("Bash", {"command": "cat .env .env"}), ("Bash", {"command": 5}),
        ("Bash", {"command": "cat ../.ssh/id_rsa"}), ("Bash", {"command": "rm -rf ../proj/.git"}),
    ])


def rate_vectors() -> Iterator[Dict[str, Any]]:
    yield from suite("rates", """
unknown_tools: allow
tools:
  t: {rate_limit: {max: 2, per: 60s}}
  u: allow
rate_limit: {max: 5, per: 1h}
""", [
        ("t", {}, {"tool": {"60.0": 0}, "all": {"3600.0": 0}}),
        ("t", {}, {"tool": {"60.0": 1}, "all": {"3600.0": 4}}),
        ("t", {}, {"tool": {"60.0": 2}, "all": {"3600.0": 4}}),
        ("t", {}, {"tool": {"60.0": 2}, "all": {"3600.0": 5}}),
        ("u", {}, {"tool": {}, "all": {"3600.0": 5}}),
        ("t", {}, {"tool": {}, "all": {}}),
        ("t", {}, {"tool": {"30.0": 9}, "all": {"60.0": 9}}),
    ])


def windows_vectors() -> Iterator[Dict[str, Any]]:
    yield from suite("windows", """
unknown_tools: allow
paths:
  protect: ["./data/**", "~/.ssh/**", "C:/Secrets/**", "**/.env"]
  read_only: ["./.git/**"]
""", [
        ("r", {"path": "C:\\Users\\ada\\proj\\data\\x"}), ("r", {"path": "c:/users/ada/proj/DATA/x"}),
        ("r", {"path": "DATA\\x"}), ("r", {"path": "~\\.ssh\\id_rsa"}),
        ("r", {"path": "C:\\Users\\ada\\.SSH\\config"}), ("r", {"path": "c:\\secrets\\k"}),
        ("r", {"path": "D:\\secrets\\k"}), ("r", {"path": "C:\\Users\\ada\\proj\\cfg\\.ENV"}),
        ("Write", {"file_path": "C:\\Users\\ada\\proj\\.GIT\\config"}),
        ("Bash", {"command": "del C:\\Users\\ada\\proj\\.git\\config"}),
    ], env=WINDOWS)


AREAS = {
    "params": params_vectors,
    "tools": tool_vectors,
    "paths": path_vectors,
    "commands": command_vectors,
    "rates": rate_vectors,
    "windows": windows_vectors,
}


def render(area: str) -> str:
    return "".join(json.dumps(v, ensure_ascii=False, sort_keys=True) + "\n" for v in AREAS[area]())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the files are out of date")
    args = ap.parse_args(argv)
    stale = []
    for area in AREAS:
        path = OUT_DIR / f"{area}.jsonl"
        text = render(area)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT_DIR)} ({text.count(chr(10))} vectors)")
    if stale:
        print("out of date (run scripts/gen_conformance.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
