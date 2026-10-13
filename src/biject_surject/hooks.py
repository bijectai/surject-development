"""Claude Code adapter: ``surject hook claude-code``, run as a PreToolUse hook.

Claude Code runs the hook before every tool call, passing JSON on stdin
(``tool_name``, ``tool_input``, ``cwd``, ...). We answer on stdout:

- blocked (enforce mode): ``permissionDecision: "deny"`` with the reason,
  which Claude sees and can act on;
- flagged: a ``systemMessage`` warning shown to the user, no decision, so
  Claude Code's normal permission flow still applies;
- allowed: nothing.

Each hook call is a fresh process, so rate-limit history is kept in
``.surject/history.json`` next to the event log.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

from biject_surject.checker import BLOCK, FLAG, History
from biject_surject.monitor import Monitor, find_policy
from biject_surject.policy import Policy, PolicyError, load_policy
from biject_surject.redact import Redactor
from biject_surject.reporter import STATE_DIR, Reporter

HISTORY_FILE = "history.json"

SETTINGS_SNIPPET = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "*",
             "hooks": [{"type": "command", "command": "surject hook claude-code"}]}
        ]
    }
}


def run_claude_code_hook(stdin: TextIO, stdout: TextIO,
                         policy_path: Optional[str] = None) -> int:
    try:
        event = json.loads(stdin.read() or "{}")
    except ValueError:
        return 0  # not our input; never get in Claude Code's way
    if not isinstance(event, dict):
        return 0
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cwd = event.get("cwd") or None

    path = Path(policy_path) if policy_path else find_policy(cwd)
    if path is None:
        return 0  # no policy for this project: stay silent
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        _emit(stdout, {"systemMessage": f"surject: {exc}; tool calls are not being checked"})
        return 0

    root = Path(policy.root or ".")
    monitor = _hook_monitor(policy, root)
    persist = _uses_rate_limits(policy)
    if persist:
        monitor.history.restore(_load_history(root, monitor.history.horizon))
    verdict = monitor.check(tool, tool_input)
    if persist:
        _save_history(root, monitor.history.snapshot())

    r = monitor.redactor
    if verdict.ok:
        return 0
    reasons = "; ".join(r.text(v.message) for v in verdict.violations)
    rules = ", ".join(r.text(v.rule) for v in verdict.violations)
    name = r.text(tool)
    if verdict.decision == BLOCK:
        _emit(stdout, {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"surject blocked tool '{name}': {reasons}",
            },
            "systemMessage": f"surject blocked {name} ({rules})",
        })
    elif verdict.decision == FLAG:
        _emit(stdout, {"systemMessage": f"surject flagged {name}: {reasons} ({rules})"})
    return 0


def _hook_monitor(policy: Policy, root: Path) -> Monitor:
    # stderr from a hook only reaches Claude Code's debug log; the user sees
    # our systemMessage instead, so the reporter only writes files here.
    reporter = Reporter(root=root, redactor=Redactor.for_policy(policy),
                        stream=io.StringIO(), policy_source=policy.source)
    monitor = Monitor(policy, reporter)
    monitor.history = History(horizon=_horizon(policy))
    return monitor


def _horizon(policy: Policy) -> float:
    windows = [t.rate_limit.per for t in policy.tools.values() if t.rate_limit]
    if policy.rate_limit:
        windows.append(policy.rate_limit.per)
    return max(windows, default=1.0)


def _load_history(root: Path, horizon: float) -> List[Tuple[str, float]]:
    try:
        data = json.loads((root / STATE_DIR / HISTORY_FILE).read_text(encoding="utf-8"))
        cutoff = time.time() - horizon
        return [(str(name), float(ts)) for name, ts in data.get("calls", [])
                if isinstance(ts, (int, float)) and ts >= cutoff]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def _save_history(root: Path, calls: List[Tuple[str, float]]) -> None:
    try:
        (root / STATE_DIR).mkdir(parents=True, exist_ok=True)
        tmp = root / STATE_DIR / (HISTORY_FILE + ".tmp")
        tmp.write_text(json.dumps({"calls": calls}), encoding="utf-8")
        tmp.replace(root / STATE_DIR / HISTORY_FILE)
    except OSError:
        pass


def _uses_rate_limits(policy: Policy) -> bool:
    return policy.rate_limit is not None or any(t.rate_limit for t in policy.tools.values())


def _emit(stdout: TextIO, payload: Dict[str, Any]) -> None:
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()
