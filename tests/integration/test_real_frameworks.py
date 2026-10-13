"""Surject against the real agent frameworks.

Each test skips if its framework isn't installed. Run them with:

    pip install -e ".[dev]" langchain-core mcp crewai autogen-core openai-agents
    pytest tests/integration
"""

import asyncio
import io

import pytest

import biject_surject as surject
from biject_surject import autopatch
from biject_surject.policy import parse_policy
from biject_surject.reporter import Reporter

POLICY = """
mode: enforce
tools:
  write_file:
    params:
      path: { not_glob: ["./data/raw/**"] }
"""


@pytest.fixture
def monitor(tmp_path):
    stream = io.StringIO()
    m = surject.Monitor(parse_policy(POLICY, root=str(tmp_path)),
                        Reporter(root=tmp_path, stream=stream))
    m.stream = stream
    old = autopatch._monitor_override
    autopatch.install(m)
    yield m
    autopatch._monitor_override = old


def blocked(m):
    return m.stream.getvalue().count("BLOCKED write_file")


def test_langchain(monitor):
    pytest.importorskip("langchain_core")
    from langchain_core.tools import tool

    @tool
    def write_file(path: str) -> str:
        """Write a file."""
        return f"wrote {path}"

    assert write_file.invoke({"path": "out/a.csv"}) == "wrote out/a.csv"
    with pytest.raises(surject.PolicyViolation):
        write_file.invoke({"path": "data/raw/a.csv"})
    with pytest.raises(surject.PolicyViolation):
        asyncio.run(write_file.ainvoke({"path": "data/raw/b.csv"}))
    assert blocked(monitor) == 2


def test_langchain_tool_call_message(monitor):
    pytest.importorskip("langchain_core")
    from langchain_core.tools import tool

    @tool
    def write_file(path: str) -> str:
        """Write a file."""
        return f"wrote {path}"

    call = {"type": "tool_call", "name": "write_file", "id": "c1",
            "args": {"path": "data/raw/c.csv"}}
    with pytest.raises(surject.PolicyViolation):
        write_file.invoke(call)
    assert "data/raw/c.csv" in monitor.stream.getvalue()


def test_mcp_client(monitor):
    pytest.importorskip("mcp")
    written = []

    try:  # mcp >= 2
        from mcp import Client
        from mcp.server.mcpserver import MCPServer

        server = MCPServer("files")

        def connect():
            return Client(server)

        async def call(client, name, args):
            return await client.call_tool(name, args)
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        from mcp.shared.memory import create_connected_server_and_client_session

        server = FastMCP("files")

        def connect():
            return create_connected_server_and_client_session(server._mcp_server)

        async def call(session, name, args):
            return await session.call_tool(name, args)

    @server.tool()
    def write_file(path: str) -> str:
        written.append(path)
        return "ok"

    async def main():
        async with connect() as client:
            await call(client, "write_file", {"path": "out/a.csv"})
            with pytest.raises(surject.PolicyViolation):
                await call(client, "write_file", {"path": "data/raw/a.csv"})

    asyncio.run(main())
    assert written == ["out/a.csv"]
    assert blocked(monitor) == 1


def test_crewai(monitor):
    pytest.importorskip("crewai")
    from crewai.tools import tool

    @tool("write_file")
    def write_file(path: str) -> str:
        """Write a file."""
        return f"wrote {path}"

    assert write_file.run(path="out/a.csv") == "wrote out/a.csv"
    with pytest.raises(surject.PolicyViolation):
        write_file.run(path="data/raw/a.csv")
    structured = write_file.to_structured_tool()
    with pytest.raises(surject.PolicyViolation):
        structured.invoke({"path": "data/raw/b.csv"})
    assert blocked(monitor) == 2


def test_autogen(monitor):
    pytest.importorskip("autogen_core")
    from autogen_core import CancellationToken
    from autogen_core.tools import FunctionTool

    async def write_file(path: str) -> str:
        return f"wrote {path}"

    t = FunctionTool(write_file, description="Write a file.", name="write_file")

    async def main():
        assert await t.run_json({"path": "out/a.csv"}, CancellationToken()) == "wrote out/a.csv"
        with pytest.raises(surject.PolicyViolation):
            await t.run_json({"path": "data/raw/a.csv"}, CancellationToken())

    asyncio.run(main())
    assert blocked(monitor) == 1


def test_openai_agents(monitor):
    pytest.importorskip("agents")
    from agents import function_tool
    from agents.tool_context import ToolContext

    @function_tool
    def write_file(path: str) -> str:
        """Write a file."""
        return f"wrote {path}"

    def ctx(args):
        return ToolContext(context=None, tool_name="write_file", tool_call_id="c1",
                           tool_arguments=args)

    async def main():
        ok = '{"path": "out/a.csv"}'
        bad = '{"path": "data/raw/a.csv"}'
        assert await write_file.on_invoke_tool(ctx(ok), ok) == "wrote out/a.csv"
        out = await write_file.on_invoke_tool(ctx(bad), bad)
        assert out.startswith("surject blocked tool 'write_file'")

    asyncio.run(main())
    assert blocked(monitor) == 1


def test_mcp_stdio_proxy(tmp_path):
    """A real MCP client and server over stdio, with `surject mcp` between."""
    pytest.importorskip("mcp")
    import json
    import os
    import subprocess
    import sys
    import textwrap

    (tmp_path / "surject.yaml").write_text(POLICY, encoding="utf-8")
    (tmp_path / "server.py").write_text(textwrap.dedent("""
        try:
            from mcp.server.mcpserver import MCPServer as Server
        except ImportError:
            from mcp.server.fastmcp import FastMCP as Server
        server = Server("files")

        @server.tool()
        def write_file(path: str) -> str:
            return "wrote " + path

        server.run()
    """), encoding="utf-8")
    (tmp_path / "client.py").write_text(textwrap.dedent("""
        import asyncio, json, sys
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "biject_surject", "mcp", "--policy", "surject.yaml", "--",
            sys.executable, "server.py"])

        async def main():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    out = {}
                    for p in ("out/a.csv", "data/raw/a.csv"):
                        res = await s.call_tool("write_file", {"path": p})
                        err = getattr(res, "is_error", getattr(res, "isError", None))
                        out[p] = [err, res.content[0].text]
                    print(json.dumps(out))

        asyncio.run(main())
    """), encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "client.py"], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["out/a.csv"][0] is False and "wrote out/a.csv" in out["out/a.csv"][1]
    assert out["data/raw/a.csv"][0] is True
    assert out["data/raw/a.csv"][1].startswith("surject blocked tool 'write_file'")
