"""Attach Surject to agent frameworks without code changes.

``install()`` patches each supported framework's tool-call entry point. A
framework that is already imported is patched immediately; otherwise the
patch is applied the moment its module is imported (via a meta-path hook),
so ``surject watch`` can install this before the agent's code runs.

Every patch funnels into ``_check_sync`` / ``_check_async``, which:
- skip the check if we're already inside a checked call, so a LangChain tool
  that wraps an MCP tool is reported once, not twice;
- never let a Surject failure break the agent in monitor mode (the monitor
  already fails open; argument extraction here does too).
"""

from __future__ import annotations

import contextvars
import functools
import importlib.abc
import importlib.machinery
import json
import sys
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

from biject_surject.monitor import Monitor, default_monitor

_inside: contextvars.ContextVar[bool] = contextvars.ContextVar("surject_inside", default=False)
_monitor_override: Optional[Monitor] = None
_lock = threading.Lock()
_patched: Dict[str, bool] = {}


def _monitor() -> Monitor:
    return _monitor_override or default_monitor()


# --------------------------------------------------------------------------
# The single checkpoint every adapter goes through


def _check_sync(name: Any, get_params: Callable[[], Mapping[str, Any]],
                call: Callable[[], Any]) -> Any:
    if _inside.get():
        return call()
    _monitor().enforce(str(name), _safe_params(get_params))
    token = _inside.set(True)
    try:
        return call()
    finally:
        _inside.reset(token)


async def _check_async(name: Any, get_params: Callable[[], Mapping[str, Any]],
                       call: Callable[[], Any]) -> Any:
    if _inside.get():
        return await call()
    _monitor().enforce(str(name), _safe_params(get_params))
    token = _inside.set(True)
    try:
        return await call()
    finally:
        _inside.reset(token)


def _safe_params(get_params: Callable[[], Mapping[str, Any]]) -> Dict[str, Any]:
    try:
        params = get_params()
        return dict(params) if isinstance(params, Mapping) else {"input": params}
    except Exception:
        return {}


def _input_to_params(tool_input: Any) -> Mapping[str, Any]:
    """Frameworks pass tool input as a dict, a JSON string, or a bare string."""
    if isinstance(tool_input, Mapping):
        # LangChain ToolCall dicts carry the real arguments under "args".
        if tool_input.get("type") == "tool_call" and isinstance(tool_input.get("args"), Mapping):
            return tool_input["args"]
        return tool_input
    if isinstance(tool_input, str):
        try:
            parsed = json.loads(tool_input)
            if isinstance(parsed, Mapping):
                return parsed
        except ValueError:
            pass
    return {"input": tool_input}


def _mark(fn: Callable[..., Any]) -> Callable[..., Any]:
    fn.__surject_guarded__ = True  # type: ignore[attr-defined]
    return fn


def _guarded(obj: Any, attr: str) -> bool:
    return bool(getattr(getattr(obj, attr, None), "__surject_guarded__", False))


# --------------------------------------------------------------------------
# Framework patches. Each takes the freshly imported module.


def _patch_langchain(mod: Any) -> None:
    tool = mod.BaseTool
    if not _guarded(tool, "run"):
        orig_run = tool.run

        @functools.wraps(orig_run)
        def run(self, tool_input, *args, **kwargs):
            return _check_sync(self.name, lambda: _input_to_params(tool_input),
                               lambda: orig_run(self, tool_input, *args, **kwargs))
        tool.run = _mark(run)

    if not _guarded(tool, "arun"):
        orig_arun = tool.arun

        @functools.wraps(orig_arun)
        async def arun(self, tool_input, *args, **kwargs):
            return await _check_async(self.name, lambda: _input_to_params(tool_input),
                                      lambda: orig_arun(self, tool_input, *args, **kwargs))
        tool.arun = _mark(arun)


def _patch_mcp(mod: Any) -> None:
    session = mod.ClientSession
    if _guarded(session, "call_tool"):
        return
    orig = session.call_tool

    @functools.wraps(orig)
    async def call_tool(self, name, arguments=None, *args, **kwargs):
        return await _check_async(name, lambda: arguments or {},
                                  lambda: orig(self, name, arguments, *args, **kwargs))
    session.call_tool = _mark(call_tool)


def _patch_crewai_base(mod: Any) -> None:
    tool = mod.BaseTool
    if _guarded(tool, "run"):
        return
    orig = tool.run

    @functools.wraps(orig)
    def run(self, *args, **kwargs):
        params = lambda: {**{f"arg{i}": a for i, a in enumerate(args)}, **kwargs}
        return _check_sync(self.name, params, lambda: orig(self, *args, **kwargs))
    tool.run = _mark(run)


def _patch_crewai_structured(mod: Any) -> None:
    tool = mod.CrewStructuredTool
    if not _guarded(tool, "invoke"):
        orig = tool.invoke

        @functools.wraps(orig)
        def invoke(self, input, *args, **kwargs):
            return _check_sync(self.name, lambda: _input_to_params(input),
                               lambda: orig(self, input, *args, **kwargs))
        tool.invoke = _mark(invoke)

    if hasattr(tool, "ainvoke") and not _guarded(tool, "ainvoke"):
        orig_a = tool.ainvoke

        @functools.wraps(orig_a)
        async def ainvoke(self, input, *args, **kwargs):
            return await _check_async(self.name, lambda: _input_to_params(input),
                                      lambda: orig_a(self, input, *args, **kwargs))
        tool.ainvoke = _mark(ainvoke)


def _patch_autogen(mod: Any) -> None:
    tool = mod.BaseTool
    if _guarded(tool, "run_json"):
        return
    orig = tool.run_json

    @functools.wraps(orig)
    async def run_json(self, args, *rest, **kwargs):
        return await _check_async(self.name, lambda: args,
                                  lambda: orig(self, args, *rest, **kwargs))
    tool.run_json = _mark(run_json)


def _patch_openai_agents(mod: Any) -> None:
    """``FunctionTool.on_invoke_tool`` is a per-instance callable, so wrap it
    as each tool is created. A blocked call returns the reason to the model
    as the tool's output instead of raising, which the SDK would otherwise
    treat as a fatal error for the whole run."""
    cls = mod.FunctionTool
    if _guarded(cls, "__post_init__"):
        return
    orig_post_init = cls.__post_init__

    @functools.wraps(orig_post_init)
    def __post_init__(self):
        orig_post_init(self)
        invoke = self.on_invoke_tool
        if getattr(invoke, "__surject_guarded__", False):
            return
        name = getattr(self, "name", "tool")

        async def on_invoke_tool(ctx, input_json, *args, **kwargs):
            from biject_surject.monitor import PolicyViolation
            try:
                return await _check_async(name, lambda: _input_to_params(input_json),
                                          lambda: invoke(ctx, input_json, *args, **kwargs))
            except PolicyViolation as exc:
                return str(exc)
        self.on_invoke_tool = _mark(on_invoke_tool)
    cls.__post_init__ = _mark(__post_init__)


# module that defines the class -> patch function
PATCHES: Dict[str, Callable[[Any], None]] = {
    "langchain_core.tools.base": _patch_langchain,
    "mcp.client.session": _patch_mcp,
    "crewai.tools.base_tool": _patch_crewai_base,
    "crewai.tools.structured_tool": _patch_crewai_structured,
    "autogen_core.tools._base": _patch_autogen,
    "agents.tool": _patch_openai_agents,
}


# --------------------------------------------------------------------------
# Installation


def install(monitor: Optional[Monitor] = None) -> List[str]:
    """Patch every supported framework now or when it's imported.
    Returns the modules patched immediately."""
    global _monitor_override
    if monitor is not None:
        _monitor_override = monitor
    done = []
    with _lock:
        for name in PATCHES:
            if name in sys.modules:
                if _apply(name, sys.modules[name]):
                    done.append(name)
        if not any(isinstance(f, _PatchFinder) for f in sys.meta_path):
            sys.meta_path.insert(0, _PatchFinder())
    return done


def _apply(name: str, module: Any) -> bool:
    if _patched.get(name):
        return False
    try:
        PATCHES[name](module)
        _patched[name] = True
        return True
    except Exception as exc:  # framework changed shape: warn, never crash
        try:
            sys.stderr.write(f"[surject] could not attach to {name}: "
                             f"{type(exc).__name__}; calls through it are not checked\n")
        except Exception:
            pass
        return False


class _PatchFinder(importlib.abc.MetaPathFinder):
    """Wraps the real loader for target modules so we patch right after the
    module body runs."""

    def find_spec(self, fullname, path, target=None):
        if fullname not in PATCHES or _patched.get(fullname):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader, fullname)
                return spec
        return None


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, inner: Any, fullname: str):
        self.inner = inner
        self.fullname = fullname

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        self.inner.exec_module(module)
        _apply(self.fullname, module)

    def __getattr__(self, item):  # get_data, get_source, is_package, ...
        return getattr(self.inner, item)
