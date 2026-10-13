"""The Python reference checker.

``check(policy, call, history)`` is a pure function: it reads the policy, the
call and the call history and returns a verdict. It performs no I/O and never
mutates its inputs, which is what lets a different checker (e.g. the Lean
kernel) replace it later without touching policies or adapters.

Violation messages describe the *rule* that failed and never include the
parameter's value, so raw (possibly secret) values cannot leak through them.
Reporters show values only after redaction.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

from biject_surject import __version__
from biject_surject.paths import changes_files, path_args
from biject_surject.policy import ParamRule, Policy, RateLimit, normalize_path

CHECKER_ID = f"python-reference@{__version__}"

ALLOW = "allow"
FLAG = "flag"    # violation recorded and surfaced, call proceeds
BLOCK = "block"  # violation recorded and surfaced, call is stopped (enforce mode)


@dataclass(frozen=True)
class ToolCall:
    name: str
    params: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Violation:
    rule: str                     # e.g. "tools.write_file.params.path.not_glob[0]"
    message: str                  # describes the rule, never the value
    severity: str = BLOCK         # "warn" or "block"
    param: Optional[str] = None   # which param failed, if any


@dataclass(frozen=True)
class Verdict:
    call: ToolCall
    decision: str                 # allow | flag | block
    violations: Tuple[Violation, ...] = ()
    mode: str = "monitor"
    checker: str = CHECKER_ID

    @property
    def ok(self) -> bool:
        return not self.violations


class History:
    """Timestamps of past calls, for rate limits. Old entries are pruned."""

    def __init__(self, horizon: float = 86400.0):
        self.horizon = horizon
        self._all: Deque[float] = deque()
        self._by_tool: Dict[str, Deque[float]] = {}

    def record(self, call: ToolCall) -> None:
        self._all.append(call.timestamp)
        self._by_tool.setdefault(call.name, deque()).append(call.timestamp)
        cutoff = call.timestamp - self.horizon
        for q in [self._all, *self._by_tool.values()]:
            while q and q[0] < cutoff:
                q.popleft()

    def snapshot(self) -> List[Tuple[str, float]]:
        """All recorded (tool, timestamp) pairs, oldest first."""
        return sorted(((name, ts) for name, q in self._by_tool.items() for ts in q),
                      key=lambda c: c[1])

    def restore(self, calls: List[Tuple[str, float]]) -> None:
        for name, ts in sorted(calls, key=lambda c: c[1]):
            self.record(ToolCall(name, {}, timestamp=ts))

    def count(self, since: float, tool: Optional[str] = None) -> int:
        q = self._all if tool is None else self._by_tool.get(tool, ())
        return sum(1 for ts in q if ts > since)


def check(policy: Policy, call: ToolCall, history: Optional[History] = None) -> Verdict:
    violations: List[Violation] = []
    tool = policy.tools.get(call.name)

    if tool is None:
        if policy.unknown_tools != "allow":
            violations.append(Violation(
                rule="unknown_tools",
                message=f"tool '{call.name}' is not listed in the policy",
                severity="warn" if policy.unknown_tools == "warn" else BLOCK,
            ))
    elif tool.blocked:
        violations.append(Violation(
            rule=f"tools.{call.name}",
            message=f"tool '{call.name}' is blocked by policy",
        ))
    else:
        for pname, prule in tool.params.items():
            where = f"tools.{call.name}.params.{pname}"
            violations.extend(_check_param(pname, prule, call.params, where, policy.root))

    if tool is None or not tool.blocked:
        violations.extend(_check_paths(policy, call))

    if history is not None:
        if tool is not None and tool.rate_limit is not None:
            v = _check_rate(tool.rate_limit, call, history, call.name,
                            f"tools.{call.name}.rate_limit")
            if v:
                violations.append(v)
        if policy.rate_limit is not None:
            v = _check_rate(policy.rate_limit, call, history, None, "rate_limit")
            if v:
                violations.append(v)

    return Verdict(
        call=call,
        decision=_decide(policy.mode, violations),
        violations=tuple(violations),
        mode=policy.mode,
    )


def _check_paths(policy: Policy, call: ToolCall) -> List[Violation]:
    rules = policy.paths
    if not (rules.protect or rules.read_only):
        return []
    out: List[Violation] = []
    seen = set()
    writes = None  # computed lazily: only matters for read_only hits
    for label, top, raw in path_args(call.params, rules.extra_params):
        path = normalize_path(raw, policy.root)
        for i, g in enumerate(rules.protect):
            if g.regex.match(path) and (label, "p", i) not in seen:
                seen.add((label, "p", i))
                out.append(Violation(
                    f"paths.protect[{i}]",
                    f"'{label}' must not touch {g.pattern} (protected path)",
                    param=top,
                ))
        for i, g in enumerate(rules.read_only):
            if g.regex.match(path) and (label, "r", i) not in seen:
                if writes is None:
                    writes = changes_files(call.name)
                if not writes:
                    continue
                seen.add((label, "r", i))
                out.append(Violation(
                    f"paths.read_only[{i}]",
                    f"'{label}' is inside {g.pattern}, which is read-only, and "
                    f"'{call.name}' looks like it changes files",
                    param=top,
                ))
    return out


def _decide(mode: str, violations: List[Violation]) -> str:
    if not violations:
        return ALLOW
    if mode == "enforce" and any(v.severity == BLOCK for v in violations):
        return BLOCK
    return FLAG


def _check_rate(limit: RateLimit, call: ToolCall, history: History,
                tool: Optional[str], rule: str) -> Optional[Violation]:
    recent = history.count(since=call.timestamp - limit.per, tool=tool)
    if recent + 1 > limit.max:
        scope = f"'{tool}'" if tool else "all tools"
        return Violation(
            rule=rule,
            message=f"rate limit exceeded for {scope}: max {limit.max} per {_fmt_secs(limit.per)}",
        )
    return None


_MISSING = object()


def _check_param(name: str, rule: ParamRule, params: Mapping[str, Any],
                 where: str, root: Optional[str] = None) -> List[Violation]:
    value = params.get(name, _MISSING) if isinstance(params, Mapping) else _MISSING
    if value is _MISSING:
        if rule.required:
            return [Violation(f"{where}.required", f"'{name}' is required", param=name)]
        return []

    out: List[Violation] = []

    def fail(key: str, msg: str) -> None:
        out.append(Violation(f"{where}.{key}", f"'{name}' {msg}", param=name))

    if rule.type is not None and not _is_type(value, rule.type):
        fail("type", f"must be of type {rule.type} (got {_type_name(value)})")
    if rule.has_equals and value != rule.equals:
        fail("equals", "must equal the configured value")
    if rule.one_of is not None and value not in rule.one_of:
        fail("one_of", f"must be one of {list(rule.one_of)}")
    if rule.regex is not None:
        if not isinstance(value, str):
            fail("regex", "must be a string to match a pattern")
        elif not rule.regex.search(value):
            fail("regex", f"must match /{rule.regex.pattern}/")
    if rule.glob or rule.not_glob:
        if not isinstance(value, str):
            key = "glob" if rule.glob else "not_glob"
            fail(key, "must be a path string")
        else:
            path = normalize_path(value, root)
            if rule.glob and not any(g.regex.match(path) for g in rule.glob):
                fail("glob", f"must be inside {', '.join(g.pattern for g in rule.glob)}")
            for i, g in enumerate(rule.not_glob):
                if g.regex.match(path):
                    fail(f"not_glob[{i}]", f"must not be inside {g.pattern}")
    if rule.min is not None or rule.max is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail("min" if rule.min is not None else "max", "must be a number")
        else:
            if rule.min is not None and value < rule.min:
                fail("min", f"must be >= {rule.min}")
            if rule.max is not None and value > rule.max:
                fail("max", f"must be <= {rule.max}")
    if rule.max_length is not None:
        if not isinstance(value, (str, list, tuple, dict)):
            fail("max_length", "must be a string, list or mapping")
        elif len(value) > rule.max_length:
            fail("max_length", f"must have length <= {rule.max_length}")
    return out


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, (list, tuple))
    if name == "object":
        return isinstance(value, Mapping)
    if name == "null":
        return value is None
    return False


def _type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__


def _fmt_secs(seconds: float) -> str:
    for unit, size in (("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{seconds:g}s"
