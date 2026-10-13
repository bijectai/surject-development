import asyncio
import io
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

import biject_surject as surject
from biject_surject import autopatch
from biject_surject.checker import ALLOW, FLAG
from biject_surject.policy import parse_policy
from biject_surject.reporter import Reporter

POLICY = """
mode: {mode}
tools:
  write_file:
    params:
      path: {{ not_glob: ["./data/raw/**"] }}
  rm: block
"""


@pytest.fixture
def monitor(tmp_path, request):
    mode = getattr(request, "param", "monitor")
    policy = parse_policy(POLICY.format(mode=mode), root=str(tmp_path))
    stream = io.StringIO()
    m = surject.Monitor(policy, Reporter(root=tmp_path, stream=stream))
    m.stream = stream
    old = autopatch._monitor_override
    autopatch._monitor_override = m
    yield m
    autopatch._monitor_override = old


def flagged(m):
    return [line for line in m.stream.getvalue().splitlines() if line.startswith("[surject]")]


# -- fake framework shapes (mirroring the real classes' entry points) -------


def fake_langchain():
    class BaseTool:
        def __init__(self, name):
            self.name = name

        def run(self, tool_input, verbose=None, **kwargs):
            return ("ran", tool_input)

        async def arun(self, tool_input, verbose=None, **kwargs):
            return ("ran", tool_input)

        def invoke(self, input, config=None, **kwargs):
            return self.run(input, **kwargs)

    return types.SimpleNamespace(BaseTool=BaseTool)


def fake_mcp():
    class ClientSession:
        async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kw):
            return ("ran", name, arguments)

    return types.SimpleNamespace(ClientSession=ClientSession)


# -- per-framework patches ----------------------------------------------------


def test_langchain_run_invoke_and_arun(monitor):
    mod = fake_langchain()
    autopatch._patch_langchain(mod)
    t = mod.BaseTool("write_file")
    assert t.run({"path": "out/x"}) == ("ran", {"path": "out/x"})
    assert flagged(monitor) == []
    t.invoke({"path": "data/raw/x"})  # invoke -> run, checked once
    assert len(flagged(monitor)) == 1
    asyncio.run(t.arun('{"path": "data/raw/y"}'))  # JSON string input
    assert len(flagged(monitor)) == 2


def test_langchain_tool_call_dict(monitor):
    mod = fake_langchain()
    autopatch._patch_langchain(mod)
    mod.BaseTool("write_file").run(
        {"type": "tool_call", "name": "write_file", "id": "1", "args": {"path": "data/raw/z"}})
    assert "data/raw/z" in monitor.stream.getvalue()


@pytest.mark.parametrize("monitor", ["enforce"], indirect=True)
def test_langchain_enforce_blocks_before_running(monitor):
    mod = fake_langchain()
    autopatch._patch_langchain(mod)
    with pytest.raises(surject.PolicyViolation):
        mod.BaseTool("rm").run({"path": "x"})


def test_patch_is_idempotent(monitor):
    mod = fake_langchain()
    autopatch._patch_langchain(mod)
    first = mod.BaseTool.run
    autopatch._patch_langchain(mod)
    assert mod.BaseTool.run is first


def test_mcp_call_tool(monitor):
    mod = fake_mcp()
    autopatch._patch_mcp(mod)
    s = mod.ClientSession()
    assert asyncio.run(s.call_tool("write_file", {"path": "ok"})) == ("ran", "write_file", {"path": "ok"})
    asyncio.run(s.call_tool("rm", {"path": "x"}))
    assert any("FLAGGED rm" in l for l in flagged(monitor))


def test_nested_calls_are_reported_once(monitor):
    lc, mcp = fake_langchain(), fake_mcp()
    autopatch._patch_langchain(lc)
    autopatch._patch_mcp(mcp)
    session = mcp.ClientSession()

    class McpBackedTool(lc.BaseTool):
        async def arun(self, tool_input, **kw):  # like langchain-mcp-adapters
            return await session.call_tool(self.name, tool_input)

    asyncio.run(McpBackedTool("rm").arun({"path": "x"}))
    assert len(flagged(monitor)) == 1


def test_crewai(monitor):
    class BaseTool:
        name = "rm"

        def run(self, *args, **kwargs):
            return "ran"

    class CrewStructuredTool:
        name = "write_file"

        def invoke(self, input, config=None, **kwargs):
            return "ran"

        async def ainvoke(self, input, config=None, **kwargs):
            return "ran"

    autopatch._patch_crewai_base(types.SimpleNamespace(BaseTool=BaseTool))
    autopatch._patch_crewai_structured(types.SimpleNamespace(CrewStructuredTool=CrewStructuredTool))
    assert BaseTool().run(path="x") == "ran"
    assert CrewStructuredTool().invoke('{"path": "data/raw/q"}') == "ran"
    asyncio.run(CrewStructuredTool().ainvoke({"path": "ok"}))
    out = flagged(monitor)
    assert len(out) == 2 and "rm" in out[0] and "write_file" in out[1]


def test_autogen(monitor):
    class BaseTool:
        name = "rm"

        async def run_json(self, args, cancellation_token, call_id=None):
            return "ran"

    autopatch._patch_autogen(types.SimpleNamespace(BaseTool=BaseTool))
    assert asyncio.run(BaseTool().run_json({"path": "x"}, None)) == "ran"
    assert len(flagged(monitor)) == 1


@pytest.mark.parametrize("monitor", ["enforce"], indirect=True)
def test_openai_agents_block_returns_message_to_model(monitor):
    from dataclasses import dataclass, field
    from typing import Any, Callable

    @dataclass
    class FunctionTool:
        name: str
        on_invoke_tool: Callable[..., Any] = field(default=None)

        def __post_init__(self):
            pass

    autopatch._patch_openai_agents(types.SimpleNamespace(FunctionTool=FunctionTool))

    async def impl(ctx, input_json):
        return "ran"

    ok = FunctionTool("write_file", impl)
    blocked = FunctionTool("rm", impl)
    assert asyncio.run(ok.on_invoke_tool(None, '{"path": "out/x"}')) == "ran"
    out = asyncio.run(blocked.on_invoke_tool(None, '{"path": "x"}'))
    assert out.startswith("surject blocked tool 'rm'")


def test_broken_patch_warns_and_continues(capsys, monkeypatch):
    monkeypatch.setitem(autopatch.PATCHES, "fake_broken_mod", lambda m: m.Missing)
    monkeypatch.setattr(autopatch, "_patched", {})
    assert autopatch._apply("fake_broken_mod", types.SimpleNamespace()) is False
    assert "could not attach to fake_broken_mod" in capsys.readouterr().err


def test_import_hook_patches_on_import(tmp_path, monkeypatch, monitor):
    pkg = tmp_path / "fakefw"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tools.py").write_text(textwrap.dedent("""
        class BaseTool:
            name = "rm"
            def run(self, tool_input, **kw):
                return "ran"
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(autopatch.PATCHES, "fakefw.tools", autopatch._patch_langchain)
    monkeypatch.setattr(autopatch, "_patched", {})
    for name in ("fakefw", "fakefw.tools"):
        sys.modules.pop(name, None)
    autopatch.install()
    try:
        import fakefw.tools
        assert getattr(fakefw.tools.BaseTool.run, "__surject_guarded__", False)
        assert fakefw.tools.BaseTool().run({"path": "x"}) == "ran"
        assert len(flagged(monitor)) == 1
    finally:
        for name in ("fakefw", "fakefw.tools"):
            sys.modules.pop(name, None)


# -- surject watch, end to end ------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _watch(tmp_path, *cmd, env_extra=None):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "biject_surject", "watch", "--", *cmd],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def project(tmp_path):
    _write(tmp_path / "surject.yaml", POLICY.format(mode="monitor"))
    # A fake MCP client, at the real module path, that the agent imports.
    _write(tmp_path / "fakes" / "mcp" / "__init__.py", "")
    _write(tmp_path / "fakes" / "mcp" / "client" / "__init__.py", "")
    _write(tmp_path / "fakes" / "mcp" / "client" / "session.py", """
        class ClientSession:
            async def call_tool(self, name, arguments=None):
                return "result-of-" + name
    """)
    _write(tmp_path / "agent.py", """
        import asyncio, sys
        sys.path.insert(0, "fakes")
        from mcp.client.session import ClientSession
        s = ClientSession()
        print(asyncio.run(s.call_tool("write_file", {"path": "out/ok.csv"})))
        print(asyncio.run(s.call_tool("write_file", {"path": "data/raw/exp1.csv",
                                                     "api_key": "sk-proj-AbCdEfGhIjKlMnOpQrSt"})))
    """)
    return tmp_path


def test_watch_end_to_end(project):
    r = _watch(project, sys.executable, "agent.py")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["result-of-write_file", "result-of-write_file"]
    assert "[surject] watching (monitor mode" in r.stderr
    assert "FLAGGED write_file" in r.stderr
    assert "tools.write_file.params.path.not_glob[0]" in r.stderr
    assert "sk-proj-AbCdEf" not in r.stderr
    events = (project / ".surject" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    assert json.loads(events[0])["params"]["api_key"] == "[REDACTED:name]"


def test_watch_enforce_blocks(project):
    (project / "surject.yaml").write_text(POLICY.format(mode="enforce"), encoding="utf-8")
    r = _watch(project, sys.executable, "agent.py")
    assert r.returncode != 0
    assert "BLOCKED write_file" in r.stderr
    assert "PolicyViolation" in r.stderr
    assert r.stdout.splitlines() == ["result-of-write_file"]


def test_watch_passes_exit_code_and_chains_sitecustomize(project):
    _write(project / "site" / "sitecustomize.py", "import builtins; builtins.CHAINED = True\n")
    _write(project / "exit.py", "import builtins, sys; print(getattr(builtins, 'CHAINED', False)); sys.exit(7)\n")
    r = _watch(project, sys.executable, "exit.py", env_extra={"PYTHONPATH": str(project / "site")})
    assert r.returncode == 7
    assert r.stdout.strip() == "True"


def test_watch_requires_policy_and_command(tmp_path):
    r = _watch(tmp_path, sys.executable, "-c", "pass")
    assert r.returncode == 1 and "surject init" in r.stderr
    _write(tmp_path / "surject.yaml", "mode: monitor\n")
    r = subprocess.run([sys.executable, "-m", "biject_surject", "watch"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2 and "nothing to run" in r.stderr


def test_subclass_overrides_are_patched_existing_and_future(monitor):
    class BaseTool:
        name = "rm"

        def run(self, *args, **kwargs):
            return "base"

    class Existing(BaseTool):  # defined before patching, overrides run
        def run(self, *args, **kwargs):
            return "existing"

    autopatch._patch_crewai_base(types.SimpleNamespace(BaseTool=BaseTool))

    class Later(BaseTool):  # defined after patching
        def run(self, *args, **kwargs):
            return "later:" + super().run(*args, **kwargs)

    class Inherits(BaseTool):  # no override: uses the patched base
        pass

    assert Existing().run(path="x") == "existing"
    assert Later().run(path="x") == "later:base"  # super() call checked once
    assert Inherits().run(path="x") == "base"
    assert len(flagged(monitor)) == 3


def test_subclass_hook_preserves_existing_init_subclass(monitor):
    seen = []

    class BaseTool:
        name = "rm"

        def __init_subclass__(cls, **kw):
            seen.append(cls.__name__)

        def run(self, tool_input=None, **kw):
            return "ran"

    autopatch._patch_langchain(types.SimpleNamespace(BaseTool=BaseTool))

    class Mine(BaseTool):
        def run(self, tool_input=None, **kw):
            return "mine"

    assert seen == ["Mine"]
    assert Mine().run({"path": "x"}) == "mine"
    assert len(flagged(monitor)) == 1
