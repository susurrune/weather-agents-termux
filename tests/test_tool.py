"""Tests for tool system."""

from __future__ import annotations

import pytest

from weather_agents.core.tool import Tool, ToolParameter, ToolRegistry


class TestTool:
    def test_basic_tool(self):
        tool = Tool(name="echo", description="Echo input", parameters=[
            ToolParameter(name="msg", type="string", description="Message to echo"),
        ])
        assert tool.name == "echo"
        assert len(tool.parameters) == 1

    def test_function_schema(self):
        tool = Tool(name="read_file", description="Read a file", parameters=[
            ToolParameter(name="path", type="string", description="File path"),
            ToolParameter(name="max_lines", type="number", description="Max lines",
                          required=False, default=100),
        ])
        schema = tool.to_function_schema()
        assert schema["function"]["name"] == "read_file"
        assert "path" in schema["function"]["parameters"]["properties"]
        assert "max_lines" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["required"] == ["path"]

    def test_execute_without_handler_returns_error(self):
        tool = Tool(name="stub", description="No handler")
        import asyncio
        result = asyncio.run(tool.execute())
        assert "has no handler" in result

    def test_execute_calls_handler(self):
        async def my_handler(**kwargs):
            return f"hello {kwargs['name']}"

        tool = Tool(name="greet", description="Greet", handler=my_handler)
        import asyncio
        result = asyncio.run(tool.execute(name="world"))
        assert result == "hello world"

    def test_retry_on_failure(self):
        call_count = 0

        async def flaky_handler(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient error")
            return "success"

        tool = Tool(name="flaky", description="Flaky tool", handler=flaky_handler,
                     max_retries=3, retry_delay=0.01)
        import asyncio
        result = asyncio.run(tool.execute())
        assert result == "success"
        assert call_count == 3

    def test_retry_exhausted(self):
        async def always_fails(**kwargs):
            raise ValueError("always fails")

        tool = Tool(name="bad", description="Bad tool", handler=always_fails,
                     max_retries=2, retry_delay=0.01)
        import asyncio
        result = asyncio.run(tool.execute())
        assert "Error" in result
        assert "retries" in result


class TestToolRegistry:
    def test_register_and_get(self):
        r = ToolRegistry()
        t = Tool(name="test_tool", description="Test")
        r.register(t)
        assert r.get("test_tool") is t
        assert r.get("nonexistent") is None

    def test_get_tools_by_names(self):
        r = ToolRegistry()
        r.register(Tool(name="a", description="A"))
        r.register(Tool(name="b", description="B"))
        r.register(Tool(name="c", description="C"))

        tools = r.get_tools(["a", "c"])
        assert len(tools) == 2
        assert tools[0].name == "a"
        assert tools[1].name == "c"

    def test_get_all_tools(self):
        r = ToolRegistry()
        r.register(Tool(name="x", description="X"))
        assert len(r.get_tools()) == 1

    def test_schemas(self):
        r = ToolRegistry()
        r.register(Tool(name="my_tool", description="My tool", parameters=[
            ToolParameter(name="p", type="string", description="A param"),
        ]))
        schemas = r.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "my_tool"

    def test_merge(self):
        r1 = ToolRegistry()
        r1.register(Tool(name="t1", description="T1"))
        r2 = ToolRegistry()
        r2.register(Tool(name="t2", description="T2"))
        r1.merge(r2)
        assert r1.get("t1") is not None
        assert r1.get("t2") is not None

    def test_list_names(self):
        r = ToolRegistry()
        r.register(Tool(name="alpha", description="Alpha"))
        r.register(Tool(name="beta", description="Beta"))
        names = r.list_names()
        assert "alpha" in names
        assert "beta" in names

    def test_override_on_reregister(self):
        r = ToolRegistry()
        t1 = Tool(name="t", description="v1")
        t2 = Tool(name="t", description="v2")
        r.register(t1)
        r.register(t2)
        assert r.get("t").description == "v2"
