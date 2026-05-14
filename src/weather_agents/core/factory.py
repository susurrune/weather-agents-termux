"""System factory — unified Agent creation and task orchestration.

Avoids duplication between CLI and web entry points.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from weather_agents.agents.dew import DewAgent
from weather_agents.agents.fog import FogAgent
from weather_agents.agents.frost import FrostAgent
from weather_agents.agents.rain import RainAgent
from weather_agents.agents.snow import SnowAgent
from weather_agents.core.agent import BaseAgent
from weather_agents.core.agent import Task as AgentTask
from weather_agents.core.bus import MessageBus
from weather_agents.core.config import AppConfig, load_config
from weather_agents.core.llm import LLMClient
from weather_agents.core.skill import global_skill_registry
from weather_agents.core.tool import global_registry
from weather_agents.plugins.loader import PluginLoader
from weather_agents.skills.loader import register_all_skills
from weather_agents.tools.builtin import register_builtin_tools

AGENT_CLASSES = {
    "fog": FogAgent,
    "rain": RainAgent,
    "frost": FrostAgent,
    "snow": SnowAgent,
    "dew": DewAgent,
}

AGENT_EMOJI = {
    "fog": "🌫️",
    "rain": "🌧️",
    "frost": "❄️",
    "snow": "🌨️",
    "dew": "💧",
}


@dataclass
class SystemContext:
    """Wires together all shared services for an agent system instance."""

    config: AppConfig
    bus: MessageBus
    llm: LLMClient
    agent_map: dict[str, BaseAgent]

    async def init_all(self) -> None:
        for agent in self.agent_map.values():
            await agent.init()

    async def close_all(self) -> None:
        for agent in self.agent_map.values():
            await agent.close()
        from weather_agents.tools.builtin import close_http_client

        await close_http_client()


def create_system_context() -> SystemContext:
    """Bootstrap the full system: config, bus, LLM, tools, skills, plugins, agents."""
    config = load_config()
    bus = MessageBus()
    register_builtin_tools()
    register_all_skills()

    # Load plugins
    plugin_loader = PluginLoader(global_registry)
    plugin_dirs = config.plugins.directories if config.plugins.enabled else []
    plugin_loader.load_from_directories(plugin_dirs)

    llm = LLMClient(config, global_registry)
    agents = {
        name: cls(
            config=config,
            llm=llm,
            bus=bus,
            tool_registry=global_registry,
            skill_registry=global_skill_registry,
        )
        for name, cls in AGENT_CLASSES.items()
    }
    return SystemContext(config=config, bus=bus, llm=llm, agent_map=agents)


@dataclass
class TaskExecutionResult:
    """Result of executing a single sub-task in an orchestration."""

    id: str
    agent: str
    description: str
    success: bool
    content: str


async def orchestrate_task(
    goal: str,
    agent_map: dict[str, BaseAgent],
    snow: BaseAgent | None = None,
) -> tuple[list[Any], list[TaskExecutionResult], str]:
    """Orchestrate a multi-agent task: plan -> execute -> summarize.

    Respects dependency ordering: tasks with depends_on wait for their
    parent to complete before starting.
    """
    if snow is None:
        snow = agent_map.get("snow")
    if snow is None:
        return [], [], "Snow agent not available"

    tasks = await snow.orchestrate(goal)  # type: ignore[attr-defined]

    # Build dependency graph and execute in topological order
    completed: set[str] = set()
    results: list[TaskExecutionResult] = []
    pending = [t for t in tasks if t.assigned_to and t.assigned_to != "snow"]

    while pending:
        # Find tasks whose dependencies are satisfied
        ready = [t for t in pending if not t.parent_id or t.parent_id in completed]
        if not ready:
            ready = pending[:1]  # break deadlock

        # Execute ready tasks concurrently
        async def _execute_one(t):
            agent = agent_map.get(t.assigned_to)
            if not agent:
                return TaskExecutionResult(
                    id=t.id,
                    agent=t.assigned_to or "",
                    description=t.description,
                    success=False,
                    content=f"Agent '{t.assigned_to}' not found",
                )
            a_task = AgentTask(
                id=t.id,
                description=t.description,
                assigned_to=t.assigned_to,
                metadata=t.metadata,
            )
            result = await agent.execute_task(a_task)
            return TaskExecutionResult(
                id=t.id,
                agent=t.assigned_to or "",
                description=t.description,
                success=result.success,
                content=result.content[:500],
            )

        batch_results = await asyncio.gather(*[_execute_one(t) for t in ready])
        for r in batch_results:
            results.append(r)
            completed.add(r.id)
        for t in ready:
            pending.remove(t)

    # Generate summary
    if results:
        summary_prompt = "请汇总以下所有子任务的执行结果：\n\n"
        for r in results:
            status = "成功" if r.success else "失败"
            summary_prompt += f"## 任务 {r.id} ({r.agent}) - {status}\n{r.content[:300]}\n\n"
        summary = await snow.chat(summary_prompt)
    else:
        summary = "没有需要执行的任务。"

    return tasks, results, summary
