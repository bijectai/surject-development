"""The library API every adapter builds on: ``Monitor``, ``guard``, ``check``.

    import biject_surject as surject

    @surject.guard
    def write_file(path, content): ...

``guard`` checks each call against the policy before the tool runs. In
monitor mode a violation is reported and the call proceeds; in enforce mode
a blocking violation raises ``PolicyViolation`` instead of running the tool.

Fail-open rule: in monitor mode a bug inside Surject never stops the agent.
In enforce mode the user asked for blocking, so an internal error blocks.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, TypeVar, Union, overload

from biject_surject.checker import (
    ALLOW, BLOCK, History, ToolCall, Verdict, Violation, check as _check,
)
from biject_surject.policy import DEFAULT_POLICY_FILE, Policy, PolicyError, load_policy
from biject_surject.redact import Redactor
from biject_surject.reporter import Reporter

F = TypeVar("F", bound=Callable[..., Any])


class PolicyViolation(PermissionError):
    """Raised in enforce mode when a tool call is blocked. The message is
    redacted and is safe to show to the agent or the user."""

    def __init__(self, verdict: Verdict, message: str):
        super().__init__(message)
        self.verdict = verdict


class Monitor:
    def __init__(self, policy: Optional[Policy] = None,
                 reporter: Optional[Reporter] = None):
        self.policy = policy if policy is not None else Policy()
        self.redactor = Redactor.for_policy(self.policy)
        self.reporter = reporter or Reporter(
            root=self.policy.root, redactor=self.redactor,
            policy_source=self.policy.source,
        )
        self.history = History()
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: Union[str, Path] = DEFAULT_POLICY_FILE) -> "Monitor":
        return cls(load_policy(path))

    # -- checking ------------------------------------------------------------

    def check(self, name: str, params: Optional[Mapping[str, Any]] = None) -> Verdict:
        """Check one tool call, record it, and surface any violation.
        Never raises in monitor mode."""
        call = ToolCall(name=name, params=dict(params or {}), timestamp=time.time())
        try:
            with self._lock:
                verdict = _check(self.policy, call, self.history)
                self.history.record(call)
            self.reporter.report(verdict)
            return verdict
        except Exception as exc:  # internal failure
            return self._internal_error(call, exc)

    def enforce(self, name: str, params: Optional[Mapping[str, Any]] = None) -> Verdict:
        """Like ``check`` but raises ``PolicyViolation`` if the call is blocked."""
        verdict = self.check(name, params)
        if verdict.decision == BLOCK:
            raise PolicyViolation(verdict, self._block_message(verdict))
        return verdict

    # -- decorating ----------------------------------------------------------

    @overload
    def guard(self, fn: F) -> F: ...
    @overload
    def guard(self, fn: None = None, *, name: Optional[str] = None) -> Callable[[F], F]: ...

    def guard(self, fn=None, *, name=None):
        """Decorate a tool function so every call is checked first.
        Works on sync and async functions and on methods."""
        def decorate(f: F) -> F:
            return _wrap(lambda: self, f, name)
        return decorate(fn) if fn is not None else decorate

    # -- internals -----------------------------------------------------------

    def _block_message(self, verdict: Verdict) -> str:
        r = self.redactor
        reasons = "; ".join(r.text(v.message) for v in verdict.violations)
        return f"surject blocked tool '{r.text(verdict.call.name)}': {reasons}"

    def _internal_error(self, call: ToolCall, exc: Exception) -> Verdict:
        detail = self.redactor.text(f"{type(exc).__name__}: {exc}")
        try:
            sys.stderr.write(f"[surject] internal error while checking "
                             f"'{self.redactor.text(call.name)}': {detail}\n")
        except Exception:
            pass
        if self.policy.mode == "enforce":
            return Verdict(call=call, decision=BLOCK, mode=self.policy.mode,
                           violations=(Violation("surject.internal_error",
                                                 "Surject could not check this call"),))
        return Verdict(call=call, decision=ALLOW, mode=self.policy.mode)


def _wrap(get_monitor: Callable[[], "Monitor"], f: F, name: Optional[str]) -> F:
    tool_name = name or getattr(f, "__name__", "tool")
    bind = _param_binder(f)

    if inspect.iscoroutinefunction(f):
        @functools.wraps(f)
        async def async_wrapper(*args, **kwargs):
            get_monitor().enforce(tool_name, bind(args, kwargs))
            return await f(*args, **kwargs)
        async_wrapper.__surject_guarded__ = True  # type: ignore[attr-defined]
        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        get_monitor().enforce(tool_name, bind(args, kwargs))
        return f(*args, **kwargs)
    wrapper.__surject_guarded__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def _param_binder(f: Callable[..., Any]) -> Callable[[tuple, dict], Dict[str, Any]]:
    """Map a call's positional/keyword args to parameter names."""
    try:
        sig = inspect.signature(f)
    except (TypeError, ValueError):
        sig = None

    def bind(args: tuple, kwargs: dict) -> Dict[str, Any]:
        if sig is None:
            return {**{f"arg{i}": a for i, a in enumerate(args)}, **kwargs}
        try:
            bound = sig.bind_partial(*args, **kwargs)
        except TypeError:
            return {**{f"arg{i}": a for i, a in enumerate(args)}, **kwargs}
        out: Dict[str, Any] = {}
        for pname, value in bound.arguments.items():
            if pname in ("self", "cls"):
                continue
            kind = sig.parameters[pname].kind
            if kind is inspect.Parameter.VAR_KEYWORD:
                out.update(value)
            elif kind is inspect.Parameter.VAR_POSITIONAL:
                out[pname] = list(value)
            else:
                out[pname] = value
        return out

    return bind


# --------------------------------------------------------------------------
# Module-level convenience: a default monitor loaded lazily from the nearest
# surject.yaml (current directory, then parents).

POLICY_ENV = "SURJECT_POLICY"

_default: Optional[Monitor] = None
_default_lock = threading.Lock()


def find_policy(start: Union[str, Path, None] = None) -> Optional[Path]:
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        candidate = d / DEFAULT_POLICY_FILE
        if candidate.is_file():
            return candidate
    return None


def default_monitor() -> Monitor:
    global _default
    with _default_lock:
        if _default is None:
            env_path = os.environ.get(POLICY_ENV)  # set by `surject watch`
            path = Path(env_path) if env_path else find_policy()
            if path is None:
                sys.stderr.write("[surject] no surject.yaml found; nothing is being "
                                 "checked (run `surject init` to create one)\n")
                _default = Monitor(Policy(unknown_tools="allow"))
            else:
                try:
                    _default = Monitor.from_file(path)
                except PolicyError as exc:
                    sys.stderr.write(f"[surject] {exc}; nothing is being checked\n")
                    _default = Monitor(Policy(unknown_tools="allow"))
        return _default


def set_default_monitor(monitor: Optional[Monitor]) -> None:
    global _default
    with _default_lock:
        _default = monitor


def guard(fn=None, *, name: Optional[str] = None):
    """``@surject.guard`` using the default monitor (resolved at call time)."""
    def decorate(f):
        return _wrap(default_monitor, f, name)
    return decorate(fn) if fn is not None else decorate


def check(name: str, params: Optional[Mapping[str, Any]] = None) -> Verdict:
    """Check a call with the default monitor; never raises in monitor mode."""
    return default_monitor().check(name, params)
