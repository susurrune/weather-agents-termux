"""Tests for plugin loader and Plugin class."""

from __future__ import annotations

from unittest.mock import Mock

from weather_agents.core.tool import Tool, ToolRegistry
from weather_agents.plugins.loader import Plugin, PluginLoader


class TestPlugin:
    def test_create_plugin(self):
        p = Plugin("my_plugin")
        assert p.name == "my_plugin"
        assert p.tools == []
        assert p.hooks == {}

    def test_register_tool(self):
        p = Plugin("test")
        tool = Tool(name="test_tool", description="A test tool", parameters=[])
        p.register_tool(tool)
        assert len(p.tools) == 1
        assert p.tools[0].name == "test_tool"

    def test_register_hook(self):
        p = Plugin("test")
        handler = Mock()
        p.on("startup", handler)
        assert "startup" in p.hooks
        assert p.hooks["startup"] is handler

    def test_multiple_tools(self):
        p = Plugin("test")
        for i in range(3):
            p.register_tool(Tool(name=f"tool_{i}", description=f"Tool {i}", parameters=[]))
        assert len(p.tools) == 3


class TestPluginLoader:
    def test_empty_loader(self):
        registry = ToolRegistry()
        loader = PluginLoader(registry)
        assert loader.plugins == {}

    def test_load_from_nonexistent_directory(self):
        registry = ToolRegistry()
        loader = PluginLoader(registry)
        result = loader.load_from_directory("/nonexistent/path/12345")
        assert result == []

    def test_load_from_empty_directory(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)
        result = loader.load_from_directory(tmp_path)
        assert result == []

    def test_load_plugin_file(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        plugin_file = tmp_path / "my_plugin.py"
        plugin_file.write_text("""
from weather_agents.plugins.loader import Plugin
from weather_agents.core.tool import Tool

def create_plugin():
    p = Plugin("loaded_plugin")
    p.register_tool(Tool(name="custom_tool", description="A custom tool", parameters=[]))
    return p
""")

        results = loader.load_from_directory(tmp_path)
        assert len(results) == 1
        assert results[0].name == "loaded_plugin"
        assert "loaded_plugin" in loader.plugins

    def test_load_plugin_registers_tools(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        plugin_file = tmp_path / "tool_plugin.py"
        plugin_file.write_text("""
from weather_agents.plugins.loader import Plugin
from weather_agents.core.tool import Tool

def create_plugin():
    p = Plugin("tooled")
    p.register_tool(Tool(name="extra_tool", description="Extra", parameters=[]))
    return p
""")

        loader.load_from_directory(tmp_path)
        assert registry.get("extra_tool") is not None

    def test_load_skips_underscore_files(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "_private.py").write_text("""
def create_plugin():
    from weather_agents.plugins.loader import Plugin
    return Plugin("private")
""")

        results = loader.load_from_directory(tmp_path)
        assert len(results) == 0

    def test_load_handles_invalid_plugin(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        invalid = tmp_path / "bad.py"
        invalid.write_text("syntax error !!! this is not valid Python")

        results = loader.load_from_directory(tmp_path)
        assert results == []

    def test_load_handles_missing_create_plugin(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        plugin_file = tmp_path / "empty.py"
        plugin_file.write_text("x = 1")

        results = loader.load_from_directory(tmp_path)
        assert results == []

    def test_load_from_multiple_directories(self, tmp_path):
        registry = ToolRegistry()
        loader = PluginLoader(registry)

        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()

        (dir_a / "plugin_a.py").write_text("""
from weather_agents.plugins.loader import Plugin
def create_plugin():
    return Plugin("plugin_a")
""")
        (dir_b / "plugin_b.py").write_text("""
from weather_agents.plugins.loader import Plugin
def create_plugin():
    return Plugin("plugin_b")
""")

        loader.load_from_directories([str(dir_a), str(dir_b)])
        assert len(loader.plugins) == 2
        assert "plugin_a" in loader.plugins
        assert "plugin_b" in loader.plugins
