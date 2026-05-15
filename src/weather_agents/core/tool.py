"""Tool registration and execution framework with retry support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from weather_agents.core.logger import get_logger

_log = get_logger("tool")


@dataclass
class ToolParameter:
    name: str
    type: str  # "string", "number", "boolean", "array", "object"
    description: str
    required: bool = True
    default: Any = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    handler: Callable[..., Coroutine[Any, Any, str]] | None = None
    max_retries: int = 2
    retry_delay: float = 0.5
    dangerous: bool = False  # 坑3: high-risk tools need audit + approval

    def to_function_schema(self) -> dict:
        """Convert to OpenAI function calling schema."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for p in self.parameters:
            properties[p.name] = {
                "type": p.type,
                "description": p.description,
            }
            if p.default is not None:
                properties[p.name]["default"] = p.default
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    async def execute(self, **kwargs) -> str:
        if self.handler is None:
            return f"Tool '{self.name}' has no handler implemented."

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                return await self.handler(**kwargs)
            except TypeError as e:
                # Bad arguments from the LLM — retry won't help.
                _log.warning(
                    "tool_bad_args",
                    extra={"tool": self.name, "error": str(e), "kwargs": list(kwargs)},
                )
                return f"Error: tool '{self.name}' called with invalid arguments: {e}"
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    _log.warning(
                        "tool_retry",
                        extra={
                            "tool": self.name,
                            "attempt": attempt + 1,
                            "error": last_error,
                        },
                    )
                    await asyncio.sleep(self.retry_delay * (2**attempt))

        _log.error(
            "tool_failed",
            extra={"tool": self.name, "retries": self.max_retries, "error": last_error},
        )
        return f"Error executing tool '{self.name}' after {self.max_retries} retries: {last_error}"


class ToolRegistry:
    """Central registry for all tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if it was registered."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_tools(self, names: list[str] | None = None) -> list[Tool]:
        if names is None:
            return list(self._tools.values())
        return [self._tools[n] for n in names if n in self._tools]

    def get_schemas(self, names: list[str] | None = None) -> list[dict]:
        return [t.to_function_schema() for t in self.get_tools(names)]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def merge(self, other: ToolRegistry) -> None:
        """Merge another registry into this one."""
        self._tools.update(other._tools)


# Global tool registry
global_registry = ToolRegistry()
