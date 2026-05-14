"""MCP (Model Context Protocol) client for connecting to MCP servers.

Integrates MCP tools into the Weather Agents tool registry,
supporting both stdio-based and SSE-based MCP servers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

from weather_agents.core.logger import get_logger
from weather_agents.core.tool import Tool, ToolParameter, ToolRegistry

_log = get_logger("mcp")


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
    """Client for connecting to a single MCP server and listing tools.

    Supports concurrent requests via JSON-RPC id correlation: each call_tool
    awaits its own Future, fulfilled by a background reader task.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._server_tools: list[dict] = []
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def initialize(self) -> list[dict]:
        """Connect to the MCP server and list available tools.

        Returns a list of MCP tool definitions (name, description, inputSchema).
        """
        try:
            if self.config.command:
                return await self._init_stdio()
            elif self.config.url:
                return await self._init_sse()
        except (FileNotFoundError, OSError) as e:
            _log.warning(
                "mcp_server_unavailable",
                extra={"server": self.config.name, "error": str(e)},
            )
        return []

    async def _init_stdio(self) -> list[dict]:
        """Connect via stdio transport."""
        # Inherit caller's environment so the child can find PATH, HOME, npx, etc.
        env = {**os.environ, **(self.config.env or {})}
        cmd = self.config.command
        if not cmd:
            return []

        self._process = await asyncio.create_subprocess_exec(
            cmd,
            *self.config.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Start the background reader so subsequent _request calls can correlate.
        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            init_response = await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "weather-agents", "version": "1.0.0"},
                },
            )
            if not init_response or "error" in init_response:
                return []

            # Notifications have no id and no reply.
            await self._send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )

            tools_response = await self._request("tools/list", {})
            if tools_response and "result" in tools_response:
                self._server_tools = tools_response["result"].get("tools", [])

            return self._server_tools
        except Exception as e:
            _log.warning(
                "mcp_init_failed",
                extra={"server": self.config.name, "error": str(e)},
            )
            await self.close()
            return []

    async def _init_sse(self) -> list[dict]:
        """Connect via SSE transport."""
        url = (self.config.url or "").rstrip("/")

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
        response = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=60.0,
        )
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
        url = (self.config.url or "").rstrip("/")
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
        async with self._send_lock:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

    async def _request(
        self,
        method: str,
        params: dict,
        timeout: float = 10.0,
    ) -> dict[str, Any] | None:
        """Send a JSON-RPC request and await the matching response by id."""
        if not self._process or not self._process.stdin:
            return None
        req_id = self._new_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        await self._send_json({"jsonrpc": "2.0", "method": method, "params": params, "id": req_id})

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            _log.warning(
                "mcp_request_timeout",
                extra={"server": self.config.name, "method": method, "id": req_id},
            )
            return None
        finally:
            self._pending.pop(req_id, None)

    async def _read_loop(self) -> None:
        """Background task: read JSON-RPC responses and dispatch by id."""
        if not self._process or not self._process.stdout:
            return
        stdout = self._process.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except (json.JSONDecodeError, ValueError):
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    # Notification — ignore.
                    continue
                fut = self._pending.get(msg_id)
                if fut and not fut.done():
                    fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning(
                "mcp_read_loop_error",
                extra={"server": self.config.name, "error": str(e)},
            )
        finally:
            # Cancel any still-pending futures.
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._reader_task = None
        proc = self._process
        if proc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
            except ProcessLookupError:
                pass
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
                parameters.append(
                    ToolParameter(
                        name=param_name,
                        type=param_type,
                        description=param_schema.get("description", ""),
                        required=param_name in required,
                    )
                )

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
