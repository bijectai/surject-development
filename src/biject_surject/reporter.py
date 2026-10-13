"""Surfacing violations: terminal alert, event log, and shareable report.

Silent on allowed calls. On a violation it prints a short alert to stderr,
appends one JSON line to ``.surject/events.jsonl`` and writes a one-page
Markdown report to ``.surject/reports/``.

Every value that leaves this module goes through ``Redactor`` first: params,
violation messages, even the tool name. ``render`` produces the redacted
view once and every output path uses only that view.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TextIO, Union

from biject_surject.checker import BLOCK, FLAG, Verdict
from biject_surject.redact import Redactor

STATE_DIR = ".surject"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


class Reporter:
    def __init__(self, root: Union[str, Path, None] = None,
                 redactor: Optional[Redactor] = None,
                 stream: Optional[TextIO] = None,
                 policy_source: Optional[str] = None,
                 write_files: bool = True):
        self.state_dir = Path(root or ".") / STATE_DIR
        self.redactor = redactor or Redactor()
        self.stream = stream
        self.policy_source = policy_source
        self.write_files = write_files

    # -- public ------------------------------------------------------------

    def report(self, verdict: Verdict) -> Optional[Dict[str, Any]]:
        """Surface a verdict. Returns the redacted event, or None if allowed."""
        if verdict.ok:
            return None
        event = self.render(verdict)
        if self.write_files:
            event["report"] = self._write_report(event)
            self._append_event(event)
        self._alert(event)
        return event

    def render(self, verdict: Verdict) -> Dict[str, Any]:
        """The redacted, JSON-safe view of a verdict. The only thing any
        output path is allowed to see."""
        r = self.redactor
        when = datetime.fromtimestamp(verdict.call.timestamp, tz=timezone.utc)
        tool = r.text(verdict.call.name)
        return {
            "id": f"{when:%Y%m%dT%H%M%S}{when.microsecond // 1000:03d}Z-"
                  f"{_UNSAFE_FILENAME.sub('_', tool)[:40]}",
            "time": when.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "tool": tool,
            "decision": verdict.decision,
            "mode": verdict.mode,
            "checker": verdict.checker,
            # File name only: reports get shared, and a full path leaks the
            # user's username and folder layout.
            "policy": r.text(_basename(self.policy_source)) if self.policy_source else None,
            "params": r.value(dict(verdict.call.params)),
            "violations": [
                {
                    "rule": r.text(v.rule),
                    "message": r.text(v.message),
                    "severity": v.severity,
                    "param": r.text(v.param) if v.param else None,
                }
                for v in verdict.violations
            ],
        }

    # -- outputs -----------------------------------------------------------

    def _alert(self, event: Dict[str, Any]) -> None:
        stream = self.stream or sys.stderr
        lines = [f"[surject] {_headline(event)}"]
        for v in event["violations"]:
            lines.append(f"  rule    {v['rule']}")
            lines.append(f"  why     {v['message']}")
        lines.append(f"  params  {_compact_json(event['params'])}")
        if event.get("report"):
            lines.append(f"  report  {event['report']}")
        try:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
        except Exception:
            pass  # never let output problems reach the agent

    def _append_event(self, event: Dict[str, Any]) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_dir / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _write_report(self, event: Dict[str, Any]) -> Optional[str]:
        try:
            reports = self.state_dir / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            path = reports / f"{event['id']}.md"
            n = 1
            while path.exists():
                n += 1
                path = reports / f"{event['id']}-{n}.md"
            path.write_text(render_markdown(event), encoding="utf-8")
            return str(path)
        except OSError:
            return None


def render_markdown(event: Dict[str, Any]) -> str:
    """A one-page report that makes sense to someone with zero context."""
    outcome = {
        BLOCK: "blocked; the call did not run",
        FLAG: "flagged; the call was allowed to run"
              + (" (monitor mode)" if event["mode"] == "monitor" else ""),
    }.get(event["decision"], event["decision"])
    lines = [
        f"# Surject caught `{event['tool']}`",
        "",
        f"- **When:** {event['time']}",
        f"- **Tool:** `{event['tool']}`",
        f"- **Verdict:** {outcome}",
        f"- **Checked by:** {event['checker']}",
    ]
    if event.get("policy"):
        lines.append(f"- **Policy:** `{event['policy']}`")
    lines += [
        "",
        "## What the agent tried",
        "",
        "```json",
        json.dumps(event["params"], indent=2, ensure_ascii=False, default=str),
        "```",
        "",
        "## Why it was caught",
        "",
    ]
    for v in event["violations"]:
        lines.append(f"- `{v['rule']}`: {v['message']}")
    lines += [
        "",
        "---",
        "Caught by [Surject](https://pypi.org/project/biject-surject/). "
        "Secrets in this report were redacted automatically.",
        "",
    ]
    return "\n".join(lines)


def _headline(event: Dict[str, Any]) -> str:
    if event["decision"] == BLOCK:
        return f"BLOCKED {event['tool']}"
    suffix = " (monitor mode, call allowed)" if event["mode"] == "monitor" else " (call allowed)"
    return f"FLAGGED {event['tool']}{suffix}"


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def _compact_json(value: Any, limit: int = 300) -> str:
    s = json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[: limit - 3] + "..."
