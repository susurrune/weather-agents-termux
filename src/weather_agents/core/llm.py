"""LLM abstraction layer using LiteLLM with retry, fallback, cost tracking, and budget control."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import litellm

from weather_agents.core.cache import LLMCache
from weather_agents.core.config import AppConfig
from weather_agents.core.logger import get_logger, log_event
from weather_agents.core.tool import ToolRegistry

log = get_logger("llm")

# Cost per 1K tokens (input / output) — USD
_MODEL_COST_ESTIMATES: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1-nano": (0.0001, 0.0004),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-opus-4-7": (0.015, 0.075),
    "claude-haiku-4-5": (0.0008, 0.004),
    "deepseek-chat": (0.00014, 0.00028),
    "deepseek-reasoner": (0.00055, 0.0022),
    "ollama/llama3": (0.0, 0.0),
    "ollama/qwen2.5": (0.0, 0.0),
}

_FALLBACK_CHAINS: dict[str, list[str]] = {
    "gpt-4o": ["gpt-4o-mini"],
    "gpt-4o-mini": ["gpt-4o"],
    "gpt-4.1": ["gpt-4.1-mini", "gpt-4o-mini"],
    "gpt-4.1-mini": ["gpt-4o-mini"],
    "claude-sonnet-4-6": ["claude-haiku-4-5", "gpt-4o-mini"],
    "claude-opus-4-7": ["claude-sonnet-4-6", "gpt-4o"],
    "claude-haiku-4-5": ["gpt-4o-mini"],
    "deepseek-chat": ["gpt-4o-mini"],
}

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    costs = _MODEL_COST_ESTIMATES.get(model, (0.001, 0.002))
    return (prompt_tokens / 1000) * costs[0] + (completion_tokens / 1000) * costs[1]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)
    cost: float = 0.0


class LLMClient:
    """Unified LLM client with retry, fallback chains, caching, cost tracking, and budget control."""

    def __init__(
        self,
        config: AppConfig,
        tool_registry: ToolRegistry,
        cost_limit: float | None = None,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.cache = LLMCache(max_size=256, ttl_seconds=120)
        self._usage_stats: dict[str, dict] = {}
        self._total_cost: float = 0.0
        self._cost_limit = cost_limit

    def _get_model(self, agent_name: str | None = None) -> str:
        if agent_name:
            agent_cfg = getattr(self.config.agents, agent_name, None)
            if agent_cfg and agent_cfg.model:
                return agent_cfg.model
        return self.config.llm.default_model

    def _get_retries(self) -> int:
        return getattr(self.config.llm, "max_retries", 2)

    def _track_usage(
        self,
        agent_name: str | None,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        key = agent_name or "default"
        if key not in self._usage_stats:
            self._usage_stats[key] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
                "cost": 0.0,
            }
        s = self._usage_stats[key]
        s["prompt_tokens"] += prompt_tokens
        s["completion_tokens"] += completion_tokens
        s["calls"] += 1
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        s["cost"] += cost
        self._total_cost += cost

    def get_usage_stats(self) -> dict[str, dict]:
        return dict(self._usage_stats)

    def get_total_cost(self) -> float:
        return self._total_cost

    def reset_usage_stats(self) -> None:
        self._usage_stats.clear()
        self._total_cost = 0.0

    def _check_budget(self) -> None:
        if self._cost_limit is not None and self._total_cost >= self._cost_limit:
            raise RuntimeError(
                f"Cost limit exceeded: ${self._total_cost:.4f} >= ${self._cost_limit:.4f}"
            )

    async def complete(
        self,
        messages: list[dict],
        agent_name: str | None = None,
        tools: list[str] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        self._check_budget()
        model = self._get_model(agent_name)

        fallback_models = _FALLBACK_CHAINS.get(model, [])
        models_to_try = [model] + fallback_models

        last_error: Exception | None = None
        for attempt_model in models_to_try:
            try:
                return await self._complete_with_retry(
                    attempt_model, messages, agent_name, tools, stream,
                )
            except Exception as e:
                last_error = e
                log.warning(
                    "llm_fallback",
                    extra={
                        "model": attempt_model,
                        "agent": agent_name,
                        "error": str(e),
                    },
                )
                continue

        log.error(
            "llm_all_failed",
            extra={
                "models": models_to_try,
                "agent": agent_name,
                "error": str(last_error),
            },
        )
        return LLMResponse(
            content=f"[{self._get_model(agent_name)}] API 调用失败: {last_error}",
            model=models_to_try[-1],
        )

    async def _complete_with_retry(
        self,
        model: str,
        messages: list[dict],
        agent_name: str | None = None,
        tools: list[str] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        tool_schemas = self.tool_registry.get_schemas(tools) if tools else None

        use_cache = not tools and not stream
        if use_cache:
            cached = self.cache.get(model, messages)
            if cached is not None:
                log_event(log, "cache_hit", model=model, agent=agent_name)
                return LLMResponse(content=cached, model=model)

        max_retries = self._get_retries()
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": self.config.llm.temperature,
                    "max_tokens": self.config.llm.max_tokens,
                    "timeout": self.config.llm.timeout,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                start = time.monotonic()
                response = await litellm.acompletion(**kwargs)
                elapsed = time.monotonic() - start

                content = ""
                tool_calls: list[dict] = []
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

                prompt_tokens = response.usage.prompt_tokens if response.usage else 0
                completion_tokens = (
                    response.usage.completion_tokens if response.usage else 0
                )
                actual_model = response.model or model

                self._track_usage(
                    agent_name, actual_model, prompt_tokens, completion_tokens,
                )

                log_event(
                    log, "llm_call",
                    model=actual_model, agent=agent_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    duration_ms=round(elapsed * 1000),
                    tool_calls=len(tool_calls),
                )

                if use_cache and content and not tool_calls:
                    self.cache.set(model, messages, content)

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    model=actual_model,
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                    cost=estimate_cost(
                        actual_model, prompt_tokens, completion_tokens,
                    ),
                )

            except Exception as e:
                last_error = e
                status = getattr(e, "status_code", 0) or getattr(
                    e, "http_status", 0,
                )
                is_retryable = status in _RETRYABLE_STATUSES or not status

                if is_retryable and attempt < max_retries:
                    delay = min(2 ** attempt * 1.0, 10.0)
                    log.warning(
                        "llm_retry",
                        extra={
                            "model": model,
                            "agent": agent_name,
                            "attempt": attempt + 1,
                            "delay": delay,
                            "error": str(e),
                        },
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        raise last_error  # type: ignore[misc]

    async def stream(
        self,
        messages: list[dict],
        agent_name: str | None = None,
    ) -> AsyncIterator[str]:
        self._check_budget()
        model = self._get_model(agent_name)

        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=self.config.llm.temperature,
                max_tokens=self.config.llm.max_tokens,
                timeout=self.config.llm.timeout,
                stream=True,
            )

            full_content = ""
            start = time.monotonic()
            async with asyncio.timeout(self.config.llm.timeout):
                async for chunk in response:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                        yield delta.content

            elapsed = time.monotonic() - start
            prompt_tokens = max(1, len(str(messages)) // 4)
            completion_tokens = max(1, len(full_content) // 4)
            self._track_usage(agent_name, model, prompt_tokens, completion_tokens)
            log_event(
                log, "llm_stream",
                model=model, agent=agent_name,
                duration_ms=round(elapsed * 1000),
                chars=len(full_content),
            )

        except asyncio.TimeoutError:
            yield f"\n[Stream timed out after {self.config.llm.timeout}s]"
        except Exception as e:
            yield f"\n[Stream error: {e}]"
