"""LLM abstraction layer using LiteLLM."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator

import litellm

from weather_agents.core.cache import LLMCache
from weather_agents.core.config import AppConfig
from weather_agents.core.tool import ToolRegistry


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)


class LLMClient:
    """Unified LLM client supporting multiple providers via LiteLLM."""

    def __init__(self, config: AppConfig, tool_registry: ToolRegistry) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.cache = LLMCache(max_size=128, ttl_seconds=60)

    def _get_model(self, agent_name: str | None = None) -> str:
        if agent_name:
            agent_cfg = getattr(self.config.agents, agent_name, None)
            if agent_cfg and agent_cfg.model:
                return agent_cfg.model
        return self.config.llm.default_model

    async def complete(
        self,
        messages: list[dict],
        agent_name: str | None = None,
        tools: list[str] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        model = self._get_model(agent_name)
        tool_schemas = self.tool_registry.get_schemas(tools) if tools else None

        # Only cache simple chat requests (no tools, no stream)
        use_cache = not tools and not stream
        if use_cache:
            cached = self.cache.get(model, messages)
            if cached is not None:
                return LLMResponse(content=cached, model=model)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.config.llm.temperature,
            "max_tokens": self.config.llm.max_tokens,
            "timeout": self.config.llm.timeout,
        }
        if tool_schemas:
            kwargs["tools"] = tool_schemas

        response = await litellm.acompletion(**kwargs)

        content = ""
        tool_calls = []
        choice = response.choices[0]

        if choice.message.content:
            content = choice.message.content

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        if use_cache and content and not tool_calls:
            self.cache.set(model, messages, content)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=response.model or model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    async def stream(
        self,
        messages: list[dict],
        agent_name: str | None = None,
    ) -> AsyncIterator[str]:
        model = self._get_model(agent_name)

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_tokens,
            timeout=self.config.llm.timeout,
            stream=True,
        )

        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
