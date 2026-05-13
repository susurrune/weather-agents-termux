"""MCP (Model Context Protocol) client for connecting to MCP servers.

Integrates MCP tools into the Weather Agents tool registry,
supporting both stdio-based and SSE-based MCP servers.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

from weather_agents.core.tool import Tool, ToolParameter, ToolRegistry


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    name: str
    command: str | None = None  # stdio: executable
    args: list[str] = field(default_factory=list)  # stdio: args
    url: str | None = None  # SSE: server URL
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPClient:
    """Client for connecting to a single MCP server and listing tools."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: subprocess.Popen | None = None
        self._server_tools: list[dict] = []

    async def initialize(self) -> list[dict]:
        """Connect to the MCP server and list available tools.

        Returns a list of MCP tool definitions (name, description, inputSchema).
        """
        if self.config.command:
            return await self._init_stdio()
        elif self.config.url:
            return await self._init_sse()
        return []

    async def _init_stdio(self) -> list[dict]:
        """Connect via stdio transport."""
        env = {**self.config.env} if self.config.env else None

        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        try:
            # Send initialize request
            await self._send_json({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "weather-agents", "version": "0.1.0"},
                },
                "id": 1,
            })

            init_response = await self._read_response()
            if not init_response or "error" in init_response:
                return []

            # Send initialized notification
            await self._send_json({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            })

            # List tools
            await self._send_json({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
                "id": 2,
            })

            tools_response = await self._read_response()
            if tools_response and "result" in tools_response:
                self._server_tools = tools_response["result"].get("tools", [])

            return self._server_tools
        except Exception:
            await self.close()
            return []

    async def _init_sse(self) -> list[dict]:
        """Connect via SSE transport."""
        url = self.config.url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{url}/initialize",
                    json={
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "weather-agents", "version": "0.1.0"},
                    },
                )
                if resp.status_code != 200:
                    return []

                resp = await client.post(
                    f"{url}/tools/list",
                    json={},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._server_tools = data.get("tools", [])
        except Exception:
            return []

        return self._server_tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server and return the result."""
        if self.config.command:
            return await self._call_stdio(name, arguments)
        elif self.config.url:
            return await self._call_sse(name, arguments)
        return f"Error: No transport configured for MCP server '{self.config.name}'"

    async def _call_stdio(self, name: str, arguments: dict[str, Any]) -> str:
        await self._send_json({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": 3,
        })
        response = await self._read_response()
        if response and "result" in response:
            result = response["result"]
            parts = []
            for item in result.get("content", []):
                if item.get("type") == "text":
                    parts.append(item["text"])
                elif item.get("type") == "resource":
                    parts.append(json.dumps(item.get("resource", {}), ensure_ascii=False))
            return "\n".join(parts) if parts else "Tool returned no content."
        error = response.get("error", {}) if response else {}
        return f"MCP tool error: {error.get('message', 'unknown')}"

    async def _call_sse(self, name: str, arguments: dict[str, Any]) -> str:
        url = self.config.url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{url}/tools/call",
                    json={"name": name, "arguments": arguments},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    parts = []
                    for item in data.get("content", []):
                        if item.get("type") == "text":
                            parts.append(item["text"])
                    return "\n".join(parts) if parts else "No text content returned."
                return f"Error: HTTP {resp.status_code}"
        except Exception as e:
            return f"MCP SSE call error: {e}"

    async def _send_json(self, data: dict) -> None:
        if not self._process or not self._process.stdin:
            return
        line = json.dumps(data, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _read_response(self, timeout: float = 10.0) -> dict | None:
        if not self._process or not self._process.stdout:
            return None
        try:
            line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=timeout,
            )
            if line:
                return json.loads(line.decode())
        except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
            pass
        return None

    async def close(self) -> None:
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

    def get_tool_definitions(self) -> list[dict]:
        """Get raw MCP tool definitions from the server."""
        return self._server_tools


class MCPManager:
    """Manages multiple MCP server connections and registers their tools."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry
        self.clients: dict[str, MCPClient] = {}
        self._server_configs: list[MCPServerConfig] = []

    def configure(self, servers: list[dict]) -> None:
        """Configure MCP servers from config data."""
        self._server_configs = [
            MCPServerConfig(
                name=s["name"],
                command=s.get("command"),
                args=s.get("args", []),
                url=s.get("url"),
                env=s.get("env", {}),
                enabled=s.get("enabled", True),
            )
            for s in servers
            if s.get("enabled", True)
        ]

    async def connect_all(self) -> list[str]:
        """Connect to all configured MCP servers and register tools.

        Returns a list of (server_name: tool_count) strings.
        """
        results = []

        for cfg in self._server_configs:
            if not cfg.enabled:
                continue

            client = MCPClient(cfg)
            tools = await client.initialize()
            if not tools:
                continue

            self.clients[cfg.name] = client
            count = self._register_mcp_tools(cfg.name, tools)
            results.append(f"{cfg.name}: {count} tools")

        return results

    def _register_mcp_tools(self, server_name: str, mcp_tools: list[dict]) -> int:
        """Convert MCP tool definitions into Tool and register them."""
        count = 0
        for mt in mcp_tools:
            name = mt.get("name", "")
            description = mt.get("description", "")
            input_schema = mt.get("inputSchema", {})

            if not name:
                continue

            parameters = []
            props = input_schema.get("properties", {}) if input_schema else {}
            required = input_schema.get("required", []) if input_schema else []

            for param_name, param_schema in props.items():
                param_type = param_schema.get("type", "string")
                parameters.append(ToolParameter(
                    name=param_name,
                    type=param_type,
                    description=param_schema.get("description", ""),
                    required=param_name in required,
                ))

            mcp_tool_name = f"mcp_{server_name}_{name}"

            tool = Tool(
                name=mcp_tool_name,
                description=f"[MCP/{server_name}] {description}",
                parameters=parameters,
                handler=self._make_mcp_handler(server_name, name),
            )
            self.tool_registry.register(tool)
            count += 1

        return count

    def _make_mcp_handler(self, server_name: str, tool_name: str):
        """Create a handler function that calls the MCP tool."""
        async def handler(**kwargs) -> str:
            client = self.clients.get(server_name)
            if not client:
                return f"Error: MCP server '{server_name}' not connected."
            return await client.call_tool(tool_name, kwargs)
        return handler

    async def close_all(self) -> None:
        for client in self.clients.values():
            await client.close()
        self.clients.clear()
