"""Command-line entry point for ``surject``."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from biject_surject import __version__
from biject_surject.monitor import POLICY_ENV, find_policy
from biject_surject.policy import DEFAULT_POLICY_FILE, PolicyError, load_policy
from biject_surject.reporter import STATE_DIR, render_markdown, render_snippet
from biject_surject.templates import DEFAULT_POLICY


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="surject",
        description="Catch when your agent does something you didn't intend.",
    )
    parser.add_argument(
        "--version", action="version", version=f"surject {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("init", help="create a commented default surject.yaml")
    p.add_argument("--force", action="store_true", help="overwrite an existing policy")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate", help="check a policy file for errors")
    p.add_argument("path", nargs="?", help="policy file (default: nearest surject.yaml)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser(
        "watch", help="run an agent with Surject attached: surject watch -- python agent.py",
    )
    p.add_argument("--policy", help="policy file (default: nearest surject.yaml)")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="the command that starts your agent")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser(
        "mcp", help="proxy an MCP server over stdio: surject mcp -- npx some-mcp-server",
    )
    p.add_argument("--policy", help="policy file (default: nearest surject.yaml)")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="the command that starts the MCP server")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("hook", help="run as an agent hook: surject hook claude-code")
    p.add_argument("agent", choices=["claude-code"], help="which agent is calling the hook")
    p.add_argument("--policy", help="policy file (default: nearest surject.yaml to the agent's cwd)")
    p.add_argument("--print-config", action="store_true",
                   help="print the settings.json snippet that installs this hook, then exit")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("report", help="list caught violations, or print/share one")
    p.add_argument("id", nargs="?",
                   help="report id (or a unique prefix), or `last` for the most recent")
    p.add_argument("-n", type=int, default=20, help="how many recent events to list")
    p.add_argument("--short", action="store_true",
                   help="a few plain lines to paste into chat or a post, instead of the full report")
    p.add_argument("--copy", action="store_true", help="copy the report to the clipboard")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


# -- commands ----------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(DEFAULT_POLICY_FILE)
    if path.exists() and not args.force:
        _err(f"{path} already exists (use --force to overwrite)")
        return 1
    path.write_text(DEFAULT_POLICY, encoding="utf-8")
    print(f"created {path}")

    gitignore = Path(".gitignore")
    if gitignore.is_file():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if not any(l.strip().rstrip("/") in (STATE_DIR, "/" + STATE_DIR) for l in lines):
            with open(gitignore, "a", encoding="utf-8") as f:
                prefix = "" if not lines or lines[-1] == "" else "\n"
                f.write(f"{prefix}# Surject event log and reports\n{STATE_DIR}/\n")
            print(f"added {STATE_DIR}/ to .gitignore")
    print("next: edit the tool names to match your agent, then `surject validate`")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else find_policy()
    if path is None:
        _err("no surject.yaml found here or in any parent folder (run `surject init`)")
        return 1
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        _err(str(exc))
        return 1
    path_rules = len(policy.paths.protect) + len(policy.paths.read_only)
    print(f"{path}: ok (mode {policy.mode}; {path_rules} path rule(s), "
          f"{len(policy.tools)} tool rule(s); unknown tools {policy.unknown_tools})")
    return 0


BOOTSTRAP_DIR = Path(__file__).resolve().parent / "_bootstrap"


def cmd_watch(args: argparse.Namespace) -> int:
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        _err("nothing to run; usage: surject watch -- python agent.py")
        return 2

    path = Path(args.policy) if args.policy else find_policy()
    if path is None:
        _err("no surject.yaml found here or in any parent folder (run `surject init`)")
        return 1
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        _err(str(exc))
        return 1

    env = dict(os.environ)
    env["SURJECT_WATCH"] = "1"
    env[POLICY_ENV] = str(path.resolve())
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(BOOTSTRAP_DIR), env.get("PYTHONPATH", "")) if p
    )
    print(f"[surject] watching ({policy.mode} mode, policy {path}); "
          f"silent unless something breaks a rule", file=sys.stderr)

    try:
        proc = subprocess.Popen(cmd, env=env)
    except FileNotFoundError:
        _err(f"command not found: {cmd[0]}")
        return 127
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # The agent got the same Ctrl+C; give it a moment to exit cleanly.
        try:
            return proc.wait(timeout=10)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            proc.kill()
            return 130


def cmd_mcp(args: argparse.Namespace) -> int:
    # stdout carries the MCP protocol: everything human goes to stderr.
    from biject_surject.mcp_proxy import McpProxy
    from biject_surject.monitor import Monitor

    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        _err("nothing to run; usage: surject mcp -- <mcp server command>")
        return 2
    path = Path(args.policy) if args.policy else find_policy()
    if path is None:
        _err("no surject.yaml found; pass --policy /path/to/surject.yaml "
             "(MCP clients often start servers from another folder)")
        return 1
    try:
        monitor = Monitor.from_file(path)
    except PolicyError as exc:
        _err(str(exc))
        return 1
    try:
        return McpProxy(monitor, cmd).run()
    except FileNotFoundError:
        _err(f"command not found: {cmd[0]}")
        return 127
    except KeyboardInterrupt:
        return 130


def cmd_hook(args: argparse.Namespace) -> int:
    from biject_surject.hooks import SETTINGS_SNIPPET, run_claude_code_hook

    if args.print_config:
        print("# Add to .claude/settings.json (project) or ~/.claude/settings.json (all projects):")
        print(json.dumps(SETTINGS_SNIPPET, indent=2))
        return 0
    return run_claude_code_hook(sys.stdin, sys.stdout, args.policy)


def cmd_report(args: argparse.Namespace) -> int:
    policy_path = find_policy()
    root = policy_path.parent if policy_path else Path.cwd()
    log = root / STATE_DIR / "events.jsonl"
    events = _read_events(log)

    if args.id:
        if args.id == "last":
            matches = events[-1:]
        else:
            matches = [e for e in events if e.get("id", "").startswith(args.id)]
        if not matches:
            _err("nothing caught yet" if args.id == "last" else f"no report matching {args.id!r}")
            return 1
        if len({e["id"] for e in matches}) > 1:
            _err(f"{args.id!r} matches {len(matches)} reports; use more of the id")
            return 1
        text = (render_snippet if args.short else render_markdown)(matches[-1])
        if args.copy:
            from biject_surject.clipboard import copy
            tool = copy(text)
            if tool:
                print(f"copied to clipboard ({'short' if args.short else 'full'} report)",
                      file=sys.stderr)
                return 0
            _err("no clipboard tool found; printing instead")
        print(text)
        return 0
    if args.copy or args.short:
        _err("pass a report id (or `last`) to copy or shorten a report")
        return 2

    if not events:
        print("nothing caught yet")
        return 0
    for e in events[-args.n:]:
        rules = ", ".join(v.get("rule", "?") for v in e.get("violations", []))
        print(f"{e.get('id')}  {e.get('decision', '?'):5}  {e.get('tool')}  {rules}")
    return 0


# -- helpers -------------------------------------------------------------------


def _read_events(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _err(message: str) -> None:
    print(f"surject: {message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
