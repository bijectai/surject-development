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
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("files")
    written = []

    @server.tool()
    def write_file(path: str) -> str:
        written.append(path)
        return "ok"

    async def main():
        async with Client(server) as client:
            await client.call_tool("write_file", {"path": "out/a.csv"})
            with pytest.raises(surject.PolicyViolation):
                await client.call_tool("write_file", {"path": "data/raw/a.csv"})

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
